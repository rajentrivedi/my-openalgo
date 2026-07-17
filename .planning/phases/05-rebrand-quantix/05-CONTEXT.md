# Phase 5: Rebrand to Quantix - Context

**Gathered:** 2026-07-17
**Status:** Ready for planning
**Source:** User request

<domain>
## Phase Boundary

Rebrand the OpenAlgo trading platform user interface and backend to "Quantix". The application functionality, architecture, and external integrations remain unchanged — only the visual identity and user-facing name references are updated.

**In scope:**
- All frontend UI components showing "OpenAlgo" → "Quantix"
- Backend startup banner, API responses, system report names
- Service messages (Telegram, WhatsApp, email)
- TOTP issuer name, MCP auth page title
- Chart/strategy watermark text
- Internal keys (localStorage, search download filenames)
- Logo file references (swap old assets for new logo-light.png, logo-dark.png, favicon.ico)

**NOT in scope:**
- External URLs (docs.openalgo.in, github.com/marketcalls/openalgo, openalgo.in/discord, x.com/openalgoHQ, youtube.com/@openalgo) — these are external project links and remain unchanged
- SDK dependency `openalgo==2.0.2` (pyproject.toml) — this is the pip package name, not brand
- Default DATABASE_URL `sqlite:///db/openalgo.db` — changing it would break existing installs
- Broker integration product names (Motilal `productname: "OpenAlgo"`, 5paisa `RemoteOrderID: "OpenAlgo"`) — these identify the source app to broker APIs and are not user-facing
- Project package name `pyproject.toml name = "openalgoUI"` — this is the pip package name
</domain>

<decisions>
## Implementation Decisions

### Brand Name
- D-01: The new brand name is "Quantix" (capital Q, rest lowercase)
- D-02: App description changes from "OpenAlgo - Open Source Algorithmic Trading Platform" / "Your Personal Algo Trading Platform" to "Quantix - Open Source Algorithmic Trading Platform" / "Your Personal Algo Trading Platform"

### Brand Assets
- D-03: New logo files are at `frontend/public/logo-light.png`, `frontend/public/logo-dark.png`. These replace `frontend/public/logo.png`
- D-04: New favicon is at `frontend/public/favicon.ico`. This replaces `frontend/public/favicon_0.ico`
- D-05: The `frontend/index.html` should use a `<picture>` element with `prefers-color-scheme` to serve the appropriate logo (light/dark) as the favicon/apple-touch-icon or just update the favicon reference

### Watermarks
- D-06: Chart/strategy watermarks show "Quantix" instead of "OpenAlgo"
- D-07: Payoff chart watermark shows "quantix.in" instead of "openalgo.in" (mirroring the brand domain pattern)

### Internal Keys
- D-08: localStorage keys `openalgo:*` → `quantix:*`
- D-09: Search CSV download filename `openalgo-search-*.csv` → `quantix-search-*.csv`
- D-10: Chunk reload flag `openalgo:chunk-reload-attempted` → `quantix:chunk-reload-attempted`

### API Responses
- D-11: The `/auth/session-status` endpoint returns `"name": "Quantix"` instead of `"name": "OpenAlgo"`
- D-12: The MCP version response returns `"Quantix version: {ver}"` instead of `"OpenAlgo version: {ver}"`

### Version Documentation
- D-13: `utils/version.py` docstring updated to reference Quantix

### the agent's Discretion
- FAQ content should be updated to reference "Quantix" where it says "OpenAlgo" as the platform name, but preserve credit to the original OpenAlgo project where legally/attribution required
- Startup banner tagline can stay "Your Personal Algo Trading Platform" unless creative input desired
- Email sender "OpenAlgo Admin" → "Quantix Admin"
- System report "OpenAlgo" heading → "Quantix"
- MCP OAuth page title "Authorize {client_name} — OpenAlgo" → "Authorize {client_name} — Quantix"
- React fallback page title "OpenAlgo - Frontend Not Available" → "Quantix - Frontend Not Available"
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Structure & Conventions
- `.planning/PROJECT.md` — Stack: Flask+React+SQLite+eventlet
- `.planning/STATE.md` — Current project state
- `AGENTS.md` — Project-specific guidelines (NullPool, ZMQ, eventlet constraints)
- `CLAUDE.md` — Deep architectural detail

### Frontend Branding Files (existing)
- `frontend/index.html` — Title and meta tags
- `frontend/src/components/layout/Navbar.tsx` — Logo + brand name
- `frontend/src/components/layout/Footer.tsx` — Brand name + social links
- `frontend/src/pages/Faq.tsx` — FAQ content

### Backend Branding Files
- `app.py` — Startup banner (line 37, 1032-1057)
- `blueprints/auth.py` — API name response (line 1059)
- `blueprints/health.py` — Health endpoint descriptions
- `blueprints/admin.py` — System report
- `blueprints/telegram.py` — Test messages
- `blueprints/whatsapp.py` — Test messages
- `blueprints/react_app.py` — Fallback page
- `blueprints/mcp_oauth.py` — Auth page title
- `database/user_db.py` — TOTP issuer
- `utils/email_utils.py` — Sender name
- `utils/version.py` — Version docstring
- `mcp/mcpserver.py` — MCP version response

### New Brand Assets
- `frontend/public/logo-light.png` — Light theme logo
- `frontend/public/logo-dark.png` — Dark theme logo
- `frontend/public/favicon.ico` — New favicon
</canonical_refs>

<specifics>
## Specific Ideas

- Use `<picture>` element with `prefers-color-scheme` media query for logo switching in the Navbar (light/dark mode)
- Navbar logo img src should change based on theme: `logo-light.png` for light mode, `logo-dark.png` for dark mode
- All user-facing "OpenAlgo" text replaced with "Quantix" while preserving attribution where legally required (FAQ about rebranding/commercial use question)
- External links to docs.openalgo.in, github.com/marketcalls/openalgo, etc. are preserved as-is (actual project URLs)
</specifics>

<deferred>
## Deferred Ideas

- None — phase scope covers full rebranding
</deferred>

---

*Phase: 05-rebrand-quantix*
*Context gathered: 2026-07-17 via user request*
