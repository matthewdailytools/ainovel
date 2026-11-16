"""Same-source policy cases only in the owned disposable E2E server."""

from copy import deepcopy
from pathlib import Path
from threading import Thread
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from ainovel.worker import Worker
from helpers.authored_classification_fixture import native, source_unverified
from helpers.auto_scope_classification_fixture import AutoScopeClassificationModel

AUTO_SCOPE_TAG = "auto-scope-classification-e2e"


class FixtureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=8, max_length=128)
    mode: Literal["ready", "ambiguous", "preserve_unknown", "classification_unknown"] = "ready"


def register_author_source_recheck_fixture(app, settings, temporary_directory):
    location = Path(temporary_directory).resolve()
    if (
        not settings.test_mode
        or settings.data_dir.resolve() != location
        or not location.is_dir()
        or not location.name.startswith("ainovel-browser-tests-")
        or set(settings.providers) != {"offline"}
        or settings.providers["offline"].base_url != "https://example.invalid"
        or settings.providers["offline"].key != "offline-not-real"
        or settings.port in {8765, 8766}
    ):
        raise RuntimeError("Same-source fixture requires owned disposable offline settings.")
    requests = {}
    app.state.author_source_recheck_modes = {}
    app.state.author_source_recheck_models = {}
    app.state.author_source_recheck_baselines = {}
    app.state.auto_source_classification_models = {}
    app.state.author_source_recheck_settings = settings

    @app.post("/test/author-source-recheck/fixtures", status_code=201)
    async def seed(data: FixtureInput):
        if data.client_request_id not in requests:
            result, failures = [], []

            def create():
                try:
                    domain = app.state.domain
                    story = domain.create_novel("后台验证 · 同原话新版核对", "原创匿名工程替身，非文学评价。")
                    _, parent, frames = source_unverified(
                        settings, domain, story, account="offline", client_request_id=data.client_request_id
                    )
                    assert len(frames) == 1
                    result.append(
                        dict(novel_id=story["id"], branch_id=story["branch_id"], source_id=parent["id"])
                    )
                    app.state.author_source_recheck_baselines[parent["id"]] = deepcopy(parent)
                except BaseException as error:
                    failures.append(error)

            thread = Thread(target=create)
            thread.start()
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Same-source seed exceeded owned worker window.")
            if failures:
                raise failures[0]
            requests[data.client_request_id] = result[0]
            app.state.author_source_recheck_modes[result[0]["source_id"]] = data.mode
        return requests[data.client_request_id]

    @app.get("/test/author-source-recheck/fixtures/{source_id}")
    def evidence(source_id: str):
        if source_id not in app.state.author_source_recheck_baselines:
            raise ValueError("Unknown owned source")
        children = app.state.domain.db.all(
            "SELECT id FROM tasks WHERE branch_id=? ORDER BY created",
            (app.state.author_source_recheck_baselines[source_id]["branch_id"],),
        )
        models = app.state.author_source_recheck_models
        return dict(
            before=app.state.author_source_recheck_baselines[source_id],
            after=app.state.domain.task(source_id),
            actions={
                row["id"]: [frame["任务"]["动作"] for frame in models[row["id"]]["frames"]]
                for row in children
                if row["id"] in models
            },
        )


async def author_source_recheck_result(app, account, task_id, system, prompt, max_output, **kwargs):
    task = app.state.domain.task(task_id)
    if task["payload"].get("mode") == "automatic" and task["payload"].get("brief") == AUTO_SCOPE_TAG:
        model = app.state.auto_source_classification_models.get(task_id)
        if model is None:
            model = AutoScopeClassificationModel()
            app.state.auto_source_classification_models[task_id] = model
        return await model.generate(account, task_id, system, prompt, max_output, **kwargs)
    source = task["result"].get("continued_from")
    if source not in app.state.author_source_recheck_modes:
        return None
    stored = app.state.author_source_recheck_models.get(task_id)
    if stored is None:
        mode = app.state.author_source_recheck_modes[source]
        isolated = Worker(app.state.author_source_recheck_settings)
        frames = native(
            isolated,
            ambiguous=mode == "ambiguous",
            preserve="unknown" if mode == "preserve_unknown" else "pass",
            classification_result="unknown" if mode == "classification_unknown" else "pass",
        )
        stored = dict(client=isolated.client, frames=frames)
        app.state.author_source_recheck_models[task_id] = stored
    return await stored["client"].generate(account, task_id, system, prompt, max_output, **kwargs)
