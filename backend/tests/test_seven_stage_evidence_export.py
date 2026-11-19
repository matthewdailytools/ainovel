from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from ainovel.budget import Budget
from ainovel.db import DomainError, uid
from ainovel import evidence_export
from ainovel.evidence_export import export_evidence
from test_seven_stage_run import Model, drain, start
from test_workflow_inspection import author, source


def _bundle(settings, task_id):
    folder = Path(export_evidence(settings, [task_id])["folder"])
    evidence = json.loads((folder / "evidence.json").read_text("utf-8"))
    assert evidence["restore"]["integrity"] == "ok"
    assert evidence["human_creative_acceptance"] == "unverified"
    assert evidence["human_benefit_acceptance"] == "unverified"
    entry = evidence["tasks"][0]
    assert "completion" not in entry
    return folder, entry, entry["seven_stage"]


def test_seven_stage_evidence_pins_original_reviews_and_delivery_after_new_save(
    client, domain, settings, story
):
    branch, endpoint, task_id = start(client, domain, story, "idea")
    model = Model(settings=settings)
    drain(settings, model)
    base = f"/api/branches/{branch}"
    resume = f"{endpoint}/{task_id}/resume"
    state = domain.task(task_id)["result"]["seven_stage_run"]
    chapter = domain.chapter(state["completed_chapters"][0]["id"])
    inspection_url = base + "/workflow-inspections"
    completion_url = base + "/book-completion"

    pending_folder, pending, run = _bundle(settings, task_id)
    assert pending["task"]["status"] == "needs_review" and run["stage"] == "inspection"
    assert run["delivery"] is None and list(pending_folder.glob("*.zip")) == []
    original = run["completed_chapters"][0]
    assert original["chapter_id"] == chapter["id"] and original["revision"] == chapter["revision"]
    assert original["adoption_receipt"]["revision"] == chapter["revision"]
    assert original["sha256"] == sha256(chapter["content"].encode()).hexdigest()
    assert run["inspections"][0]["id"] == state["inspection_id"] and run["inspections"][0]["current"]
    assert run["dependencies"]["outline"]["id"] == state["outline_id"]
    assert run["dependencies"]["outline"]["setting_basis"]["confirmation_id"] == state["setting_id"]

    preview = client.get(inspection_url).json()["preview"]
    response = client.post(
        inspection_url,
        json={
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [
                author(item["check_id"], chapter) | {"reason": "只留在本机体检的核对说明。"}
                for item in preview["items"]
                if item["status"] == "pending"
            ],
        },
    )
    assert response.status_code == 201, response.text
    original_inspection = response.json()
    assert client.post(resume).status_code == 200
    drain(settings, model)
    assert domain.task(task_id)["result"]["seven_stage_run"]["stage"] == "completion"
    completion = client.post(
        completion_url + "/confirm",
        json={
            "basis_sha256": client.get(completion_url).json()["basis_sha256"],
            "read_note": "在合成样本中确认保留通读记录。",
            "author_decisions": {
                "reading": "verified",
                "ending": "verified",
                "characters": "verified",
                "promises": "verified",
            },
            "ending": {"note": "核对本篇回收了寻找失主的承诺。", "sources": [source(chapter)]},
            "characters": {"note": "检查学徒的选择。", "sources": [source(chapter)]},
            "promises": {"note": "检查铃的去向。", "sources": [source(chapter)]},
            "intro": "学徒循着旧铃寻找它的主人。",
            "spoiler_scope": "opening",
            "sample_chapter_ids": [chapter["id"]],
            "materials_reviewed": True,
        },
    )
    assert completion.status_code == 201, completion.text
    original_delivery = completion.json()
    official_zip = client.get(f"{completion_url}/{original_delivery['id']}/export")
    assert official_zip.status_code == 200
    assert client.post(resume).status_code == 200
    drain(settings, model)
    before = Budget(settings).report()

    folder, entry, run = _bundle(settings, task_id)
    assert entry["task"]["status"] == "succeeded" and run["stage"] == "done"
    exported = run["delivery"]
    assert exported["current"] and exported["id"] == original_delivery["id"]
    assert exported["manifest"] == original_delivery["manifest"]
    assert exported["manifest"]["inspection_id"] == original_inspection["id"]
    report = next(row for row in run["inspections"] if row["id"] == original_inspection["id"])
    assert report["current"] and report["report"]["basis_sha256"] == original_inspection["basis_sha256"]
    assert report["chapter_bindings"][0]["revision_id"] == original["id"]
    assert any(item.get("author_review", {}).get("evidence") for item in report["report"]["items"])
    assert entry["calls"] and entry["cost_by_currency"]["CNY"]["settled"] > 0
    bundle_zip = (folder / exported["zip"]["file"]).read_bytes()
    assert exported["zip"]["sha256"] == sha256(bundle_zip).hexdigest()

    edited_text = "作者另行修改了这份合成存稿。"
    edited_title = "批次完成后另取的标题"
    domain.save(chapter["id"], chapter["revision"], edited_text)
    assert client.patch(f"/api/chapters/{chapter['id']}", json={"title": edited_title}).status_code == 200
    history_folder, historical, run = _bundle(settings, task_id)
    delivery = run["delivery"]
    assert delivery["id"] == original_delivery["id"] and not delivery["current"]
    assert all(not row["current"] for row in run["inspections"])
    assert run["completed_chapters"][0] == {**original, "current": False}
    assert historical["calls"] == entry["calls"] and Budget(settings).report() == before
    frozen_file = history_folder / f"{task_id}.md"
    frozen = frozen_file.read_text("utf-8")
    assert original["content"] in frozen and chapter["title"] in frozen
    assert original["id"] in frozen and original["sha256"] in frozen
    assert edited_text not in frozen and edited_title not in frozen
    assert historical["manuscript_sha256"] == sha256(frozen_file.read_bytes()).hexdigest()
    context = historical["work_context"]
    current_file = history_folder / context["manuscript"]["file"]
    assert context["scope"] == "branch_diagnostics"
    assert edited_text in current_file.read_text("utf-8") and edited_title in current_file.read_text("utf-8")
    assert context["manuscript"]["sha256"] == sha256(current_file.read_bytes()).hexdigest()
    historical_zip = (history_folder / delivery["zip"]["file"]).read_bytes()
    with (
        ZipFile(BytesIO(official_zip.content)) as official,
        ZipFile(BytesIO(bundle_zip)) as current,
        ZipFile(BytesIO(historical_zip)) as history,
    ):
        assert set(official.namelist()) == set(current.namelist()) == set(history.namelist())
        assert len(official.namelist()) == 5
        for name in official.namelist():
            assert official.read(name) == current.read(name) == history.read(name)
    markdown = (history_folder / f"{task_id}-修订报告.md").read_text("utf-8")
    assert "历史依据" in markdown and "三层正式验收" not in markdown

    shared_folder = Path(export_evidence(settings, [task_id], batch_only=True)["folder"])
    raw = (shared_folder / "evidence.json").read_text("utf-8")
    share = json.loads(raw)
    assert share["format"] == "seven-stage-batch-evidence/1"
    assert "backup" not in share and share["restore"] == {"integrity": "ok"}
    shared = share["tasks"][0]
    assert shared["task"] == {"id": task_id, "status": "succeeded", "start_from": "idea"}
    assert shared["manuscript_sha256"] == historical["manuscript_sha256"]
    assert shared["seven_stage"]["completed_chapters"][0]["sha256"] == original["sha256"]
    shared_inspection = next(
        row for row in shared["seven_stage"]["inspections"] if row["id"] == original_inspection["id"]
    )
    assert shared_inspection["report"]["basis_sha256"] == original_inspection["basis_sha256"]
    assert shared_inspection["chapter_bindings"] == report["chapter_bindings"]
    assert len(shared["seven_stage"]["delivery"]["checks"]) == 6
    assert shared["seven_stage"]["delivery"]["manifest"]["inspection_id"] == original_inspection["id"]
    assert (shared_folder / frozen_file.name).read_bytes() == frozen_file.read_bytes()
    assert (shared_folder / delivery["zip"]["file"]).read_bytes() == historical_zip
    assert {file.name for file in shared_folder.iterdir()} == {
        "README.md",
        "evidence.json",
        frozen_file.name,
        delivery["zip"]["file"],
        f"{task_id}-修订报告.md",
    }
    assert all(fragment not in raw for fragment in (edited_text, edited_title, "只留在本机体检的核对说明。"))
    assert Budget(settings).report() == before


def test_batch_markdown_uses_only_metadata_bound_to_the_exact_original_revision():
    digest = sha256(b"shared content").hexdigest()
    chapter = {"id": "c1", "revision": 1, "sha256": digest}
    title = "历史标题"
    metadata = {**chapter, "title": title, "position": 4}
    binding = {**chapter, "revision_id": "different-chain"}
    run = {
        "completed_chapters": [
            {**chapter, "chapter_id": chapter["id"], "id": "batch-source", "content": "shared content"}
        ],
        "inspections": [{"report": {"chapters": [metadata]}, "chapter_bindings": [binding]}],
        "delivery": None,
    }
    unbound = evidence_export._batch_manuscript({"id": "batch"}, run)
    assert title not in unbound and "第 1 条章稿" in unbound and "shared content" in unbound
    binding["revision_id"] = "batch-source"
    assert "第 4 章 · 历史标题" in evidence_export._batch_manuscript({"id": "batch"}, run)


@pytest.mark.parametrize("batch_only", [False, True])
def test_seven_stage_evidence_rejects_unverified_original_adoption_receipt(
    client, domain, settings, story, batch_only
):
    _, _, task_id = start(client, domain, story, "idea")
    drain(settings, Model(settings=settings))
    state = domain.task(task_id)["result"]["seven_stage_run"]
    artifact_id = state["completed_chapters"][0]["artifact_id"]
    with domain.db.connect(True) as tx:
        if batch_only:
            tx.execute(
                "UPDATE receipts SET result=json_set(result,'$.chapter_id','wrong-chapter') WHERE artifact_id=?",
                (artifact_id,),
            )
        else:
            tx.execute("DELETE FROM receipts WHERE artifact_id=?", (artifact_id,))
    with pytest.raises(DomainError, match=rf"{artifact_id} 的原采纳回执"):
        export_evidence(settings, [task_id], batch_only=batch_only)
    assert list((settings.data_dir / "acceptance").iterdir()) == []


def test_seven_stage_backup_ledger_keeps_same_branch_batches_separate(
    client, domain, settings, story, monkeypatch, capsys
):
    branch, endpoint, first_id = start(client, domain, story, "idea")
    assert client.post(f"{endpoint}/{first_id}/pause").json()["status"] == "paused"
    assert client.post(f"{endpoint}/{first_id}/resume").json()["status"] == "queued"
    drain(settings, Model())
    first_state = domain.task(first_id)["result"]["seven_stage_run"]
    assert first_state["stage"] == "inspection"
    assert client.post(f"{endpoint}/{first_id}/cancel").json()["status"] == "cancelled"

    queued = client.post(
        endpoint,
        json={
            "start_from": "outline",
            "source_id": first_state["outline_id"],
            "account": "deepseek",
            "budget_limit": 1,
            "client_request_id": uid(),
        },
    )
    assert queued.status_code == 202, queued.text
    second_id = queued.json()["id"]
    manual = domain.queue(
        "module",
        {
            "account": "deepseek",
            "novel_id": domain.task(first_id)["novel_id"],
            "brief": "同作品另一任务的创作输入",
        },
        branch,
    )

    ledger = Budget(settings)
    provider = settings.providers["deepseek"]
    first_call = ledger.reserve(provider, first_state["children"][0], 10, 10)
    ledger.metadata(first_call, "试跑模型", "本批服务方私有请求标识")
    ledger.settle(first_call, {"input": 2, "output": 1})
    second_call = ledger.reserve(provider, second_id, 10, 10)
    manual_call = ledger.reserve(provider, manual["id"], 10, 10)
    ledger.metadata(manual_call, "manual", "非当前七台批次的请求")
    before = ledger.report()

    folder = Path(export_evidence(settings, [first_id, second_id])["folder"])
    first, second = json.loads((folder / "evidence.json").read_text("utf-8"))["tasks"]
    assert [row["id"] for row in first["calls"]] == [first_call]
    assert [row["id"] for row in second["calls"]] == [second_call]
    assert first["billing_evidence"] == {
        "source": "data_directory_backup",
        "task_ids": [first_id, *first_state["children"]],
        "state": "captured",
        "row_count": 1,
    }
    assert second["billing_evidence"]["task_ids"] == [second_id]
    assert first["cost_by_currency"]["CNY"] == {"settled": 0.000012, "held_unknown": 0}
    assert second["cost_by_currency"]["CNY"] == {"settled": 0, "held_unknown": 0.0001}
    for item in (first, second):
        assert "related_tasks" not in item and "artifacts" not in item
        assert {first_id, second_id, manual["id"]} <= {
            task["id"] for task in item["work_context"]["related_tasks"]
        }
    first_chapter = first["seven_stage"]["completed_chapters"][0]["content"]
    pending = (folder / f"{second_id}.md").read_text("utf-8")
    assert "此批次尚未记录已保存章稿" in pending and first_chapter not in pending
    assert first_chapter in (folder / second["work_context"]["manuscript"]["file"]).read_text("utf-8")
    assert first["calls"][0]["request_id"] == "本批服务方私有请求标识"

    monkeypatch.setattr("sys.argv", ["evidence_export", "--task", first_id, "--batch-only"])
    monkeypatch.setattr(evidence_export.Settings, "load", lambda: settings)
    evidence_export.main()
    scoped = Path(json.loads(capsys.readouterr().out)["folder"])
    share_raw = (scoped / "evidence.json").read_text("utf-8")
    share = json.loads(share_raw)["tasks"][0]
    assert share["billing_evidence"] == first["billing_evidence"]
    assert share["cost_by_currency"] == first["cost_by_currency"]
    assert [call["id"] for call in share["calls"]] == [first_call]
    assert share["calls"][0]["actual_model"] == "试跑模型"
    assert all(
        value not in share_raw
        for value in (
            second_id,
            manual["id"],
            second_call,
            manual_call,
            "同作品另一任务的创作输入",
            "本批服务方私有请求标识",
            "work_context",
            '"account"',
        )
    )
    assert not list(scoped.glob("*.zip"))
    assert ledger.report() == before

    before_paths = set(scoped.parent.iterdir())
    with pytest.raises(DomainError, match="须指定一个七台父批次"):
        export_evidence(settings, [first_id, second_id], batch_only=True)
    with pytest.raises(DomainError, match="适用于七台父批次"):
        export_evidence(settings, [manual["id"]], batch_only=True)
    assert set(scoped.parent.iterdir()) == before_paths


def test_source_snapshots_are_gated_and_isolated_evidence_unpack_does_not_hold_gate(
    client, domain, settings, story, monkeypatch
):
    from ainovel.runtime import data_directory_gate

    _, _, task_id = start(client, domain, story, "idea")
    open_database = evidence_export.Database
    make_backup = evidence_export.create_backup
    unpack_backup = evidence_export.unpack_verified
    observed = []

    def assert_gate_held(step):
        with pytest.raises(DomainError) as blocked, data_directory_gate(settings.data_dir):
            pytest.fail("The live data directory was not locked")
        assert blocked.value.code == "runtime_busy"
        observed.append(step)

    def open_checked(path):
        if path == domain.db.path:
            assert_gate_held("open")
        return open_database(path)

    def backup_checked(*args):
        assert_gate_held("backup")
        return make_backup(*args)

    def unpack_checked(*args):
        with data_directory_gate(settings.data_dir):
            observed.append("unpack")
        return unpack_backup(*args)

    monkeypatch.setattr(evidence_export, "Database", open_checked)
    monkeypatch.setattr(evidence_export, "create_backup", backup_checked)
    monkeypatch.setattr(evidence_export, "unpack_verified", unpack_checked)
    assert export_evidence(settings, [task_id])["restore"]["integrity"] == "ok"
    assert observed == ["open", "backup", "unpack"]


def test_seven_stage_shared_ledger_filters_families_in_one_read_snapshot(
    client, domain, settings, story, monkeypatch
):
    branch, _, task_id = start(client, domain, story, "idea")
    drain(settings, Model(settings=settings))
    state = domain.task(task_id)["result"]["seven_stage_run"]
    child = state["children"][0]
    _, _, second_id = start(client, domain, story, "idea")
    manual = domain.queue(
        "module", {"account": "deepseek", "novel_id": domain.task(task_id)["novel_id"]}, branch
    )
    # Reproduce the durable queue between claim and the parent's next checkpoint.
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
    ahead_of_checkpoint = domain.queue(
        "module",
        {
            "account": "deepseek",
            "novel_id": domain.task(task_id)["novel_id"],
            "branch_id": branch,
            "seven_stage_run_id": task_id,
            "client_request_id": f"seven-stage-{task_id}-interrupted-before-parent-checkpoint",
        },
        branch,
    )
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='needs_review' WHERE id=?", (task_id,))

    billing_dir = settings.data_dir / "canonical-billing-synthetic"
    shared = Budget(replace(settings, data_dir=billing_dir))
    provider = settings.providers["deepseek"]
    settled = shared.reserve(provider, child, 10, 10)
    shared.metadata(settled, "试跑模型", "本批精确外部请求编号")
    shared.settle(settled, {"input": 2, "output": 1})
    held = shared.reserve(provider, task_id, 10, 10)
    interrupted = shared.reserve(provider, ahead_of_checkpoint["id"], 10, 10)
    excluded_manual = shared.reserve(provider, manual["id"], 10, 10)
    excluded_foreign = shared.reserve(provider, "另一个合成作品的任务", 10, 10)
    shared.metadata(excluded_manual, "manual", "同支但非本批请求编号")
    shared.metadata(excluded_foreign, "foreign", "其他作品请求编号")

    read_calls = evidence_export._ledger_calls
    added = []

    def after_first_read(usage, identifiers):
        result = read_calls(usage, identifiers)
        if identifiers[0] == task_id:
            added.append(shared.reserve(provider, second_id, 10, 10))
        return result

    monkeypatch.setattr(evidence_export, "_ledger_calls", after_first_read)
    result = export_evidence(settings, [task_id, second_id], usage_ledger=shared.path)
    raw = (Path(result["folder"]) / "evidence.json").read_text("utf-8")
    first, second = json.loads(raw)["tasks"]
    assert first["billing_evidence"] == {
        "source": "shared_ledger",
        "task_ids": [task_id, *state["children"], ahead_of_checkpoint["id"]],
        "state": "captured",
        "row_count": 3,
    }
    assert first["seven_stage"]["unrecorded_child_task_ids"] == [ahead_of_checkpoint["id"]]
    assert {row["id"] for row in first["calls"]} == {settled, held, interrupted}
    assert next(row for row in first["calls"] if row["id"] == settled)["request_id"] == "本批精确外部请求编号"
    assert first["cost_by_currency"]["CNY"] == {"settled": 0.000012, "held_unknown": 0.0002}
    assert second["billing_evidence"]["state"] == "no_matching_calls" and second["calls"] == []
    assert len(added) == 1
    assert all(value not in raw for value in (billing_dir.name, excluded_manual, excluded_foreign, added[0]))
    assert "同支但非本批请求编号" not in raw and "其他作品请求编号" not in raw
    with shared.db() as connection:
        assert {row["id"] for row in connection.execute("SELECT id FROM calls")} == {
            settled,
            held,
            interrupted,
            excluded_manual,
            excluded_foreign,
            added[0],
        }
