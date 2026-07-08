---
name: add-admin-endpoint
description: >-
  Add a new /admin/* API endpoint with the right multi-tenant authorization and
  admin-SPA wiring. Use when exposing a new admin/dashboard capability (read a
  metric, edit a product-scoped resource, manage something). Enforces the
  require_admin choke points, product scoping, and product_id data rules so the
  route can't leak across tenants.
---

# add-admin-endpoint

Every `/admin/*` route mounts under `router` in `api/admin.py` (or
`api/retention.py` for Telegram), which already carries
`dependencies=[Depends(require_admin)]`. Authorization is NOT "is admin
somewhere" — it's scoped to the product/partner the route touches.

## 1. Authorize through the choke points (`api/admin_auth.py`)

- **Read** route → `admin = Depends(require_admin)`, then filter data by scope
  with `resolve_scope_filter(admin, product_id, partner_id)` (returns the
  `product_ids` filter: `None` = all accessible, `[]` = none). Omitted scope =
  aggregate the caller's whole accessible scope.
- **Write** route → `admin = Depends(require_admin_write)` (coarse: role is
  `admin`, else 403) **plus** the fine check for the target:
  `await require_product_write(admin, product_id)` (product-scoped) or
  `require_global_write(admin)` (global-only). `require_admin_write` alone is
  only the pre-filter — never trust it as the authorization.
- Never invent a bare role check. `role_for_product` / `accessible_product_ids`
  are the helpers if you need custom logic.

## 2. Resolve the acting product for tenancy

Product-scoped routes take `product_id` (query or body) and the SPA sends the
header switcher selection as `product_id`/`partner_id`. Set the tenancy scope so
`settings.*()` and per-product resolution work if the handler builds prompts /
reads product settings.

## 3. Data rules

- New per-turn / per-session rows must carry `product_id` (copy from the session).
- Return JSON-safe types only: convert `datetime` → isoformat string in the
  `db._row_to_*` converter (a raw datetime 500s `JSONResponse`).
- Reads that power dashboards: support aggregates exclude `consumer='telegram'`;
  Telegram has its own `retention_*` endpoints. Don't mix them.

## 4. Wire the admin SPA (`admin/src/`)

Map the endpoint in `dataProvider.js` (or call it directly from the page), add
the page/field, and role-gate edit controls (managers are read-only; the server
is authoritative, the UI gate is cosmetic). Product-scoped surfaces wrap in
`components/RequireProduct` so they refuse to render at the all/partner scope.

## 5. Tests + verify

Add a test in the pattern of `tests/test_admin_scope.py` / `test_admin_roles.py`
/ `test_admin_auth.py`: assert a manager is 403 on write, an out-of-scope admin
can't reach another product, and the scope filter is applied. Then
`bash scripts/preflight.sh --checks`. Update the `/integration-admin` docs page
if this changes the public `/admin/*` contract.
