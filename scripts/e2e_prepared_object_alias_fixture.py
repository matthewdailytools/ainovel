"""Object-identity source cases only in the owned disposable E2E server."""

from pathlib import Path
from threading import Thread
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from helpers.prepared_scope_fixture import stopped
from test_prepared_source_object_aliases import AliasModel
from test_prepared_source_scope_correction import BodyModel


class FixtureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=8, max_length=128)
    mode: Literal["same", "unknown", "different", "exact", "final_unknown"] = "same"


def register_prepared_object_alias_fixture(app, settings, temporary_directory):
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
        raise RuntimeError("Object-identity fixture requires owned disposable offline settings.")
    requests = {}
    app.state.prepared_object_alias_modes = {}
    app.state.prepared_object_alias_models = {}

    @app.post("/test/prepared-object-alias/fixtures", status_code=201)
    async def seed(data: FixtureInput):
        if data.client_request_id not in requests:
            result, failures = [], []

            def create():
                try:
                    domain = app.state.domain
                    novel = domain.create_novel("后台验证 · 材料称谓关联", "原创工程替身，非文学评价。")
                    chapter = domain.create_chapter(novel["branch_id"], "寄出的副本")
                    parent, candidate = stopped(
                        settings, domain, {**novel, "chapter_id": chapter["id"]}, account="offline"
                    )
                    result.append(
                        dict(
                            novel_id=novel["id"],
                            branch_id=novel["branch_id"],
                            chapter_id=chapter["id"],
                            parent_id=parent["id"],
                            candidate_id=candidate["id"],
                        )
                    )
                except BaseException as error:
                    failures.append(error)

            thread = Thread(target=create)
            thread.start()
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Object-identity fixture seed exceeded owned worker window.")
            if failures:
                raise failures[0]
            requests[data.client_request_id] = result[0]
            app.state.prepared_object_alias_modes[result[0]["candidate_id"]] = data.mode
        return requests[data.client_request_id]


async def prepared_object_alias_result(app, account, task_id, system, prompt, max_output, **kwargs):
    task = app.state.domain.task(task_id)
    candidate = task["payload"].get("source_candidate_id")
    if candidate not in app.state.prepared_object_alias_modes:
        return None
    model = app.state.prepared_object_alias_models.get(task_id)
    if model is None:
        if task["kind"] == "prepared_source_scope_correction":
            mode = app.state.prepared_object_alias_modes[candidate]
            relation = (
                "unknown"
                if mode == "unknown"
                else "different_transfer_or_instance"
                if mode == "different"
                else "same_transfer_same_instance"
            )
            model = AliasModel(
                relation=relation,
                exact=mode == "exact",
                final_alias="unknown" if mode == "final_unknown" else "pass",
            )
        elif (
            task["kind"] == "source_corrected_candidate_recheck"
            and task["result"].get("source_corrected_recheck_version")
            == "scope-corrected-candidate-recheck/2"
        ):
            model = BodyModel()
        else:
            return None
        app.state.prepared_object_alias_models[task_id] = model
    return await model.generate(account, task_id, system, prompt, max_output, **kwargs)
