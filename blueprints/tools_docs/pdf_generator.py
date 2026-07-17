"""
PDF Generation Engine for Quantix Tools Reference Guide.

Uses WeasyPrint to convert HTML + CSS into a professionally styled PDF
document containing documentation for all 15 analytical tools on the /tools route.

Features:
- Cover page with title, subtitle, generation date, and platform version
- Table of contents with dotted leaders and page numbers
- Per-tool sections with colour-coded accent bars, feature checklists,
  numbered usage instructions, API endpoint tables, and practical examples
- Professional typography with serif/sans-serif system fonts
- Page headers (section name) and footers (page numbers)
"""

import os
from datetime import date

from weasyprint import CSS, HTML

from blueprints.tools_docs.docs_data import TOOLS_DOCS, TOOL_NAMES


PDF_CSS = """
@page {
  size: A4;
  margin: 2cm;
  @top-center {
    content: "Quantix Tools Reference Guide";
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    font-size: 9pt;
    color: #888;
  }
  @bottom-center {
    content: "Page " counter(page) " of " counter(pages);
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    font-size: 9pt;
    color: #888;
  }
}

@page :first {
  margin: 0;
  @top-center { content: none; }
  @bottom-center { content: none; }
}

@page toc {
  @top-center { content: "Table of Contents"; }
}

body {
  font-family: 'Georgia', 'Times New Roman', Times, serif;
  font-size: 11pt;
  line-height: 1.6;
  color: #222;
  margin: 0;
  padding: 0;
}

/* ===== Cover Page ===== */
.cover-page {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  height: 100vh;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
  color: #fff;
  text-align: center;
  page-break-after: always;
}

.cover-page .cover-title {
  font-size: 36pt;
  font-weight: bold;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  letter-spacing: 2px;
  margin-bottom: 12pt;
}

.cover-page .cover-subtitle {
  font-size: 16pt;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-weight: 300;
  color: #a0c4ff;
  margin-bottom: 48pt;
}

.cover-page .cover-meta {
  font-size: 10pt;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  color: #8899bb;
  line-height: 1.8;
}

.cover-page .cover-line {
  width: 120px;
  height: 3px;
  background: #4da6ff;
  margin: 24pt auto;
}

/* ===== Table of Contents ===== */
.toc-page {
  page: toc;
  page-break-after: always;
}

.toc-title {
  font-size: 22pt;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-weight: bold;
  color: #1a1a2e;
  border-bottom: 2px solid #1a1a2e;
  padding-bottom: 8pt;
  margin-bottom: 20pt;
}

.toc-entry {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 4pt 0;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 11pt;
  color: #333;
  text-decoration: none;
}

.toc-entry:hover {
  color: #0f3460;
}

.toc-entry .toc-dots {
  flex: 1;
  border-bottom: 1px dotted #aaa;
  margin: 0 6pt;
  min-width: 20px;
}

.toc-entry .toc-num {
  font-weight: bold;
  color: #0f3460;
  margin-right: 8pt;
  min-width: 24pt;
}

.toc-entry .toc-page-num {
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  color: #666;
  min-width: 30pt;
  text-align: right;
}

/* ===== Tool Sections ===== */
.tool-section {
  page-break-before: always;
  padding-top: 12pt;
}

.tool-header-bar {
  height: 6px;
  border-radius: 3px;
  margin-bottom: 16pt;
}

.tool-title {
  font-size: 20pt;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-weight: bold;
  color: #1a1a2e;
  margin-bottom: 4pt;
}

.tool-route {
  font-family: 'Courier New', Courier, monospace;
  font-size: 10pt;
  color: #888;
  margin-bottom: 16pt;
}

.section-heading {
  font-size: 13pt;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-weight: bold;
  color: #1a1a2e;
  border-bottom: 1px solid #ddd;
  padding-bottom: 4pt;
  margin-top: 18pt;
  margin-bottom: 10pt;
}

.overview-text {
  font-size: 11pt;
  line-height: 1.65;
  margin-bottom: 10pt;
  text-align: justify;
}

/* ===== Feature Checklist ===== */
.feature-list {
  list-style: none;
  padding-left: 0;
  margin: 8pt 0 12pt 0;
}

.feature-list li {
  padding: 3pt 0 3pt 24pt;
  position: relative;
  font-size: 11pt;
  line-height: 1.5;
}

.feature-list li::before {
  content: "✓";
  position: absolute;
  left: 2pt;
  color: #16a34a;
  font-weight: bold;
  font-size: 12pt;
}

/* ===== Numbered Steps ===== */
.steps-list {
  padding-left: 24pt;
  margin: 8pt 0 12pt 0;
}

.steps-list li {
  padding: 3pt 0;
  font-size: 11pt;
  line-height: 1.5;
}

/* ===== API Endpoint Table ===== */
.api-table {
  width: 100%;
  border-collapse: collapse;
  margin: 10pt 0 12pt 0;
  font-size: 10pt;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
}

.api-table th {
  background: #1a1a2e;
  color: #fff;
  padding: 6pt 10pt;
  text-align: left;
  font-weight: bold;
}

.api-table td {
  padding: 5pt 10pt;
  border-bottom: 1px solid #e0e0e0;
  vertical-align: top;
}

.api-table tr:nth-child(even) td {
  background: #f5f7fa;
}

.api-table td:first-child {
  font-family: 'Courier New', Courier, monospace;
  font-size: 9pt;
  color: #0f3460;
  white-space: nowrap;
}

.api-table td:nth-child(2) {
  font-family: 'Courier New', Courier, monospace;
  font-size: 9pt;
  font-weight: bold;
  color: #555;
  text-align: center;
  width: 60pt;
}

/* ===== Code Block ===== */
.code-block {
  background: #f4f4f4;
  border: 1px solid #ddd;
  border-radius: 4px;
  padding: 10pt 12pt;
  font-family: 'Courier New', Courier, monospace;
  font-size: 9pt;
  line-height: 1.5;
  margin: 10pt 0 12pt 0;
  white-space: pre-wrap;
  color: #333;
  border-left: 3px solid #888;
}

/* ===== Example Scenario ===== */
.example-box {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  padding: 12pt 14pt;
  margin: 10pt 0 12pt 0;
  border-left: 4px solid #0f3460;
}

.example-box .example-label {
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-weight: bold;
  font-size: 10pt;
  color: #0f3460;
  margin-bottom: 4pt;
}

.example-box .example-text {
  font-size: 10.5pt;
  line-height: 1.6;
  font-style: italic;
  color: #444;
}

/* ===== Configuration ===== */
.config-box {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 4px;
  padding: 10pt 12pt;
  margin: 10pt 0 12pt 0;
  font-size: 10.5pt;
  line-height: 1.5;
  border-left: 4px solid #f59e0b;
}

/* ===== Related Tools ===== */
.related-tools {
  margin: 8pt 0 4pt 0;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 10.5pt;
}

.related-tools a {
  color: #0f3460;
  text-decoration: none;
}

.related-tools a:hover {
  text-decoration: underline;
}

/* ===== Footer Section ===== */
.footer-note {
  margin-top: 24pt;
  padding-top: 12pt;
  border-top: 1px solid #ddd;
  font-size: 9pt;
  color: #999;
  text-align: center;
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
}
"""


def _escape_html(text: str) -> str:
    """Escape minimal HTML entities for safe embedding in HTML content."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_cover_html() -> str:
    """Generate the cover page HTML."""
    today = date.today().strftime("%B %d, %Y")
    version = os.environ.get("QUANTIX_VERSION", "2.0.1.5")
    return f"""
    <div class="cover-page">
      <div class="cover-title">Quantix Tools Reference Guide</div>
      <div class="cover-subtitle">Options Trading &amp; Market Analysis Tools</div>
      <div class="cover-line"></div>
      <div class="cover-meta">
        Version {_escape_html(version)}<br>
        Generated on {_escape_html(today)}<br>
        {len(TOOLS_DOCS)} Analytical Tools
      </div>
    </div>
    """


def _build_toc_html() -> str:
    """Generate the table of contents HTML."""
    entries = []
    for idx, tool in enumerate(TOOLS_DOCS, 1):
        entries.append(
            f'<div class="toc-entry">'
            f'<span class="toc-num">{idx}.</span>'
            f'<span>{_escape_html(tool.title)}</span>'
            f'<span class="toc-dots"></span>'
            f'<span class="toc-page-num"></span>'
            f'</div>'
        )
    return f"""
    <div class="toc-page">
      <div class="toc-title">Table of Contents</div>
      {''.join(entries)}
    </div>
    """


def _build_tool_section_html(tool) -> str:
    """Generate the HTML for a single tool documentation section."""
    color = tool.color_hex

    parts = [
        '<div class="tool-section">',
        f'<div class="tool-header-bar" style="background:{color};"></div>',
        f'<div class="tool-title">{_escape_html(tool.title)}</div>',
        f'<div class="tool-route">Route: {_escape_html(tool.route)}</div>',
    ]

    # Overview
    parts.append('<div class="section-heading">Overview</div>')
    for paragraph in tool.overview.strip().split("\n\n"):
        parts.append(f'<div class="overview-text">{_escape_html(paragraph.strip())}</div>')

    # Key Features
    parts.append('<div class="section-heading">Key Features</div>')
    parts.append('<ul class="feature-list">')
    for feature in tool.key_features:
        parts.append(f'<li>{_escape_html(feature)}</li>')
    parts.append('</ul>')

    # How to Use
    parts.append('<div class="section-heading">How to Use</div>')
    parts.append('<ol class="steps-list">')
    for step in tool.how_to_use:
        parts.append(f'<li>{_escape_html(step)}</li>')
    parts.append('</ol>')

    # API Endpoints
    parts.append('<div class="section-heading">API Endpoints</div>')
    parts.append('<table class="api-table">')
    parts.append('<tr><th>Endpoint</th><th>Method</th><th>Purpose</th></tr>')
    for ep in tool.api_endpoints:
        parts.append(
            f'<tr><td>{_escape_html(ep["path"])}</td>'
            f'<td>{_escape_html(ep["method"])}</td>'
            f'<td>{_escape_html(ep["purpose"])}</td></tr>'
        )
    parts.append('</table>')

    # Example Scenario
    parts.append('<div class="section-heading">Practical Example</div>')
    parts.append(
        '<div class="example-box">'
        '<div class="example-label">Example Scenario</div>'
        f'<div class="example-text">{_escape_html(tool.example_scenario)}</div>'
        '</div>'
    )

    # Configuration
    if tool.configuration:
        parts.append('<div class="section-heading">Configuration</div>')
        parts.append(f'<div class="config-box">{_escape_html(tool.configuration)}</div>')

    # Related Tools
    if tool.related_tools:
        related = ", ".join(tool.related_tools)
        parts.append(f'<div class="section-heading">Related Tools</div>')
        parts.append(f'<div class="related-tools">{_escape_html(related)}</div>')

    parts.append("</div>")
    return "\n".join(parts)


def _build_full_html() -> str:
    """Build the complete HTML document combining all sections."""
    tool_sections = "\n".join(_build_tool_section_html(t) for t in TOOLS_DOCS)

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>Quantix Tools Reference Guide</title>
    </head>
    <body>
      {_build_cover_html()}
      {_build_toc_html()}
      {tool_sections}
      <div class="footer-note">
        Quantix Tools Reference Guide — Generated on {date.today().strftime("%B %d, %Y")}
      </div>
    </body>
    </html>
    """


def generate_tools_pdf(output_path: str | None = None) -> bytes:
    """Generate the Quantix Tools Reference Guide PDF.

    Args:
        output_path: If provided, write the PDF to this file path. If None,
                     the PDF bytes are returned without writing to disk.

    Returns:
        The PDF content as bytes.

    Raises:
        RuntimeError: If WeasyPrint encounters an error during generation.
    """
    html_str = _build_full_html()
    html = HTML(string=html_str)

    pdf_bytes = html.write_pdf()

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)

    return pdf_bytes
