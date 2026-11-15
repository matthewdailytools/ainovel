"""Seed source-review scenarios only in the disposable browser test server."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from helpers.source_correction_fixture import create_source_correction_fixture


class FixtureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=8, max_length=128)


def register_source_correction_fixture(app, settings, temporary_directory):
    location = Path(temporary_directory).resolve()
    if (
        not settings.test_mode
        or settings.data_dir.resolve() != location
        or not location.is_dir()
        or not location.name.startswith("ainovel-browser-tests-")
        or "offline" not in settings.providers
    ):
        raise RuntimeError("来源解释测试入口仅允许独立离线浏览器临时目录。")
    requests = {}

    @app.post("/test/source-correction/fixtures", status_code=201)
    async def seed(data: FixtureInput):
        # Do not yield between queueing the synthetic parent and freezing its
        # stopped checkpoint: the test worker runs on this same event loop.
        if data.client_request_id not in requests:
            requests[data.client_request_id] = create_source_correction_fixture(
                app.state.domain, test_mode=True, account="offline"
            )
        return requests[data.client_request_id]
