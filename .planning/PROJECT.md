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
