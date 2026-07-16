import logging
import os

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL, poolclass=NullPool, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(DATABASE_URL, pool_size=50, max_overflow=100, pool_timeout=10)

db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()


class WishlistAlert(Base):
    __tablename__ = "wishlist_alerts"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    wishlist_item_id = Column(Integer, ForeignKey("wishlist_items.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), nullable=False, default="NSE")
    condition_type = Column(String(20), nullable=False)
    condition_value = Column(Float, nullable=False)
    reference_price = Column(Float, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    triggered = Column(Boolean, nullable=False, default=False)
    triggered_at = Column(DateTime(timezone=True), nullable=True)
    last_checked_price = Column(Float, nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "condition_type IN ('target_above', 'target_below', 'percent_up', 'percent_down')",
            name="ck_wishlist_alert_condition_type",
        ),
        Index("idx_wishlist_alert_user_id", "user_id"),
        Index("idx_wishlist_alert_active", "is_active", "triggered"),
    )


def init_db():
    from database.db_init_helper import init_db_with_logging

    init_db_with_logging(Base, engine, "Wishlist Alert DB", logger)


def create_alert(user_id, wishlist_item_id, symbol, exchange, condition_type, condition_value, reference_price=None):
    try:
        alert = WishlistAlert(
            user_id=user_id,
            wishlist_item_id=wishlist_item_id,
            symbol=symbol,
            exchange=exchange,
            condition_type=condition_type,
            condition_value=condition_value,
            reference_price=reference_price,
        )
        db_session.add(alert)
        db_session.commit()
        return alert
    except Exception as e:
        logger.exception(f"Error creating wishlist alert: {str(e)}")
        db_session.rollback()
        return None


def get_user_alerts(user_id):
    try:
        return (
            WishlistAlert.query.filter_by(user_id=user_id)
            .order_by(WishlistAlert.created_at.desc())
            .all()
        )
    except Exception as e:
        logger.exception(f"Error getting alerts for user {user_id}: {str(e)}")
        return []


def get_active_alerts():
    try:
        return (
            WishlistAlert.query.filter_by(is_active=True, triggered=False)
            .all()
        )
    except Exception as e:
        logger.exception(f"Error getting active alerts: {str(e)}")
        return []


def get_alerts_for_wishlist_item(item_id, user_id):
    try:
        return (
            WishlistAlert.query.filter_by(wishlist_item_id=item_id, user_id=user_id)
            .order_by(WishlistAlert.created_at.desc())
            .all()
        )
    except Exception as e:
        logger.exception(f"Error getting alerts for wishlist item {item_id}: {str(e)}")
        return []


def get_alert_by_id(alert_id, user_id):
    try:
        return WishlistAlert.query.filter_by(id=alert_id, user_id=user_id).first()
    except Exception as e:
        logger.exception(f"Error getting alert {alert_id}: {str(e)}")
        return None


def update_alert_triggered(alert_id, price):
    try:
        alert = WishlistAlert.query.filter_by(id=alert_id).first()
        if alert:
            alert.triggered = True
            alert.triggered_at = func.now()
            alert.last_checked_price = price
            alert.last_checked_at = func.now()
            db_session.commit()
            return True
        return False
    except Exception as e:
        logger.exception(f"Error updating alert {alert_id} triggered state: {str(e)}")
        db_session.rollback()
        return False


def update_alert_checked(alert_id, price):
    try:
        alert = WishlistAlert.query.filter_by(id=alert_id).first()
        if alert:
            alert.last_checked_price = price
            alert.last_checked_at = func.now()
            db_session.commit()
            return True
        return False
    except Exception as e:
        logger.exception(f"Error updating alert {alert_id} checked state: {str(e)}")
        db_session.rollback()
        return False


def toggle_alert(alert_id, user_id):
    try:
        alert = WishlistAlert.query.filter_by(id=alert_id, user_id=user_id).first()
        if alert:
            alert.is_active = not alert.is_active
            db_session.commit()
            return alert.is_active
        return None
    except Exception as e:
        logger.exception(f"Error toggling alert {alert_id}: {str(e)}")
        db_session.rollback()
        return None


def delete_alert(alert_id, user_id):
    try:
        alert = WishlistAlert.query.filter_by(id=alert_id, user_id=user_id).first()
        if alert:
            db_session.delete(alert)
            db_session.commit()
            return True
        return False
    except Exception as e:
        logger.exception(f"Error deleting alert {alert_id}: {str(e)}")
        db_session.rollback()
        return False


def delete_alerts_for_wishlist_item(item_id, user_id):
    try:
        alerts = WishlistAlert.query.filter_by(wishlist_item_id=item_id, user_id=user_id).all()
        count = len(alerts)
        for alert in alerts:
            db_session.delete(alert)
        db_session.commit()
        return count
    except Exception as e:
        logger.exception(f"Error deleting alerts for wishlist item {item_id}: {str(e)}")
        db_session.rollback()
        return 0


def count_active_alerts():
    try:
        return WishlistAlert.query.filter_by(is_active=True, triggered=False).count()
    except Exception as e:
        logger.exception(f"Error counting active alerts: {str(e)}")
        return 0
