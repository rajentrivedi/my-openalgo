import logging
import os

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

# Conditionally create engine based on DB type
if DATABASE_URL and "sqlite" in DATABASE_URL:
    # SQLite: Use NullPool to prevent connection pool exhaustion
    engine = create_engine(
        DATABASE_URL, poolclass=NullPool, connect_args={"check_same_thread": False}
    )
else:
    # For other databases like PostgreSQL, use connection pooling
    engine = create_engine(DATABASE_URL, pool_size=50, max_overflow=100, pool_timeout=10)

db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()


class WishlistItem(Base):
    """Model for wishlist items (saved symbols to track/trade later)"""

    __tablename__ = "wishlist_items"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), nullable=False, default="NSE")
    notes = Column(String(500), nullable=True)
    target_price = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_wishlist_user_symbol", "user_id", "symbol", unique=True),)


def init_db():
    """Initialize the database"""
    from database.db_init_helper import init_db_with_logging

    init_db_with_logging(Base, engine, "Wishlist DB", logger)


def add_item(user_id, symbol, exchange, notes=None, target_price=None):
    """Add a new wishlist item

    Args:
        user_id: User identifier
        symbol: Trading symbol
        exchange: Exchange (NSE, BSE, etc.)
        notes: Optional notes about the item
        target_price: Optional target price

    Returns:
        WishlistItem on success, None on failure
    """
    try:
        item = WishlistItem(
            user_id=user_id,
            symbol=symbol,
            exchange=exchange,
            notes=notes,
            target_price=target_price,
        )
        db_session.add(item)
        db_session.commit()
        return item
    except Exception as e:
        logger.exception(f"Error adding wishlist item: {str(e)}")
        db_session.rollback()
        return None


def get_user_items(user_id):
    """Get all wishlist items for a user, ordered by created_at descending

    Args:
        user_id: User identifier

    Returns:
        List of WishlistItem objects (empty list if none or on error)
    """
    try:
        return (
            WishlistItem.query.filter_by(user_id=user_id)
            .order_by(WishlistItem.created_at.desc())
            .all()
        )
    except Exception as e:
        logger.exception(f"Error getting wishlist items for {user_id}: {str(e)}")
        return []


def remove_item(item_id, user_id):
    """Remove a wishlist item by id, scoped to user_id (security — prevents deleting another user's item)

    Args:
        item_id: Item ID
        user_id: User identifier (must match the item's owner)

    Returns:
        True if deleted, False if not found or on error
    """
    try:
        item = WishlistItem.query.filter_by(id=item_id, user_id=user_id).first()
        if item:
            db_session.delete(item)
            db_session.commit()
            return True
        return False
    except Exception as e:
        logger.exception(f"Error removing wishlist item {item_id}: {str(e)}")
        db_session.rollback()
        return False
