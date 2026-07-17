"""
Flask Blueprint for Quantix Tools Documentation PDF download.

Registers the /api/docs/tools-pdf endpoint that generates and serves
the Tools Reference Guide as a downloadable PDF file.

The PDF is generated on each request (no caching) to ensure it always
reflects the latest documentation content.
"""

import io

from flask import Blueprint, after_this_request, current_app, send_file

from blueprints.tools_docs.pdf_generator import generate_tools_pdf
from utils.logging import get_logger

logger = get_logger(__name__)

tools_docs_bp = Blueprint("tools_docs", __name__, url_prefix="/api/docs")


@tools_docs_bp.route("/tools-pdf", methods=["GET"])
def download_tools_pdf():
    """Generate and download the Quantix Tools Reference Guide PDF.

    Returns:
        A PDF file as a downloadable attachment with 1-hour cache control.
    """
    try:
        logger.info("Generating Quantix Tools Reference Guide PDF...")

        pdf_bytes = generate_tools_pdf()

        @after_this_request
        def set_cache_control(response):
            response.headers["Cache-Control"] = "public, max-age=3600"
            return response

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="quantix-tools-reference-guide.pdf",
        )
    except Exception as e:
        logger.exception("Failed to generate Tools Reference Guide PDF")
        return {"error": "Failed to generate PDF", "message": str(e)}, 500
