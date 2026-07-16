---
plan: "04-01"
phase: "04"
status: "complete"
commits:
  - "3df4865e — feat(phase-04): create wishlist alert database model"
  - "594c70a9 — feat(phase-04): create WishlistPriceMonitorService with polling and Telegram/WhatsApp dispatch"
  - "3baa4dba — feat(phase-04): add wishlist alert API endpoints (POST/GET/PATCH/DELETE)"
  - "63892c9f — feat(phase-04): register wishlist alert DB init in app.py"
  - "b7b25eed — feat(phase-04): extend frontend API client with wishlist alert types and methods"
  - "23499bdc — feat(phase-04): add alert management UI to Wishlist page with bell indicators, creation modal, and toggle/delete"
  - "a6576db1 — style(phase-04): fix import sorting after lint"
---

# Plan 04-01: Create Wishlist Price Alert System — Summary

## What was built

### Database
- `database/wishlist_alert_db.py` — `wishlist_alerts` table with FK to `wishlist_items`, condition types (`target_above`, `target_below`, `percent_up`, `percent_down`), active/triggered state tracking, and all CRUD functions

### Backend Service
- `services/wishlist_price_monitor_service.py` — Singleton `WishlistPriceMonitor` with background polling (30s interval), price fetching via `quotes_service.get_quotes_with_auth()`, condition evaluation, and Telegram + WhatsApp notification dispatch

### API Endpoints
- `blueprints/wishlist.py` — 6 new routes:
  - `POST /api/alerts` — create alert (validates condition type, fetches reference price)
  - `GET /api/alerts` — list user's alerts
  - `GET /api/alerts/wishlist/<id>` — alerts for a specific item
  - `PATCH /api/alerts/<id>/toggle` — toggle active state
  - `DELETE /api/alerts/<id>` — delete alert
  - `DELETE /api/alerts/wishlist/<id>` — delete all alerts for an item

### App Registration
- `app.py` — alert DB init registered in startup sequence

### Frontend API Client
- `frontend/src/api/wishlist.ts` — `WishlistAlert` interface, `CreateAlertRequest`, 5 new API methods
- `frontend/src/api/client.ts` — added PATCH to CSRF interceptor

### Frontend UI
- `frontend/src/pages/Wishlist.tsx` — bell icon per row (green for active, amber for triggered, gray for disabled), alert creation dialog with condition selector, inline switch toggle and delete, alerts deleted when wishlist item is removed

## Key Decisions
- Polling interval set to 30 seconds (not aggressive, suitable for broker API rate limits)
- Reference price auto-fetched for percentage conditions at alert creation time
- Monitor auto-starts on first alert creation, auto-stops when no active alerts remain
- Both Telegram and WhatsApp dispatched simultaneously; logs a warning if neither is configured

## Files Created/Modified
- `database/wishlist_alert_db.py` (created)
- `services/wishlist_price_monitor_service.py` (created)
- `blueprints/wishlist.py` (modified)
- `app.py` (modified)
- `frontend/src/api/wishlist.ts` (modified)
- `frontend/src/api/client.ts` (modified)
- `frontend/src/pages/Wishlist.tsx` (modified)
