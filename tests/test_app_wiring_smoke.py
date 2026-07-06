import types

import config
import main


def test_app_has_retention_routes():
    paths = set(main.app.openapi()["paths"].keys())
    for p in ("/telegram/webhook/{secret}", "/api/retention/deeplink",
              "/partner/{product_id}/player-update", "/admin/retention/kb",
              "/admin/retention/photos", "/admin/retention/managers",
              "/admin/retention/telegram/{product_id}",
              "/admin/retention/webhook/{product_id}"):
        assert p in paths, p


def _fake_request(method, path):
    return types.SimpleNamespace(method=method,
                                 url=types.SimpleNamespace(path=path))


def test_media_upload_gets_larger_body_cap():
    # A photo upload gets the big cap; every other request keeps the JSON cap.
    up = main._body_cap_for(_fake_request("POST", "/admin/retention/photos"))
    assert up == config.RETENTION_MAX_UPLOAD_BYTES and up >= 1_000_000
    normal = main._body_cap_for(_fake_request("POST", "/admin/retention/kb"))
    assert normal < up  # the 64 KiB JSON cap
    # A GET on the media path is not an upload -> normal cap.
    assert main._body_cap_for(_fake_request("GET", "/admin/retention/photos/1/file")) == normal
