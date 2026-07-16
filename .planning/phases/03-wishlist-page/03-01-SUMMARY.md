---
phase: 03-wishlist-page
plan: 01
subsystem: wishlist
tags: [database, api, blueprint, ui, react, navigation]
requires: [02-place-order-page]
provides: [wishlist-core]
affects: [app.py, frontend/src/App.tsx, frontend/src/config/navigation.ts]
tech-stack:
  added:
    - SQLAlchemy (WishlistItem model)
    - Flask Blueprint (3 REST endpoints)
    - React 19 (Wishlist page with autocomplete search)
  patterns:
    - NullPool for SQLite connections
    - scoped_session pattern matching strategy_db.py
    - @check_session_validity decorator for auth
    - webClient for session-based API calls
    - shadcn/ui Card, Table, Button, Input, Select
    - Lucide icons (Heart, Plus, Trash2, RefreshCw)
key-files:
  created:
    - database/wishlist_db.py
    - blueprints/wishlist.py
    - frontend/src/api/wishlist.ts
    - frontend/src/pages/Wishlist.tsx
  modified:
    - app.py
    - frontend/src/App.tsx
    - frontend/src/config/navigation.ts
decisions:
  - "Unique index on (user_id, symbol) prevents duplicate wishlist entries"
  - "DELETE endpoint filters by both item_id AND user_id for security (prevents cross-user deletion)"
  - "Autocomplete search uses existing /search/api/search endpoint with 300ms debounce"
  - "Exchange selector uses native shadcn Select component with NSE/BSE/NFO/CDS/MCX options"
metrics:
  duration: "39 minutes"
  completed_date: "2026-07-16"
---

# Phase 03 Plan 01: Wishlist Page Summary

**One-liner:** Full wishlist feature with SQLAlchemy model + CRUD, 3-endpoint Flask REST API, React 19 page with autocomplete symbol search and add/delete UI, plus navigation integration.

All 6 tasks executed successfully. Every task committed individually with 6 atomic commits.

## Tasks Executed

| Task | Name                          | Commit     | Files                                   |
| ---- | ----------------------------- | ---------- | --------------------------------------- |
| 1    | Create wishlist database module | a21ef78c  | `database/wishlist_db.py`               |
| 2    | Create wishlist API blueprint  | 3a63eeed  | `blueprints/wishlist.py`                |
| 3    | Register blueprint and DB init in app.py | c220db7a | `app.py` |
| 4    | Create frontend wishlist API client | 45ccc565 | `frontend/src/api/wishlist.ts` |
| 5    | Create wishlist page component | 1bf80915  | `frontend/src/pages/Wishlist.tsx`       |
| 6    | Register wishlist route and navigation | 5ec995ff | `frontend/src/App.tsx`, `frontend/src/config/navigation.ts` |

## Deliverables

### Backend

**database/wishlist_db.py** — SQLAlchemy `WishlistItem` model with columns `id`, `user_id`, `symbol`, `exchange`, `notes`, `target_price`, `created_at`. Unique composite index on `(user_id, symbol)`. CRUD functions:
- `add_item(user_id, symbol, exchange, notes=None, target_price=None)` — inserts and commits, returns item or None
- `get_user_items(user_id)` — queries by user_id, ordered by `created_at` desc, returns list
- `remove_item(item_id, user_id)` — deletes only when BOTH id and user_id match (security)
- `init_db()` — calls `init_db_with_logging()` for table creation

**blueprints/wishlist.py** — Flask blueprint with `url_prefix="/wishlist"` and 3 endpoints:
- `GET /wishlist/api/items` — list items with symbol, exchange, notes, target_price, created_at
- `POST /wishlist/api/items` — add item (validates symbol and exchange required, returns 400 if missing)
- `DELETE /wishlist/api/items/<int:item_id>` — delete item (404 if not found)

All endpoints use `@check_session_validity` and `session.get("user")`.

**app.py** — 4 insertions: wishlist DB import + init call in parallel DB initialization, wishlist blueprint import + registration.

### Frontend

**frontend/src/api/wishlist.ts** — Typed API client:
- `WishlistItem` interface (id, symbol, exchange, notes, target_price, created_at)
- `AddWishlistItemRequest` interface (symbol, exchange, optional notes/target_price)
- `wishlistApi.getItems()`, `.addItem()`, `.removeItem(itemId)` using `webClient`

**frontend/src/pages/Wishlist.tsx** — Full React 19 page with:
- Autocomplete symbol search with 300ms debounce via `/search/api/search`
- Add form: search input with dropdown results, exchange selector (Select), notes input, target price input, add button
- Items table: Symbol, Exchange (badge), Notes, Target Price (INR formatted), Added (date), Action (trash icon)
- Loading spinner, empty state, refresh button

**frontend/src/App.tsx** — Added `lazy(() => import('@/pages/Wishlist'))` + `<Route path="/wishlist" />`

**frontend/src/config/navigation.ts** — Added `Heart` icon import + `{ href: '/wishlist', label: 'Wishlist', icon: Heart }` nav item

## Verification Results

- Backend CRUD: Passed (add/get/remove, security check)
- Flask routes: 3 routes registered (GET/POST/DELETE)
- TypeScript: No compilation errors
- Frontend build: `vite build` successful
- Full app import: `import app` passes

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED

- [x] All 6 tasks executed
- [x] Each task committed individually (6 commits)
- [x] `database/wishlist_db.py` — model + CRUD working
- [x] `blueprints/wishlist.py` — 3 endpoints, all use @check_session_validity
- [x] `app.py` — 4 insertions, full import OK
- [x] `frontend/src/api/wishlist.ts` — typed API client
- [x] `frontend/src/pages/Wishlist.tsx` — autocomplete, add/delete, table
- [x] `frontend/src/App.tsx` — lazy import + route
- [x] `frontend/src/config/navigation.ts` — Heart icon + nav item
- [x] Backend imports work
- [x] TypeScript compilation passes
- [x] Frontend builds successfully
- [x] Full app import: `import app` succeeds
