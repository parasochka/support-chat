import main


def test_app_has_retention_routes():
    paths = set(main.app.openapi()["paths"].keys())
    for p in ("/telegram/webhook/{secret}", "/api/retention/deeplink",
              "/partner/{product_id}/player-update", "/admin/retention/kb",
              "/admin/retention/photos", "/admin/retention/managers",
              "/admin/retention/telegram/{product_id}",
              "/admin/retention/webhook/{product_id}"):
        assert p in paths, p
