"""Per-request tenant scope: which PRODUCT this request is acting on.

The service is multi-tenant (partners own casino products); almost every
subsystem — settings, prompt variables, translations, KB, language set, the
OpenAI keys — resolves per product. Threading a `product_id` argument through
every call chain would touch dozens of signatures, so the request's product is
carried in a `ContextVar` instead: the API layer sets it once (from the widget
key on the public chat routes, from the session row on per-session routes, or
from the admin's selected scope on /admin routes) and the sync settings getters
read it transparently.

`None` means "no product scope": resolution then stops at the global layer
(app_settings → env → built-in default), which is exactly the pre-tenancy
behaviour — so code paths and tests that never set a scope are unchanged.

ContextVars are task-local, so concurrent requests never observe each other's
scope. Handlers only ever SET the value (each request runs in its own context;
no reset needed).
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_current_product_id: ContextVar[Optional[int]] = ContextVar(
    "current_product_id", default=None
)

# Boot-seeded default tenant (see db._migrate_tenancy): pre-tenancy data is
# wrapped into this partner/product, and a widget that sends no widget_key
# lands here — so a single-product deployment keeps working unchanged.
DEFAULT_PARTNER_SLUG = "default"
DEFAULT_PRODUCT_SLUG = "default"


def set_current_product(product_id: Optional[int]):
    """Bind the request to a product. Returns the contextvar token."""
    return _current_product_id.set(product_id)


def current_product_id() -> Optional[int]:
    return _current_product_id.get()
