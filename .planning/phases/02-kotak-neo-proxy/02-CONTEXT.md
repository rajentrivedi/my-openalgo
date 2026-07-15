# Phase 2: Kotak Neo Zero IP Proxy Integration - Context

**Gathered:** 2026-07-15
**Status:** Ready for planning
**Source:** User request (direct)

<domain>
## Phase Boundary

Configure the OpenAlgo Kotak Securities Neo broker plugin to route all trading API traffic through the snipy.in/zero-ip HTTPS proxy service, which provides a static IPv6 egress address that can be whitelisted with Kotak Securities for API-based order placement.

**Problem:** Kotak Securities Neo trading API requires all API traffic to originate from a whitelisted IP address. OpenAlgo users running on dynamic IPs (home servers, cloud VPS with elastic IPs, etc.) cannot maintain a consistent whitelisted IP, causing trading API calls to fail.

**Solution:** snipy.in/zero-ip provides a free HTTPS CONNECT proxy at `https://zeroip.snipy.in:8443` that routes traffic through a dedicated static IPv6 address. The service supports Kotak Securities Neo (`napi.kotaksecurities.com`) in its broker domain allowlist.

**Boundary:** WebSocket streaming (`wss://mlhsm.kotaksecurities.com`) is out of scope — Zero IP does not support WebSocket proxy. Only HTTPS REST API calls through the proxy.
</domain>

<decisions>
## Implementation Decisions

### D-01: Proxy Service
- **Service:** snipy.in/zero-ip — free HTTPS CONNECT proxy at `https://zeroip.snipy.in:8443`
- **Auth:** Bearer token in `Proxy-Authorization` header
- **IP selection:** `X-Snipy-Proxy-IPv6` header for dedicated IPv6 address
- **Domains allowed:** `napi.kotaksecurities.com` (Kotak Neo trading API)
- **Reference:** https://www.snipy.in/zero-ip

### D-02: Proxy Configuration via Env Vars
- `KOTAK_PROXY_ENABLED` — Master switch (true/false, default false)
- `KOTAK_PROXY_URL` — Proxy URL, default `https://zeroip.snipy.in:8443`
- `KOTAK_PROXY_API_KEY` — Bearer token from snipy dashboard
- `KOTAK_PROXY_IPV6` — Dedicated IPv6 address from snipy dashboard
- Pattern follows existing `BROKER_API_KEY`/`BROKER_API_SECRET` convention

### D-03: httpx Proxy Implementation
- Use `httpx.Proxy(url=..., headers={...})` for proxy configuration with custom headers
- httpx natively supports `Proxy-Authorization` and custom headers via `httpx.Proxy.headers`
- Create separate proxy-enabled client rather than modifying global singleton
- The `get_httpx_client()` remains the default; Kotak modules call `get_proxied_httpx_client()` when proxy is enabled

### D-04: requests Library Handling
- `order_api.py` uses both httpx and raw `requests.post()` (for `place_order_api` with multiple format attempts)
- Convert `requests.post()` calls in `place_order_api` to httpx to maintain proxy support
- Eliminates the mixed httpx/requests pattern that complicates proxy routing

### D-05: Routing Scope
- **Route through proxy:** All Kotak trading API calls on broker-assigned base_url (typically `napi.kotaksecurities.com`) — orders, quotes, depth, positions, holdings, tradebook, orderbook
- **Auth domain (`mis.kotaksecurities.com`)**: May not need proxy; route direct by default, but if auth also requires IP whitelist, route through proxy too. Zero IP only blocks unauthorized domains, so routing auth through proxy is safe (it will either work or fail gracefully).
- **WebSocket (`wss://mlhsm.kotaksecurities.com`)**: Out of scope — Zero IP doesn't support WS proxy

### D-06: Error Handling
- When proxy is enabled but unavailable, Kotak API calls should log explicit error and fall back to direct connection (configurable)
- Add clear error logging indicating whether the failure was due to proxy vs. broker API

### the agent's Discretion
- Whether to convert all `requests` calls to httpx or only those in `place_order_api`
- Exact error message format and logging levels
- Whether to add a `connect_timeout` for proxy connection specifically
- Whether to support per-request proxy bypass for specific endpoints

</decisions>

<canonical_refs>
## Canonical References

### Broker Pattern
- `broker/kotak/api/auth_api.py` — Kotak Neo authentication flow (login, TOTP, MPIN validation)
- `broker/kotak/api/order_api.py` — Kotak Neo order placement, modify, cancel, orderbook, positions
- `broker/kotak/api/data.py` — Kotak Neo quotes, depth, multiquotes REST API
- `broker/kotak/api/funds.py` — Kotak Neo margin/funds API

### Shared Infrastructure
- `utils/httpx_client.py` — Shared httpx client with connection pooling (must NOT break existing usage)
- `utils/config.py` — Environment variable getter functions (pattern to follow for new vars)

### Existing Example
- `broker/dhan_sandbox/api/auth_api.py` (lines 302-380) — Dhan's static IP management API (reference for IP whitelisting pattern, though Zero IP is proxy-based not API-based)

### Zero IP Service
- `https://www.snipy.in/zero-ip` — Zero IP documentation and dashboard

</canonical_refs>

<specifics>
## Specific Ideas

### Zero IP Integration Approach

Zero IP works as an HTTPS CONNECT proxy. From their docs:

```bash
curl \
  --proxy https://zeroip.snipy.in:8443 \
  --proxy-header "Proxy-Authorization: Bearer <PROXY_API_KEY>" \
  --proxy-header "X-Snipy-Proxy-IPv6: <YOUR_IPV6>" \
  https://api.kite.trade/instruments
```

In httpx:
```python
import httpx

proxy = httpx.Proxy(
    url="https://zeroip.snipy.in:8443",
    headers={
        "Proxy-Authorization": "Bearer <PROXY_API_KEY>",
        "X-Snipy-Proxy-IPv6": "<YOUR_IPV6>",
    }
)
client = httpx.Client(proxy=proxy)
# All requests through this client go via Zero IP
response = client.get("https://napi.kotaksecurities.com/...")
```

### Current Kotak API Call Flow

1. **Auth** (`auth_api.py`): POST to `https://mis.kotaksecurities.com/login/1.0/tradeApiLogin` then `/tradeApiValidate` — uses `get_httpx_client()`
2. **Orders** (`order_api.py`): `get_api_response()` uses httpx via `get_httpx_client()`, `place_order_api()` uses raw `requests.post()`
3. **Data** (`data.py`): `BrokerData` class uses `get_httpx_client()` for quotes/depth on `base_url`
4. **Funds**: Uses httpx client for margin/funds queries

### Implementation Plan

Files to modify:
- `.env` — Add 4 new env vars
- `utils/config.py` — Add 4 getter functions
- `utils/httpx_client.py` — Add `get_proxied_httpx_client(proxy_config)` function
- `broker/kotak/api/auth_api.py` — Route through proxy when enabled
- `broker/kotak/api/order_api.py` — Route through proxy, convert requests.post to httpx
- `broker/kotak/api/data.py` — Route through proxy  
- `broker/kotak/api/funds.py` — Route through proxy (verify; may not need changes)

</specifics>

<deferred>
## Deferred Ideas

- **WebSocket proxy support** — Zero IP does not support WebSocket proxy. If Kotak WebSocket streaming also requires IP whitelisting, a separate solution is needed (e.g., SSH tunnel, VPN, or cloud relay).
- **Other broker proxy support** — Only Kotak Neo is in scope. Other brokers that require IP whitelisting (e.g., Dhan has its own API-based IP management) have different requirements.
- **UI for proxy configuration** — Adding proxy settings to the broker credentials UI is deferred. Configuration is via .env file only.
- **Auto-detection of proxy requirement** — No automatic detection of when proxy is needed. User explicitly enables via `KOTAK_PROXY_ENABLED=true`.
- **Per-endpoint proxy bypass** — Not needed for initial implementation. All Kotak REST traffic goes through proxy when enabled.
- **.sample.env or .env.example** — No dedicated .env.example file exists. Add zip IP vars as commented section in .env.

</deferred>

---

*Phase: 02-kotak-neo-proxy*
*Context gathered: 2026-07-15 via direct input*
