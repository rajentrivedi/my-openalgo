from flask import Blueprint, jsonify, request, session

from database.wishlist_db import add_item, get_user_items, remove_item
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

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
