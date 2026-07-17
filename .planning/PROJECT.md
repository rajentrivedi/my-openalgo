# OpenAlgo - Project

**Platform:** Flask (Python 3.12+) + React 19 (TypeScript, Vite, shadcn/ui)
**Purpose:** Self-hosted algorithmic trading platform with 34+ Indian broker integrations
**Repo:** https://github.com/marketcalls/openalgo

## Architecture

- **Backend:** Flask + Flask-RESTX + SQLAlchemy (SQLite, NullPool) + eventlet (prod)
- **Frontend:** React 19 SPA, TanStack Query, shadcn/ui, Biome.js
- **Real-time:** Flask-SocketIO (events), WebSocket proxy (market data), ZeroMQ (internal bus)
- **Broker plugins:** `broker/{name}/` with standardized api/, mapping/, streaming/, database/

## Key Constraints

- Single user per deployment
- No asyncio in production (eventlet incompatibility)
- Gunicorn -w 1 always
- SQLite NullPool only
- ZMQ: SUB binds, PUBs connect

## Phase 06 Complete

Phase 06 (Tools Documentation PDF) completed 2026-07-17:
- `blueprints/tools_docs/` package with structured docs data, WeasyPrint PDF generator, and Flask download endpoint at `/api/docs/tools-pdf`
- Download button on `/tools` page for one-click PDF access
- CLI script at `scripts/generate_tools_pdf.py` for standalone generation
