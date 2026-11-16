"""Observation recovery fixtures only in the owned disposable offline server."""

from copy import deepcopy
import json
from pathlib import Path
from threading import Thread

from pydantic import BaseModel, ConfigDict, Field

from ainovel.worker import Worker
from helpers.authored_classification_fixture import native
from helpers.author_observation_retry_fixture import known_returned_failure


class FixtureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=8, max_length=100)


def register_author_source_observation_fixture(app, settings, temporary_directory):
    folder = Path(temporary_directory).resolve()
    if (
        not settings.test_mode
        or settings.data_dir.resolve() != folder
        or not folder.is_dir()
        or not folder.name.startswith("ainovel-browser-tests-")
        or set(settings.providers) != {"offline"}
        or settings.providers["offline"].base_url != "https://example.invalid"
        or settings.providers["offline"].key != "offline-not-real"
        or settings.port in {8765, 8766}
    ):
        raise RuntimeError("Observation fixture requires owned disposable offline settings.")
    requests = {}
    app.state.observation_retry_sources = {}
    app.state.observation_retry_models = {}
    app.state.observation_retry_source_calls = {}
    app.state.observation_retry_settings = settings

    @app.post("/test/author-source-observation-retry/fixtures", status_code=201)
    async def seed(data: FixtureInput):
        if data.client_request_id not in requests:
            result, failures = [], []

            def create():
                try:
                    domain = app.state.domain
                    story = domain.create_novel("后台验证·来源引用恢复", "匿名确定性替身，不代表文学质量。")
                    chapter = domain.create_chapter(story["branch_id"], "匿名第一章")
                    story = {**story, "chapter_id": chapter["id"]}
                    _, parent, _, frames = known_returned_failure(
                        settings, domain, story, account="offline", client_request_id=data.client_request_id
                    )
                    assert len(frames) == 3
                    app.state.observation_retry_sources[parent["id"]] = deepcopy(parent)
                    with Worker(settings).budget.db() as usage:
                        app.state.observation_retry_source_calls[parent["id"]] = [
                            dict(row)
                            for row in usage.execute(
                                "SELECT * FROM calls WHERE task_id=? ORDER BY id", (parent["id"],)
                            )
                        ]
                    result.append(
                        dict(
                            novel_id=story["id"],
                            branch_id=story["branch_id"],
                            source_id=parent["id"],
                            engineering_mock=True,
                        )
                    )
                except BaseException as error:
                    failures.append(error)

            thread = Thread(target=create)
            thread.start()
            thread.join(timeout=15)
            if thread.is_alive():
                raise RuntimeError("Owned observation seed exceeded worker window.")
            if failures:
                raise failures[0]
            requests[data.client_request_id] = result[0]
        return requests[data.client_request_id]

    @app.get("/test/author-source-observation-retry/fixtures/{source_id}")
    def evidence(source_id: str):
        if source_id not in app.state.observation_retry_sources:
            raise ValueError("Unknown owned observation source")
        models = app.state.observation_retry_models
        with Worker(settings).budget.db() as usage:
            source_calls = [
                dict(row)
                for row in usage.execute("SELECT * FROM calls WHERE task_id=? ORDER BY id", (source_id,))
            ]
        reasons_in_frames = {}
        for key, value in models.items():
            task = app.state.domain.task(key)
            reason = task["result"]["author_source_observation_retry"]["request"]["reason"]
            reasons_in_frames[key] = reason in json.dumps(value["frames"], ensure_ascii=False)
        return dict(
            source_call_rows_preserved=source_calls == app.state.observation_retry_source_calls[source_id],
            source_calls=len(source_calls),
            source_call_statuses=[row["status"] for row in source_calls],
            audit_reasons_in_frames=reasons_in_frames,
            before=app.state.observation_retry_sources[source_id],
            after=app.state.domain.task(source_id),
            actions={key: [frame["任务"]["动作"] for frame in row["frames"]] for key, row in models.items()},
            engineering_mock=True,
        )


async def author_source_observation_result(app, account, task_id, system, prompt, max_output, **kwargs):
    task = app.state.domain.task(task_id)
    source = task["result"].get("continued_from")
    if source not in app.state.observation_retry_sources:
        return None
    stored = app.state.observation_retry_models.get(task_id)
    if stored is None:
        isolated = Worker(app.state.observation_retry_settings)
        frames = native(isolated)
        stored = dict(client=isolated.client, frames=frames)
        app.state.observation_retry_models[task_id] = stored
    return await stored["client"].generate(account, task_id, system, prompt, max_output, **kwargs)
