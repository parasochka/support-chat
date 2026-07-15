#!/usr/bin/env python3
"""Export a full admin-panel snapshot of ONE product to a single JSON file.

Pulls everything an operator can edit for a product — settings (global +
product layers), prompt variables (support + retention), translations, KB
topics + texts + variables, site map, the retention KB document, idle rules,
the test profile and both effective-prompt previews — so the data can be
anonymized offline and turned into shipped defaults.

Read-only: only GET endpoints are called. Needs a service admin key (sak_…)
or a human admin JWT with read access to the product.

Usage:
    python3 scripts/export_product_snapshot.py \
        --api https://your-deploy.example.app \
        --key sak_... \
        --product nikabet \
        --out nikabet_snapshot.json
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def get(api: str, key: str, path: str, params: dict | None = None):
    url = api.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        return {"_error": e.code, "_path": path, "_body": body}
    except Exception as e:  # noqa: BLE001 - report and keep exporting
        return {"_error": str(e), "_path": path}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", required=True, help="Base URL of the deployment")
    ap.add_argument("--key", required=True, help="sak_… service key or admin JWT")
    ap.add_argument("--product", default="nikabet",
                    help="Product name substring (case-insensitive) to export")
    ap.add_argument("--out", default="product_snapshot.json")
    args = ap.parse_args()
    api, key = args.api, args.key

    snap: dict = {"api": api, "requested_product": args.product}

    structure = get(api, key, "/admin/structure")
    snap["structure"] = structure
    if "_error" in structure:
        print(f"FAILED to read /admin/structure: {structure}", file=sys.stderr)
        return 1

    # Find the product by name substring across the structure payload.
    products = []
    for partner in structure.get("partners", []):
        for prod in partner.get("products", []):
            products.append(prod)
    if not products and isinstance(structure.get("products"), list):
        products = structure["products"]
    needle = args.product.lower()
    matches = [p for p in products if needle in (p.get("name") or "").lower()]
    if not matches:
        print(f"No product matching {args.product!r}. Available: "
              f"{[p.get('name') for p in products]}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"Multiple products match {args.product!r}: "
              f"{[p.get('name') for p in matches]} — using the first.", file=sys.stderr)
    product = matches[0]
    pid = product["id"]
    snap["product"] = product
    print(f"Exporting product {product.get('name')!r} (id={pid})", file=sys.stderr)

    p = {"product_id": pid}
    sections = {
        "meta": ("/admin/meta", None),
        "settings_global": ("/admin/settings", None),
        "settings_product": ("/admin/settings", p),
        "prompt_variables": ("/admin/prompt-variables", p),
        "retention_prompt_variables": ("/admin/retention/prompt-variables", p),
        "translations": ("/admin/translations", p),
        "translations_global": ("/admin/translations", None),
        "kb_topics": ("/admin/kb/topics", p),
        "kb_variables": ("/admin/kb/variables", p),
        "site_map": ("/admin/site-map", p),
        "retention_kb_text": ("/admin/retention/kb/text", p),
        "retention_idle_rules": ("/admin/retention/idle/rules", p),
        "test_profile": ("/admin/test-profile", p),
        "effective_prompt": ("/admin/effective-prompt", p),
        "retention_effective_prompt": ("/admin/retention/effective-prompt", p),
    }
    for name, (path, params) in sections.items():
        print(f"  {name} …", file=sys.stderr)
        snap[name] = get(api, key, path, params)

    # Per-topic KB texts.
    topics = snap.get("kb_topics")
    kb_content: dict = {}
    topic_rows = topics if isinstance(topics, list) else (topics or {}).get("topics", [])
    for t in topic_rows or []:
        tid = t.get("id")
        if tid is None:
            continue
        print(f"  kb content for topic {t.get('slug') or tid} …", file=sys.stderr)
        kb_content[str(tid)] = get(api, key, "/admin/kb/content",
                                   {"topic_id": tid, "product_id": pid})
    snap["kb_content_by_topic"] = kb_content

    errors = [k for k, v in snap.items()
              if isinstance(v, dict) and "_error" in v]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"Wrote {args.out}", file=sys.stderr)
    if errors:
        print(f"NOTE: {len(errors)} section(s) failed and were saved with an "
              f"_error marker: {errors}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
