# OpenAlgo Roadmap

## Phase 1: Kotak Neo Order Placement Page

**Goal:** Dedicated manual order entry page for placing orders directly from the UI, with symbol search, real-time quotes, and full order form.

**Requirements:** [ORD-01, ORD-02, ORD-03]

**Plans:** 1 plan
- [ ] 01-01-PLAN.md — Create dedicated order placement page with symbol search, real-time quotes, and order form

---

## Requirements

### ORD-01: Manual Order Entry Page
A dedicated page at `/place-order` where users can manually enter and submit orders with symbol search, exchange selection, and full order parameters.

### ORD-02: Real-time Quote Integration
The order page displays real-time LTP, bid/ask, and market depth for the selected symbol using WebSocket with REST API fallback.

### ORD-03: Navigation Integration
The order placement page is accessible from the main navigation bar and mobile bottom navigation.
