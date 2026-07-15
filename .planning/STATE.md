# Project State

**Position:** Phase 1 planning
**Last Updated:** 2026-07-14

## Decisions

- Existing `broker/kotak/` plugin already implements Kotak Neo v2 API via raw httpx calls (no SDK needed)
- `PlaceOrderDialog` component exists and can be reused/adapted for the standalone page
- Symbol search API at `/search/api/search` provides typeahead functionality
- Order placement flows through `tradingApi.placeOrder()` → `/api/v1/placeorder` → broker module

## Issues

- No standalone order entry page exists — orders can only be placed from dialogs opened by other pages (OptionChain, Scalping, etc.)
