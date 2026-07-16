# Phase 3: Wishlist Page - Context

**Gathered:** 2026-07-16
**Status:** Ready for planning
**Source:** User request (direct)

<domain>
## Phase Boundary

Create a dedicated wishlist page where users can save and manage symbols they want to track or trade later, with notes and target prices, persisted in the database.

**Problem:** Users currently have no way to save symbols for later monitoring. They must either remember symbols or keep them in active watchlists or open orders. A wishlist provides a persistent, organized space for tracking potential trading opportunities with personal notes and target prices.

**Solution:** SQLAlchemy-backed wishlist items scoped to the current user, with REST API for CRUD operations and a React frontend page with symbol search autocomplete for adding items.

**Boundary:** No bulk operations, no sorting/filtering UI, no sharing between users, no price alerts tied to wishlist items. Pure save-and-view functionality.
</domain>

<decisions>
## Implementation Decisions

### D-01: User Scoping
- Wishlist items are scoped by `user_id` (username string from Flask session `session["user"]`)
- Only the current user can view/add/delete their own wishlist items
- Follows the exact same pattern as `database/strategy_db.py` and `database/action_center_db.py`

### D-02: Database Model Fields
- `id` (Integer, primary key)
- `user_id` (String, indexed)
- `symbol` (String, required)
- `exchange` (String, required, default "NSE")
- `notes` (String, nullable)
- `target_price` (Float, nullable)
- `created_at` (DateTime, server default)
- Unique constraint on `(user_id, symbol)` to prevent duplicate symbol entries per user

### D-03: API Endpoints
- `GET /wishlist/api/items` — List all wishlist items for current user
- `POST /wishlist/api/items` — Add a symbol (with symbol, exchange, notes, target_price)
- `DELETE /wishlist/api/items/<id>` — Remove a wishlist item
- All protected by `@check_session_validity` decorator
- Follows exact pattern from `blueprints/flow.py` and `blueprints/strategy.py`

### D-04: Symbol Search on Frontend
- Use debounced autocomplete via existing `/search/api/search?q=<query>&exchange=<exch>` endpoint
- Same pattern as `frontend/src/pages/Token.tsx` lines 212-249
- Show symbol + name in search results dropdown
- Debounce at 300ms, min 2 characters

### D-05: Frontend Page Design
- **Add Form Card** at top: search input with autocomplete dropdown, exchange selector, notes text field, target price input, "Add" button
- **Items List** below: table showing symbol, exchange, notes, target price, date added, delete button
- **Empty state**: "No symbols in your wishlist yet" centered message
- Loading spinner while fetching
- Toast notifications for add/delete success/error
- Uses shadcn/ui components: Card, Table, Button, Input, Badge

### D-06: Navigation
- Add to main sidebar nav with a Heart icon
- Add to mobile bottom navigation
- Route path: `/wishlist`

### D-07: No Edit Endpoint
- Editing is simplified to delete-and-re-add. Users who need to change notes or target price can delete and re-add the symbol.
- Avoids complexity of a PUT/PATCH endpoint for minimal added value

### the agent's Discretion
- Exact debounce timing and search result display format
- Which lucide-react icon to use (Heart, Bookmark, Star, List)
- Notes field style (inline vs modal vs expandable)
- Table column order and responsive breakpoints
- Whether to add a "Notes" column or keep it as an expandable detail row
- Whether target_price shows in the table or only as optional metadata
</decisions>

<canonical_refs>
## Canonical References

### Database Patterns
- `database/strategy_db.py` — SQLAlchemy model pattern, CRUD functions, scoped session, init_db()
- `database/action_center_db.py` — Another example of the same pattern with different fields
- `database/db_init_helper.py` — `init_db_with_logging()` for auto table creation

### API Blueprint Patterns
- `blueprints/strategy.py` — Blueprint registration, `@check_session_validity`, JSON response pattern
- `blueprints/flow.py` — Another blueprint example with CRUD endpoints

### App Registration
- `app.py` — Blueprint registration at lines ~280-328, DB init at lines ~111-128

### Frontend Patterns
- `frontend/src/pages/Token.tsx` — Debounced autocomplete symbol search (lines 212-249)
- `frontend/src/pages/ActionCenter.tsx` — Page component with Card/Table/Button pattern
- `frontend/src/pages/strategy/StrategyIndex.tsx` — Another CRUD page example
- `frontend/src/api/strategy.ts` — API client pattern with webClient
- `frontend/src/api/client.ts` — webClient (session-based) vs apiClient
- `frontend/src/App.tsx` — Lazy loaded page registration and routing
- `frontend/src/config/navigation.ts` — Nav item configuration

### Symbol Search API
- `/search/api/search?q=<query>&exchange=<exch>` — Returns `{ results: [{symbol, brsymbol, name, exchange, token, ...}] }`

### Session/Auth
- `utils/session.py` — `@check_session_validity` decorator for route protection
</canonical_refs>

<specifics>
## Specific Ideas

### Expected Workflow

1. User navigates to `/wishlist` (visible in sidebar nav)
2. Page shows empty state with "Add Symbol" form
3. User types symbol name (>=2 chars), sees autocomplete dropdown with matching symbols
4. User selects a symbol, optionally adds notes and target price, clicks "Add"
5. Symbol appears in the wishlist table below
6. User can click delete (trash icon) to remove any symbol
7. All data persists in the database across sessions

### Database Table

```python
class WishlistItem(Base):
    __tablename__ = "wishlist_items"
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), nullable=False, default="NSE")
    notes = Column(String(500), nullable=True)
    target_price = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_wishlist_user_symbol", "user_id", "symbol", unique=True),
    )
```

### Files to Create/Modify

| Layer | File | Action |
|-------|------|--------|
| Database | `database/wishlist_db.py` | Create |
| Blueprint | `blueprints/wishlist.py` | Create |
| App init | `app.py` | Edit (3 insertions) |
| Frontend API | `frontend/src/api/wishlist.ts` | Create |
| Frontend page | `frontend/src/pages/Wishlist.tsx` | Create |
| Frontend App | `frontend/src/App.tsx` | Edit (2 insertions) |
| Frontend nav | `frontend/src/config/navigation.ts` | Edit (1 insertion) |

### Existing .env Section

The `.env` file starts with broker configuration. The new env vars are not needed since wishlist has no configurable settings.
</specifics>

<deferred>
## Deferred Ideas

- **Price alerts** — Notifications when a wishlist symbol hits target price
- **Sorting/filtering** — Sort by date added, symbol, exchange; filter by exchange
- **Bulk operations** — Multi-select and delete, import from CSV
- **Notes with formatting** — Rich text notes
- **Tags/categories** — Group wishlist items by custom tags
- **Share wishlist** — Share with other users or export
- **Integration with watchlist** — Cross-reference with existing watchlist feature
</deferred>

---

*Phase: 03-wishlist-page*
*Context gathered: 2026-07-16 via codebase analysis*
