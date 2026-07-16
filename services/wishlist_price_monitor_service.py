import logging
import threading
import time
from datetime import datetime
from typing import Any, Optional

from database.auth_db import get_api_key_for_tradingview, get_auth_token_broker
from database.telegram_db import get_telegram_user_by_username
from database.whatsapp_db import get_whatsapp_user_by_username
from database.wishlist_alert_db import (
    count_active_alerts,
    get_active_alerts,
    update_alert_checked,
    update_alert_triggered,
)
from services.quotes_service import get_quotes_with_auth
from services.telegram_alert_service import telegram_alert_service
from services.whatsapp_alert_service import whatsapp_alert_service

logger = logging.getLogger(__name__)

CONDITION_TARGET_ABOVE = "target_above"
CONDITION_TARGET_BELOW = "target_below"
CONDITION_PERCENT_UP = "percent_up"
CONDITION_PERCENT_DOWN = "percent_down"


class WishlistPriceMonitor:
    _instance: Optional["WishlistPriceMonitor"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._running = False
        self._monitor_thread: threading.Thread | None = None
        self._poll_interval = 30
        self._stop_event = threading.Event()
        logger.info("WishlistPriceMonitor initialized")

    def start(self):
        if self._running:
            return

        active_count = count_active_alerts()
        if active_count == 0:
            logger.debug("No active alerts to monitor")
            return

        self._stop_event.clear()
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self._monitor_thread.start()
        logger.info(f"Wishlist price monitoring started with {active_count} active alerts")

    def stop(self):
        if not self._running:
            return

        self._stop_event.set()
        self._running = False

        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
            self._monitor_thread = None

        logger.info("Wishlist price monitoring stopped")

    def _monitoring_loop(self):
        while not self._stop_event.is_set():
            try:
                self._check_all_alerts()
            except Exception as e:
                logger.exception(f"Error in wishlist monitoring loop: {e}")

            self._stop_event.wait(timeout=self._poll_interval)

        logger.info("Wishlist monitoring loop ended")

    def _check_all_alerts(self):
        alerts = get_active_alerts()
        if not alerts:
            if self._running:
                logger.debug("No active alerts, stopping monitor")
                self.stop()
            return

        user_cache: dict[str, str | None] = {}

        for alert in alerts:
            try:
                api_key = user_cache.get(alert.user_id)
                if api_key is None:
                    api_key = get_api_key_for_tradingview(alert.user_id)
                    user_cache[alert.user_id] = api_key

                if not api_key:
                    logger.debug(f"No API key for user {alert.user_id}, skipping alert {alert.id}")
                    continue

                auth_token, feed_token, broker = get_auth_token_broker(api_key, include_feed_token=True)
                if not auth_token:
                    logger.debug(f"No auth token for user {alert.user_id}, skipping alert {alert.id}")
                    continue

                success, result, status_code = get_quotes_with_auth(
                    auth_token, feed_token, broker, alert.symbol, alert.exchange
                )

                if not success:
                    logger.debug(f"Failed to get quote for {alert.symbol}: {result.get('message')}")
                    continue

                data = result.get("data", {})
                current_price = float(data.get("ltp", 0))

                if current_price <= 0:
                    continue

                self._check_alert(alert, current_price)

            except Exception as e:
                logger.exception(f"Error checking wishlist alert {alert.id}: {e}")

    def _check_alert(self, alert, current_price: float):
        condition_met = self._evaluate_condition(alert, current_price)

        update_alert_checked(alert.id, current_price)

        if condition_met and not alert.triggered:
            logger.info(
                f"Wishlist alert triggered: {alert.symbol}@{alert.exchange} "
                f"({alert.condition_type} at {alert.condition_value}, "
                f"current: {current_price})"
            )

            self._send_alert_notification(alert, current_price)
            update_alert_triggered(alert.id, current_price)

    def _evaluate_condition(self, alert, current_price: float) -> bool:
        condition = alert.condition_type
        target = alert.condition_value
        reference = alert.reference_price

        if condition == CONDITION_TARGET_ABOVE:
            return current_price > target

        elif condition == CONDITION_TARGET_BELOW:
            return current_price < target

        elif condition == CONDITION_PERCENT_UP:
            if not reference or reference == 0:
                return False
            pct_change = ((current_price - reference) / reference) * 100
            return pct_change >= target

        elif condition == CONDITION_PERCENT_DOWN:
            if not reference or reference == 0:
                return False
            pct_change = ((reference - current_price) / reference) * 100
            return pct_change >= target

        return False

    def _send_alert_notification(self, alert, current_price: float):
        condition_labels = {
            CONDITION_TARGET_ABOVE: "Price Above",
            CONDITION_TARGET_BELOW: "Price Below",
            CONDITION_PERCENT_UP: "% Up",
            CONDITION_PERCENT_DOWN: "% Down",
        }

        condition_label = condition_labels.get(alert.condition_type, alert.condition_type)
        reference_text = ""
        if alert.reference_price:
            reference_text = f"\nReference Price: ₹{alert.reference_price:.2f}"

        message = (
            f"🚨 Wishlist Alert\n"
            f"Symbol: {alert.symbol} ({alert.exchange})\n"
            f"Condition: {condition_label} at {alert.condition_value}\n"
            f"Current Price: ₹{current_price:.2f}{reference_text}"
        )

        thread = threading.Thread(
            target=self._dispatch_notifications,
            args=(alert, message),
            daemon=True,
        )
        thread.start()

    def _dispatch_notifications(self, alert, message: str):
        telegram_sent = False
        whatsapp_sent = False

        try:
            telegram_user = get_telegram_user_by_username(alert.user_id)
            if telegram_user and telegram_user.get("telegram_id"):
                tg_id = telegram_user["telegram_id"]
                telegram_alert_service.send_alert_sync(tg_id, message)
                telegram_sent = True
                logger.info(f"Telegram wishlist alert sent to {alert.user_id}")
        except Exception as e:
            logger.exception(f"Failed to send Telegram wishlist alert: {e}")

        try:
            whatsapp_user = get_whatsapp_user_by_username(alert.user_id)
            if whatsapp_user and whatsapp_user.get("whatsapp_jid"):
                jid = whatsapp_user["whatsapp_jid"]
                whatsapp_alert_service.send_alert_sync(jid, message)
                whatsapp_sent = True
                logger.info(f"WhatsApp wishlist alert sent to {alert.user_id}")
        except Exception as e:
            logger.exception(f"Failed to send WhatsApp wishlist alert: {e}")

        if not telegram_sent and not whatsapp_sent:
            logger.warning(
                f"No notification channel available for {alert.user_id} "
                f"(alert {alert.id} triggered but no Telegram/WhatsApp configured)"
            )

    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "alerts_count": count_active_alerts(),
            "poll_interval": self._poll_interval,
        }

    def shutdown(self):
        self.stop()


wishlist_price_monitor = WishlistPriceMonitor()


def get_wishlist_price_monitor() -> WishlistPriceMonitor:
    return wishlist_price_monitor
