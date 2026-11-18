from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from ainovel.budget import Budget
from ainovel.db import DomainError
from ainovel import evidence_export
from ainovel.evidence_export import export_evidence
from test_seven_stage_run import Model, drain, start
from test_workflow_inspection import author


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
                author(item["check_id"], chapter) for item in preview["items"] if item["status"] == "pending"
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
            "ending": {"note": "核对本篇回收了寻找失主的承诺。"},
            "characters": {"note": "检查学徒的选择。"},
            "promises": {"note": "检查铃的去向。"},
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

    domain.save(chapter["id"], chapter["revision"], "作者另行修改了这份合成存稿。")
    history_folder, historical, run = _bundle(settings, task_id)
    delivery = run["delivery"]
    assert delivery["id"] == original_delivery["id"] and not delivery["current"]
    assert all(not row["current"] for row in run["inspections"])
    assert run["completed_chapters"][0] == {**original, "current": False}
    assert historical["calls"] == entry["calls"] and Budget(settings).report() == before
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


def test_seven_stage_evidence_rejects_missing_original_adoption_receipt(client, domain, settings, story):
    _, _, task_id = start(client, domain, story, "idea")
    drain(settings, Model(settings=settings))
    state = domain.task(task_id)["result"]["seven_stage_run"]
    artifact_id = state["completed_chapters"][0]["artifact_id"]
    with domain.db.connect(True) as tx:
        tx.execute("DELETE FROM receipts WHERE artifact_id=?", (artifact_id,))
    with pytest.raises(DomainError, match=rf"{artifact_id} 的原采纳回执"):
        export_evidence(settings, [task_id])


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
