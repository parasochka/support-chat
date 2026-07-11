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


def reset_current_product(token) -> None:
    """Restore the scope a set_current_product() call replaced.

    Handlers never need this (each request runs in its own context); it exists
    for sync helpers that must PEEK at the global layer mid-request (e.g. the
    worker-cadence read) without clobbering the caller's product scope.
    """
    _current_product_id.reset(token)


def current_product_id() -> Optional[int]:
    return _current_product_id.get()


# The boot-seeded default product's id, recorded once at startup (db.init_db →
# _migrate_tenancy). Lets sync code ask "is this request acting on the default
# product?" without a DB round-trip — used to keep deploy-level env fallbacks
# (e.g. CONTACT_FORM_URL) from leaking into OTHER products.
_default_product_id: Optional[int] = None


def set_default_product_id(product_id: Optional[int]) -> None:
    global _default_product_id
    _default_product_id = product_id


def is_default_scope() -> bool:
    """True when the request acts on the boot-seeded default product.

    No product scope at all (tests, scripts, pre-tenancy code paths) also counts
    as default — that is exactly the single-product/pre-tenancy behaviour. A
    non-default product scope returns False, so deploy-level env fallbacks never
    leak into another partner's casino.
    """
    pid = _current_product_id.get()
    if pid is None:
        return True
    return _default_product_id is not None and pid == _default_product_id
