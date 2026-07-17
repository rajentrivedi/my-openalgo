#!/usr/bin/env python
"""
Standalone CLI script to generate the Quantix Tools Reference Guide PDF.

Usage:
    uv run python scripts/generate_tools_pdf.py
    uv run python scripts/generate_tools_pdf.py --output custom-name.pdf

The generated PDF contains documentation for all 15 analytical tools
on the /tools route, with a cover page, table of contents, and
professionally styled sections.
"""

import argparse
import sys
import os


def main():
    parser = argparse.ArgumentParser(
        description="Generate the Quantix Tools Reference Guide PDF"
    )
    parser.add_argument(
        "--output",
        "-o",
        default="quantix-tools-reference-guide.pdf",
        help="Output PDF file path (default: quantix-tools-reference-guide.pdf)",
    )
    args = parser.parse_args()

    # Ensure project root is on sys.path so blueprint imports resolve
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from blueprints.tools_docs.pdf_generator import generate_tools_pdf

        print(f"Generating Quantix Tools Reference Guide PDF...")
        generate_tools_pdf(output_path=args.output)
        print(f"PDF generated: {args.output}")

    except ImportError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(
            "\nMake sure dependencies are installed. Run: uv sync",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"Error generating PDF: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
