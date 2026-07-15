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

## Requirements

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
