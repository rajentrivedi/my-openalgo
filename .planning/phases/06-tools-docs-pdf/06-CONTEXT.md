# Phase 6: Tools Documentation PDF — Context

**Gathered:** 2026-07-17
**Status:** Ready for planning
**Source:** User request

<domain>
## Phase Boundary

Create a comprehensive PDF documentation for all 15 analytical tools listed on the `/tools` route. The PDF should include detailed descriptions, features, screenshots/scenarios, API endpoints consumed, practical usage examples, and configuration details for each tool.

**In scope:**
- Documentation content for all 15 tools on /tools route
- PDF generation using WeasyPrint (HTML→CSS→PDF)
- Flask API endpoint to download the PDF
- CLI script for standalone PDF generation
- Frontend download button on /tools page
- Professional PDF layout with table of contents, sections, and examples

**NOT in scope:**
- Editing/rewriting the existing tool pages themselves
- Adding new features to existing tools
- Rebranding or renaming tools
- Video/animated documentation
- API documentation for the backend (already exists in docs/)
- User guide documentation (already exists in docs/userguide/)
</domain>

<tools_inventory>
## Tools to Document

The 15 tools listed at `frontend/src/pages/Tools.tsx` (lines 5-103):

| # | Tool | Route | Color | Description |
|---|------|-------|-------|-------------|
| 1 | Strategy Builder | `/strategybuilder` | bg-fuchsia-500 | Multi-leg option strategies with live Greeks, payoff diagram and what-if simulators |
| 2 | Strategy Portfolio | `/strategybuilder/portfolio` | bg-violet-500 | Saved strategies across MyTrades and Simulation watchlists |
| 3 | Option Chain | `/optionchain` | bg-emerald-500 | Real-time option chain with live Greeks, OI data, and quick order placement |
| 4 | Option Greeks (IV Chart) | `/ivchart` | bg-violet-500 | Historical IV, Delta, Theta, Vega, and Gamma charts for ATM options |
| 5 | OI Tracker | `/oitracker` | bg-blue-500 | Open Interest analysis with CE/PE OI bars, PCR overlay, and ATM strike marker |
| 6 | OI Range | `/oirange` | bg-sky-500 | Open Interest by strike for a custom range with ATM-relative quick selectors and optional 1-minute auto-refresh |
| 7 | Max Pain | `/maxpain` | bg-amber-500 | Max Pain strike calculation with visual pain distribution across strikes |
| 8 | Straddle Chart | `/straddle` | bg-teal-500 | Dynamic ATM Straddle chart with rolling strike, Spot, and Synthetic Futures overlay |
| 9 | Straddle PnL | `/straddlepnl` | bg-orange-500 | Simulated intraday ATM straddle P&L with automated N-point adjustments and trade log |
| 10 | Vol Surface | `/volsurface` | bg-rose-500 | 3D Implied Volatility surface across strikes and expiries using live option chain data |
| 11 | GEX Dashboard | `/gex` | bg-indigo-500 | Gamma Exposure analysis with OI Walls, Net GEX per strike, and top gamma strikes |
| 12 | Gamma Density | `/gammadensity` | bg-lime-500 | Γ×OI density and convexity zones with intraday & to-expiry views, ATM IV, and ±1σ/±2σ expected-move bands |
| 13 | IV Smile | `/ivsmile` | bg-cyan-500 | Implied Volatility smile with Call/Put IV curves, ATM IV, and skew analysis |
| 14 | OI Profile | `/oiprofile` | bg-orange-500 | Futures candlestick with OI butterfly and daily OI change across strikes |
| 15 | Arbitrage | `/arbitrage` | bg-green-600 | Realtime futures calendar-spread scanner across NFO & MCX, ranked by executable bid/ask spread % with one-click two-leg orders |
</tools_inventory>

<backend_apis>
## Backend API Endpoints (per tool)

Tool pages consume these backend endpoints:

- **Strategy Builder**: `/api/v1/strategy-chart` (chart data), `/api/v1/optionchain` (chain lookup), `/api/v1/strategy/portfolio` (saved strategies), `/api/v1/trading/placeorder` (basket orders)
- **Strategy Portfolio**: `/api/v1/strategy/portfolio` (CRUD watchlists/entries)
- **Option Chain**: `/api/v1/optionchain` (live chain data with Greeks + OI), `/api/v1/oiprofile` (OI bars), `/api/search` (symbol search), WebSocket feed
- **Option Greeks**: `/api/v1/iv-chart` (IV/Greeks history)
- **OI Tracker**: `/api/v1/oi-tracker` (OI data)
- **OI Range**: `/api/v1/oiprofile` (OI by strike range)
- **Max Pain**: `/api/v1/oi-tracker/maxpain` (max pain calculation)
- **Straddle Chart**: `/api/v1/straddle-chart` (straddle history)
- **Straddle PnL**: `/api/v1/custom-straddle` (PnL + trades data)
- **Vol Surface**: `/api/v1/vol-surface` (3D IV surface data)
- **GEX Dashboard**: `/api/v1/gex` (gamma exposure data)
- **Gamma Density**: `/api/v1/gamma-density` (density data)
- **IV Smile**: `/api/v1/iv-smile` (IV smile data)
- **OI Profile**: `/api/v1/oiprofile` (profile data)
- **Arbitrage**: `/api/v1/arbitrage` (spread scanner data), `/api/v1/trading/placeorder` (basket orders)
</backend_apis>

<decisions>
## Implementation Decisions

### Technology
- D-01: Use **WeasyPrint** for PDF generation (HTML → CSS → PDF) for maximum styling flexibility
- D-02: Documentation content will be defined as Python data structures (not separate markdown files) for programmatic generation
- D-03: PDF will use professional styling with cover page, table of contents, tool sections, and examples

### Architecture
- D-04: Documentation content lives in `blueprints/tools_docs/docs_data.py` as structured data
- D-05: PDF generator in `blueprints/tools_docs/pdf_generator.py` handles rendering
- D-06: Flask blueprint in `blueprints/tools_docs/__init__.py` exposes download endpoint
- D-07: CLI script at `scripts/generate_tools_pdf.py` for standalone generation

### PDF Layout
- D-08: Cover page with platform title, description, and date
- D-09: Table of Contents with page numbers
- D-10: Each tool gets: overview, key features, how to use, configuration, API endpoints, practical example
- D-11: Professional CSS styling with consistent typography, color coding per tool, code blocks
- D-12: Page numbers in footer, section headers in header

### Frontend
- D-13: Download button in `/tools` page header area
- D-14: PDF served from `/api/docs/tools-pdf` endpoint
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before implementing.**

### Phase Context
- `.planning/PROJECT.md` — Stack: Flask+React+SQLite+eventlet
- `.planning/STATE.md` — Current project state
- `AGENTS.md` — Project-specific guidelines (NullPool, ZMQ, eventlet constraints)
- `CLAUDE.md` — Deep architectural detail

### Files to Read
- `frontend/src/pages/Tools.tsx` — Tool list definitions
- `blueprints/react_app.py` — Route patterns
- `app.py` — Blueprint registration pattern (lines 105-155 imports, 267-332 registrations)
- Each tool page for reference (read the ones that require deeper understanding during implementation)

### PDF Library
- WeasyPrint: HTML→CSS→PDF — install via `uv add weasyprint`
- Add system deps: libpango-1.0-0, libpangocairo-1.0-0, libgdk-pixbuf2.0-0, libffi-dev, libcairo2
</canonical_refs>

<the_agent_discretion>
## the agent's Discretion

- PDF CSS styling choices (colors, fonts, layout) — keep professional and consistent with platform style
- Level of detail in examples — sufficient for a new user to understand each tool's purpose
- Whether to include screenshots or rely on text/table descriptions (start with text, add screenshots if time permits)
- PDF filename convention — use `quantix-tools-reference-guide.pdf` or similar
- Whether to cache the PDF or regenerate on each request (recommend: cache with regeneration trigger via CLI)
</the_agent_discretion>

<deferred>
## Deferred Ideas

- None — phase scope covers full tools documentation PDF
</deferred>

---

*Phase: 06-tools-docs-pdf*
*Context gathered: 2026-07-17 via user request*
