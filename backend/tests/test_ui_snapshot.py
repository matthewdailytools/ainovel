from dataclasses import replace

from fastapi.testclient import TestClient

from ainovel.api import create_app


def test_running_daily_ui_is_unchanged_by_new_frontend_build(settings, tmp_path, monkeypatch):
    from ainovel import api

    source = tmp_path / "source"
    dist = source / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html>原版本</html>", encoding="utf-8")
    (assets / "app.js").write_text("original-ui", encoding="utf-8")
    monkeypatch.setattr(api, "ROOT", source)
    daily = replace(settings, test_mode=False)
    app = create_app(daily)
    (dist / "index.html").write_text("<html>新构建</html>", encoding="utf-8")
    (assets / "app.js").write_text("new-ui", encoding="utf-8")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/").text == "<html>原版本</html>"
        assert client.get("/assets/app.js").text == "original-ui"
    with TestClient(create_app(daily), base_url="http://127.0.0.1:8765") as client:
        assert client.get("/").text == "<html>新构建</html>"
        assert client.get("/assets/app.js").text == "new-ui"
