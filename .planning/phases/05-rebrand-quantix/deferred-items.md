# Deferred Items

## Pre-existing Lint Issues (Not Caused by Plan 05-03)

1. **app.py:47** — Import block unsorted (`ruff I001`). The entire import block (lines 47-159) is pre-existing and not touched by plan 05-03 changes.
2. **app.py:615** — Unnecessary `dict()` call (`ruff C408`). Pre-existing; `return dict(version=get_version())` was not touched by plan 05-03.

These are out-of-scope for plan 05-03 (backend rebranding). Fixing them would require restructuring app.py imports, which is not related to the rebranding task.

## Pre-existing Brand References (Not Caused by Plan 05-02)

1. **frontend/src/pages/IVChart.tsx:210** — Watermark text still reads `'OpenAlgo'`. The IV chart watermark is a visible brand element that was not included in the Plan 05-02 scope. Should be addressed in a follow-up.
