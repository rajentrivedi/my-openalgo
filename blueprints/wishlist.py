from flask import Blueprint, jsonify, request, session

from database.wishlist_alert_db import (
    create_alert as db_create_alert,
    count_active_alerts,
    delete_alert as db_delete_alert,
    delete_alerts_for_wishlist_item,
    get_alert_by_id,
    get_alerts_for_wishlist_item,
    get_user_alerts,
    toggle_alert as db_toggle_alert,
)
from database.wishlist_db import add_item, get_user_items, remove_item
from services.quotes_service import get_quotes_with_auth
from services.wishlist_price_monitor_service import wishlist_price_monitor
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

VALID_CONDITIONS = {"target_above", "target_below", "percent_up", "percent_down"}

_alert_fields = [
    "id",
    "wishlist_item_id",
    "user_id",
    "symbol",
    "exchange",
    "condition_type",
    "condition_value",
    "reference_price",
    "is_active",
    "triggered",
    "triggered_at",
    "last_checked_price",
    "last_checked_at",
    "created_at",
]


def _alert_to_dict(alert):
    return {
        "id": alert.id,
        "wishlist_item_id": alert.wishlist_item_id,
        "user_id": alert.user_id,
        "symbol": alert.symbol,
        "exchange": alert.exchange,
        "condition_type": alert.condition_type,
        "condition_value": alert.condition_value,
        "reference_price": alert.reference_price,
        "is_active": alert.is_active,
        "triggered": alert.triggered,
        "triggered_at": alert.triggered_at.isoformat() if alert.triggered_at else None,
        "last_checked_price": alert.last_checked_price,
        "last_checked_at": alert.last_checked_at.isoformat() if alert.last_checked_at else None,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }

wishlist_bp = Blueprint("wishlist_bp", __name__, url_prefix="/wishlist")


@wishlist_bp.route("/api/items", methods=["GET"])
@check_session_validity
def list_items():
    """Get all wishlist items for the current user"""
    try:
        username = session.get("user")
        items = get_user_items(username)
        return jsonify(
            [
                {
                    "id": item.id,
                    "symbol": item.symbol,
                    "exchange": item.exchange,
                    "notes": item.notes,
                    "target_price": item.target_price,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in items
            ]
        )
    except Exception as e:
        logger.exception(f"Error listing wishlist items: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


@wishlist_bp.route("/api/items", methods=["POST"])
@check_session_validity
def add_wishlist_item():
    """Add a new wishlist item"""
    try:
        username = session.get("user")
        data = request.get_json()

        if not data or not data.get("symbol") or not data.get("exchange"):
            return jsonify({"error": "Symbol and exchange required"}), 400

        item = add_item(
            username,
            data["symbol"],
            data["exchange"],
            data.get("notes"),
            data.get("target_price"),
        )

        if item is None:
            return jsonify({"error": "Failed to add wishlist item"}), 500

        return jsonify({"status": "success", "id": item.id}), 201
    except Exception as e:
        logger.exception(f"Error adding wishlist item: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


@wishlist_bp.route("/api/items/<int:item_id>", methods=["DELETE"])
@check_session_validity
def delete_wishlist_item(item_id):
    """Delete a wishlist item (scoped to current user)"""
    try:
        username = session.get("user")
        removed = remove_item(item_id, username)

        if removed:
            return jsonify({"status": "success", "message": "Item removed"})
        else:
            return jsonify({"error": "Item not found"}), 404
    except Exception as e:
        logger.exception(f"Error deleting wishlist item {item_id}: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


@wishlist_bp.route("/api/alerts", methods=["POST"])
@check_session_validity
def create_alert():
    """Create a new price alert for a wishlist item"""
    try:
        username = session.get("user")
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        item_id = data.get("wishlist_item_id")
        condition_type = data.get("condition_type")
        condition_value = data.get("condition_value")

        if not item_id or not condition_type or condition_value is None:
            return jsonify({"error": "wishlist_item_id, condition_type, and condition_value required"}), 400

        if condition_type not in VALID_CONDITIONS:
            return jsonify({"error": f"Invalid condition_type. Must be one of: {', '.join(sorted(VALID_CONDITIONS))}"}), 400

        if condition_value <= 0:
            return jsonify({"error": "condition_value must be positive"}), 400

        from database.wishlist_db import WishlistItem

        item = WishlistItem.query.filter_by(id=item_id, user_id=username).first()
        if not item:
            return jsonify({"error": "Wishlist item not found"}), 404

        reference_price = None
        if condition_type in ("percent_up", "percent_down"):
            try:
                from database.auth_db import get_api_key_for_tradingview, get_auth_token_broker

                api_key = get_api_key_for_tradingview(username)
                if api_key:
                    auth_token, feed_token, broker = get_auth_token_broker(api_key, include_feed_token=True)
                    if auth_token:
                        success, result, _ = get_quotes_with_auth(
                            auth_token, feed_token, broker, item.symbol, item.exchange
                        )
                        if success:
                            data = result.get("data", {})
                            ltp = float(data.get("ltp", 0))
                            if ltp > 0:
                                reference_price = ltp
            except Exception:
                logger.debug("Failed to fetch reference price for alert", exc_info=True)

        alert = db_create_alert(
            user_id=username,
            wishlist_item_id=item_id,
            symbol=item.symbol,
            exchange=item.exchange,
            condition_type=condition_type,
            condition_value=condition_value,
            reference_price=reference_price,
        )

        if alert is None:
            return jsonify({"error": "Failed to create alert"}), 500

        wishlist_price_monitor.start()

        return jsonify({"status": "success", "alert": _alert_to_dict(alert)}), 201

    except Exception as e:
        logger.exception(f"Error creating wishlist alert: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


@wishlist_bp.route("/api/alerts", methods=["GET"])
@check_session_validity
def list_alerts():
    """List all alerts for the current user"""
    try:
        username = session.get("user")
        alerts = get_user_alerts(username)
        return jsonify([_alert_to_dict(a) for a in alerts])
    except Exception as e:
        logger.exception(f"Error listing wishlist alerts: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


@wishlist_bp.route("/api/alerts/wishlist/<int:item_id>", methods=["GET"])
@check_session_validity
def list_alerts_for_item(item_id):
    """List alerts for a specific wishlist item"""
    try:
        username = session.get("user")
        alerts = get_alerts_for_wishlist_item(item_id, username)
        return jsonify([_alert_to_dict(a) for a in alerts])
    except Exception as e:
        logger.exception(f"Error listing alerts for wishlist item {item_id}: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


@wishlist_bp.route("/api/alerts/<int:alert_id>/toggle", methods=["PATCH"])
@check_session_validity
def toggle_alert(alert_id):
    """Toggle alert active state"""
    try:
        username = session.get("user")
        is_active = db_toggle_alert(alert_id, username)

        if is_active is None:
            return jsonify({"error": "Alert not found"}), 404

        active_count = count_active_alerts()
        if active_count == 0:
            wishlist_price_monitor.stop()

        return jsonify({"status": "success", "is_active": is_active})
    except Exception as e:
        logger.exception(f"Error toggling alert {alert_id}: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


@wishlist_bp.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
@check_session_validity
def delete_alert_route(alert_id):
    """Delete a wishlist alert"""
    try:
        username = session.get("user")
        deleted = db_delete_alert(alert_id, username)

        if not deleted:
            return jsonify({"error": "Alert not found"}), 404

        active_count = count_active_alerts()
        if active_count == 0:
            wishlist_price_monitor.stop()

        return jsonify({"status": "success", "message": "Alert deleted"})
    except Exception as e:
        logger.exception(f"Error deleting alert {alert_id}: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


@wishlist_bp.route("/api/alerts/wishlist/<int:item_id>", methods=["DELETE"])
@check_session_validity
def delete_alerts_for_item(item_id):
    """Delete all alerts for a wishlist item"""
    try:
        username = session.get("user")
        count = delete_alerts_for_wishlist_item(item_id, username)

        active_count = count_active_alerts()
        if active_count == 0:
            wishlist_price_monitor.stop()

        return jsonify({"status": "success", "deleted_count": count})
    except Exception as e:
        logger.exception(f"Error deleting alerts for wishlist item {item_id}: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500
