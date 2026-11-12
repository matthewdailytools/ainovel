from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.db import Database
from ainovel.domain import Domain
from ainovel.settings import Provider, Settings


@pytest.fixture
def settings(tmp_path):
    accounts = {
        name: Provider(name, kind, model, url, "test-not-real", Decimal("2"), Decimal("8"))
        for name, kind, model, url in [
            ("openai_1", "openai", "test-model", "https://api.openai.com/v1"),
            ("openai_2", "openai", "test-model", "https://api.openai.com/v1"),
            ("deepseek", "deepseek", "test-model", "https://api.deepseek.com"),
        ]
    }
    return Settings(tmp_path, accounts, {"openai": Decimal("20"), "deepseek": Decimal("80")}, test_mode=True)


@pytest.fixture
def domain(settings):
    return Domain(Database(settings.data_dir / "manuscripts.sqlite"))


@pytest.fixture
def story(domain):
    novel = domain.create_novel("原创测试作品", "仅用于工程验证，不是创作质量验收。")
    chapter = domain.create_chapter(novel["branch_id"], "第一章")
    return {**novel, "chapter_id": chapter["id"]}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        session = client.get("/api/session").json()
        client.headers["X-Ainovel-CSRF"] = session["csrf"]
        yield client
