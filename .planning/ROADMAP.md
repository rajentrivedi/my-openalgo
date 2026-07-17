# OpenAlgo Roadmap

## Phase 1: Kotak Neo Order Placement Page

**Goal:** Dedicated manual order entry page for placing orders directly from the UI, with symbol search, real-time quotes, and full order form.

**Requirements:** [ORD-01, ORD-02, ORD-03]

**Plans:** 1 plan
- [ ] 01-01-PLAN.md — Create dedicated order placement page with symbol search, real-time quotes, and order form

---

## Phase 2: Kotak Neo Zero IP Proxy Integration

**Goal:** Route Kotak Securities Neo trading API traffic through Zero IP static IPv6 proxy so that all trading API calls originate from a whitelisted IP address, enabling order placement, quotes, and portfolio management for Kotak Neo users on dynamic IPs.

**Requirements:** [PROXY-01, PROXY-02, PROXY-03]

**Plans:** 2 plans
- [ ] 02-01-PLAN.md — Add Zero IP proxy configuration and proxy-aware httpx client
- [ ] 02-02-PLAN.md — Update Kotak broker plugin modules to route API calls through Zero IP proxy

---

## Phase 3: Wishlist Page

**Goal:** Dedicated wishlist page where users can save and manage symbols they want to track or trade later, with notes and target prices, persisted in the database.

**Requirements:** [WISH-01, WISH-02, WISH-03]

**Plans:** 0 plans
- [ ] 03-01-PLAN.md — Create wishlist database, API blueprint, and frontend page

---

---

## Phase 4: Wishlist Price Alerts

**Goal:** Add price alert functionality to the wishlist page so users can set target price and percentage change alerts on wishlist items, with automatic notifications via Telegram and WhatsApp when conditions are met.

**Requirements:** [WISH-ALERT-01, WISH-ALERT-02, WISH-ALERT-03, WISH-ALERT-04]

**Plans:** 0 plans
- [x] 04-01-PLAN.md — Create wishlist alert database, monitor service, API, and frontend

---

## Phase 5: Rebrand to Quantix

**Goal:** Rebrand the application UI, backend responses, and branding assets from "OpenAlgo" to "Quantix", including logo swap, favicon, app title, navbar branding, footer, watermarks, FAQ content, startup banner, API response names, and service messages.

**Requirements:** [BRAND-01, BRAND-02, BRAND-03, BRAND-04]

**Plans:** 3 plans
- [x] 05-01-PLAN.md — Frontend visual rebranding (logos, navbar, footer, index.html, watermarks)
- [x] 05-02-PLAN.md — Frontend content rebranding (FAQ, setup, page text, internal keys)
- [x] 05-03-PLAN.md — Backend rebranding (banner, blueprints, services, TOTP, email)

---

## Phase 6: Tools Documentation PDF

**Goal:** Generate a comprehensive PDF reference document documenting all 15 analytical tools available on the `/tools` route, with detailed descriptions, key features, usage instructions, API endpoints, and practical examples for each tool.

**Requirements:** [DOC-01, DOC-02, DOC-03, DOC-04]

**Plans:** 2 plans
- [ ] 06-01-PLAN.md — Backend: tools documentation content + PDF generator + Flask download endpoint + CLI script
- [ ] 06-02-PLAN.md — Frontend: download button on /tools page + blueprint registration

---

## Requirements

### DOC-01: Tools Documentation Content
Comprehensive documentation content for all 15 tools on the /tools route, including overview, key features, usage instructions, API endpoints, practical examples, and configuration notes for each tool.

### DOC-02: PDF Generation
A Python script and/or Flask endpoint that generates a professional PDF from the documentation content using WeasyPrint (HTML->CSS->PDF), with cover page, table of contents, and professional styling.

### DOC-03: Usage Examples
Each tool documented with practical, real-world usage examples demonstrating how to use the tool effectively in common trading scenarios.

### DOC-04: Frontend Access
A download button on the /tools page that triggers PDF download from /api/docs/tools-pdf.

### WISH-ALERT-01: Alert Database
A database model for wishlist alerts with fields for wishlist_item_id, user_id, condition_type (target_above, target_below, percent_up, percent_down), condition_value, reference_price, is_active, triggered status, and timestamps.

### WISH-ALERT-02: Price Monitor Service
A singleton background service that periodically polls active wishlist alerts, fetches live prices via quotes_service, evaluates conditions, and triggers notifications.

### WISH-ALERT-03: Alert API
REST API endpoints (POST/GET/DELETE/PATCH) for creating, listing, toggling, and deleting wishlist alerts, scoped to the authenticated user.

### WISH-ALERT-04: Alert UI
Frontend integration on the wishlist page to set alerts per symbol, view alert status (active/triggered), enable/disable, and delete alerts.

### ORD-01: Manual Order Entry Page
A dedicated page at `/place-order` where users can manually enter and submit orders with symbol search, exchange selection, and full order parameters.

### ORD-02: Real-time Quote Integration
The order page displays real-time LTP, bid/ask, and market depth for the selected symbol using WebSocket with REST API fallback.

### ORD-03: Navigation Integration
The order placement page is accessible from the main navigation bar and mobile bottom navigation.

### PROXY-01: Zero IP Proxy Configuration
New environment variables for Zero IP proxy URL, Bearer token authentication, and IPv6 address selection, with config.py getter functions and .env documentation.

### PROXY-02: Proxy-Aware HTTP Client
The shared httpx client supports optional proxy routing with custom headers (Proxy-Authorization Bearer token, X-Snipy-Proxy-IPv6) for broker-specific proxy configuration.

### PROXY-03: Kotak Proxy Integration
All Kotak Neo REST API calls (auth, orders, quotes, funds) route through the Zero IP proxy when enabled, configurable via environment variables with graceful fallback if proxy is unavailable.

### WISH-01: Wishlist Database
A database model to store wishlist items with fields for symbol, exchange, notes, target price, and created_at timestamp, with SQLAlchemy ORM and auto table creation.

### WISH-02: Wishlist API
REST API endpoints (GET, POST, DELETE) for managing wishlist items, with session-based auth via `@check_session_validity`.

### BRAND-01: Frontend Visual Identity
All frontend UI elements showing the brand name or logo (navbar, footer, index.html title/meta, favicon, chart watermarks) are updated from "OpenAlgo" to "Quantix" using the new logo assets.

### BRAND-02: Frontend Content References
All user-facing text content in frontend pages (FAQ, setup flow, broker selection, API playground, flow editor, health check text) that references "OpenAlgo" as the platform name is updated to "Quantix".

### BRAND-03: Backend Branding
Backend Python files (startup banner, API name response, health checks, system reports, Telegram/WhatsApp messages, TOTP issuer, email sender name, MCP auth page, version info) reflect the new "Quantix" brand name.

### BRAND-04: Brand Asset Integration
New logo-light.png, logo-dark.png, and favicon.ico are properly referenced from the frontend HTML and components in place of the old OpenAlgo brand assets (logo.png, favicon_0.ico).

### WISH-03: Wishlist Page
A dedicated page at `/wishlist` where users can view saved symbols, add new ones via symbol search, set notes/targets, and delete items. Accessible from the main navigation menu.
