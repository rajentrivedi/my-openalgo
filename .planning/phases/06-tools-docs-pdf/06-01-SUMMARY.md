---
phase: 06-tools-docs-pdf
plan: 01
subsystem: api
tags: [weasyprint, pdf-generation, documentation, flask]

requires:
  - phase: 05-rebrand
    provides: "Quantix brand name used in PDF cover and headers"
provides:
  - "Structured documentation data for all 15 analytical tools"
  - "PDF generation engine using WeasyPrint HTML->CSS->PDF"
  - "Flask blueprint at /api/docs/tools-pdf for PDF download"
  - "Standalone CLI script for offline PDF generation"

affects:
  - 06-tools-docs-pdf-02 (frontend download button)

tech-stack:
  added:
    - weasyprint >=64.0
    - CSS selectors, tinycss2, pydyf (transitive WeasyPrint deps)
  patterns:
    - "Blueprint package pattern: blueprints/tools_docs/ package with __init__.py, docs_data.py, pdf_generator.py"
    - "ToolDoc dataclass for structured documentation content"

key-files:
  created:
    - blueprints/tools_docs/docs_data.py
    - blueprints/tools_docs/pdf_generator.py
    - blueprints/tools_docs/__init__.py
    - scripts/generate_tools_pdf.py
  modified:
    - pyproject.toml

key-decisions:
  - "WeasyPrint 69.0 for HTML->CSS->PDF generation (professional styling with CSS @page rules)"
  - "PDF generated on each request with 1-hour Cache-Control (no file caching)"
  - "PDF filename: quantix-tools-reference-guide.pdf"
  - "ToolDoc dataclass in docs_data.py enables programmatic access to documentation content"
  - "Per-tool colour coding matches Tools.tsx hex colours for consistent branding"

patterns-established:
  - "Blueprint package pattern: blueprints/<name>/__init__.py + supporting modules"
  - "Standalone script pattern: scripts/<name>.py with sys.path manipulation"

requirements-completed: [DOC-01, DOC-02, DOC-03]

duration: 16min
completed: 2026-07-17
---

# Phase 06: Tools Documentation PDF — Backend Summary

**WeasyPrint-based PDF generation engine with structured documentation for all 15 tools, Flask download endpoint, and standalone CLI script**

## Performance

- **Duration:** 16 min
- **Started:** 2026-07-17T09:41:00Z
- **Completed:** 2026-07-17T09:57:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Created ToolDoc dataclass with documentation for all 15 tools (Strategy Builder, Strategy Portfolio, Option Chain, IV Chart, OI Tracker, OI Range, Max Pain, Straddle Chart, Straddle PnL, Vol Surface, GEX Dashboard, Gamma Density, IV Smile, OI Profile, Arbitrage)
- Built PDF generator engine using WeasyPrint with professional CSS styling — cover page, table of contents, per-tool sections with colour-coded accent bars, feature checklists, API endpoint tables, and practical examples
- Exposed Flask blueprint at /api/docs/tools-pdf serving downloadable PDF with 1-hour cache control
- Created standalone CLI script at scripts/generate_tools_pdf.py for offline PDF generation
- Added WeasyPrint dependency to pyproject.toml and verified generation produces valid 43KB PDF

## Task Commits

1. **Task 1: Create tools documentation data module** — `ec003964` (feat)
2. **Task 2: Create PDF generator, Flask blueprint, and CLI script** — `3f917b4a` (feat)

**Plan metadata:** (final commit)

## Files Created/Modified

- `blueprints/tools_docs/docs_data.py` — ToolDoc dataclass + 15 tool instances with overviews, features, usage steps, API endpoints, examples (687 lines)
- `blueprints/tools_docs/pdf_generator.py` — HTML+CSS→PDF engine with WeasyPrint, cover page, TOC, per-tool sections, professional styling (437 lines)
- `blueprints/tools_docs/__init__.py` — Flask Blueprint with GET /api/docs/tools-pdf endpoint (73 lines)
- `scripts/generate_tools_pdf.py` — Standalone CLI script for PDF generation (61 lines)
- `pyproject.toml` — Added WeasyPrint dependency

## Decisions Made

- **WeasyPrint 69.0** for PDF generation — provides HTML+CSS rendering with full professional styling control
- **PDF on-demand generation** — regenerated per request with 1-hour cache control to always reflect latest docs
- **Per-tool colour coding** — each tool section header bar uses its Tools.tsx hex colour for visual consistency
- **ToolDoc dataclass pattern** — structured documentation data stored as Python objects for both programmatic rendering and potential future markdown/HTML export
- **Cover page design** — dark gradient theme with "Quantix Tools Reference Guide" branding matching platform identity

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

- WeasyPrint was not pre-installed — added to pyproject.toml and installed via `uv sync`. System-level dependencies (libpango, libcairo, libffi) were already present.
- Flask 3.1.3 `Blueprint.deferred_functions` uses tuple-based wrappers instead of function objects — addressed by adjusting route inspection logic in verification.

## User Setup Required

None — all functionality is self-contained in the codebase. Run `uv sync` to install WeasyPrint (already done as part of this plan).

## Next Phase Readiness

- Backend infrastructure for tools documentation PDF is complete
- Ready for Phase 06 Plan 02: Frontend download button on /tools page
- Flask blueprint already registered at /api/docs/tools-pdf — frontend only needs to add a download button linking to this endpoint

---
*Phase: 06-tools-docs-pdf*
*Completed: 2026-07-17*
