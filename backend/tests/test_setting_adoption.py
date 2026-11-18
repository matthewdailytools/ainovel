from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from ainovel.cloud_sync import create_sync_app
from ainovel.db import Database, DomainError
from ainovel.domain import Domain
from ainovel.manuscript_exchange import ManuscriptPackage, export_manuscript, import_manuscript
from ainovel.outline_sync import version_object as outline_object
from ainovel.settings_sync import version_object as settings_object
from test_cloud_sync import ADMIN, grant, parent, push
from test_conception_workbench import confirm, item, path, save
from test_outline_sync import plan
from test_outline_workspace import chapter_plan, put_and_confirm
from test_seven_stage_run import BODY, Model, drain


RULE = "铜铃只在旧宅门内响。"
PENDING = "待核规则：铜铃在任何门外都响。"
PROPOSED = "候选规则：被雨淋过的铜铃永远无声。"


def adopt_settings(client, branch):
    endpoint = path(branch)
    state = client.get(endpoint).json()
    categories = {category["id"]: category for category in state["categories"]}
    categories["core"]["items"] = [
        {**item("kept-ending", "确认收束"), "adoption_state": "adopted"},
        {**item("pending-ending", "另一结局"), "adoption_state": "pending"},
    ]
    categories["characters"]["items"] = [
        item("actor", "主角"),
        {**item("proposed-actor", "候选人物"), "adoption_state": "proposed"},
    ]
    categories["world"]["items"] = [
        {**item("bell", "铜铃规则", ["pending-rule"]), "detail": RULE},
        {
            **item("pending-rule", "铜铃规则"),
            "detail": PENDING,
            "adoption_state": "pending",
            "setting_policy": {"strength": "hard", "scope": "所有门外"},
        },
        {
            **item("proposed-rule", "候选雨水规则"),
            "detail": PROPOSED,
            "adoption_state": "proposed",
            "setting_policy": {"strength": "hard", "scope": "所有环境"},
        },
    ]
    saved = save(client, endpoint, state)
    assert not saved["setting_findings"]
    return confirm(client, endpoint, saved)


def _outline_data(branch, chapter, setting_id):
    return {
        "endpoint": f"/api/branches/{branch}/outline-workspace",
        "data": {
            "base_revision": 0,
            "confirmation_id": setting_id,
            "plan": plan(chapter_plan(chapter, "核对门内铃声")),
        },
    }


def test_only_adopted_settings_feed_frozen_handoff_chapter_plan_and_inspection(client, domain, story):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "学徒在门内听到了铃声。")
    confirmed = adopt_settings(client, branch)
    snapshot = confirmed["confirmation"]
    assert "确认收束" in snapshot["planning_brief"] and "另一结局" not in snapshot["planning_brief"]
    setting = snapshot["id"]
    outline = _outline_data(branch, chapter["id"], setting)
    endpoint, data = outline["endpoint"], outline["data"]
    options = client.get(endpoint + "/setting-options").json()
    assert [row["id"] for row in options["items"]] == ["kept-ending", "actor", "bell"]
    world_options = client.get(endpoint + "/setting-options", params={"scope": "world"})
    assert world_options.status_code == 200, world_options.text
    assert [row["id"] for row in world_options.json()["items"]] == ["bell"]
    for identifier in ("pending-rule", "proposed-rule"):
        rejected = deepcopy(data)
        rejected["plan"]["volumes"][0]["units"][0]["chapters"][0]["setting_ids"] = [identifier]
        response = client.put(endpoint, json=rejected)
        assert response.status_code == 400 and response.json()["code"] == "outline_setting"
    data["plan"]["volumes"][0]["units"][0]["chapters"][0]["setting_ids"] = ["actor", "bell"]
    adopted = put_and_confirm(client, endpoint, data)
    request = {
        "action": "writing.advise",
        "novel_id": story["id"],
        "branch_id": branch,
        "chapter_id": chapter["id"],
        "base_revision": chapter["revision"],
        "instruction": "按当前作者设定继续。",
        "account": "openai_1",
        "conception_snapshot_id": setting,
    }
    queued = client.post("/api/modules", json={**request, "outline_revision_id": adopted["confirmed"]["id"]})
    assert queued.status_code == 202, queued.text
    frozen = queued.json()["result"]
    handoff_items = [
        rule for category in frozen["conception_snapshot"]["categories"] for rule in category["items"]
    ]
    assert [rule["id"] for rule in handoff_items] == ["kept-ending", "actor", "bell"]
    assert handoff_items[-1]["links"] == []
    assert [rule["id"] for rule in frozen["outline"]["settings"]] == ["actor", "bell"]
    assert frozen["outline"]["settings"][0]["category_id"] == "characters"
    for identifier in ("pending-rule", "proposed-rule", "actor"):
        response = client.post(
            "/api/modules", json={**request, "action": "review.chapter", "setting_item_id": identifier}
        )
        assert response.status_code == 409 and response.json()["code"] == "setting_rule"
    preview = client.get(f"/api/branches/{branch}/workflow-inspections").json()["preview"]
    rules = next(row for row in preview["items"] if row["check_id"] == "05")["basis"]
    assert [row["id"] for row in rules["rules"]] == ["bell"] and rules["tracked"] == [[chapter["id"], "bell"]]


def test_unconfirmed_draft_items_without_adopted_world_rules_do_not_block_inspection(client, domain, story):
    branch = story["branch_id"]
    domain.save(story["chapter_id"], 0, "学徒保存了当前试稿。")
    endpoint = path(branch)
    state = client.get(endpoint).json()
    world = next(category for category in state["categories"] if category["id"] == "world")
    world["items"] = [
        {**item("draft-1", "未定硬规则"), "adoption_state": "pending", "detail": PENDING},
        {**item("draft-2", "建议硬规则"), "adoption_state": "proposed", "detail": PROPOSED},
    ]

    def inspection():
        preview = client.get(f"/api/branches/{branch}/workflow-inspections").json()["preview"]
        return next(row for row in preview["items"] if row["check_id"] == "05")

    saved = save(client, endpoint, state)
    check = inspection()
    assert check["status"] == "pending" and not check["basis"]["world_needs_confirmation"]
    world = next(category for category in saved["categories"] if category["id"] == "world")
    world["items"][0]["adoption_state"] = "adopted"
    save(client, endpoint, saved)
    check = inspection()
    assert check["status"] == "outdated" and check["basis"]["world_needs_confirmation"]


def test_settings_handoff_run_never_uses_saved_nonadopted_items_as_rules(client, domain, settings, story):
    branch = story["branch_id"]
    adopted = adopt_settings(client, branch)["confirmation"]["id"]

    class ModelChecksAdoption(Model):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frame = json.loads(prompt.split("\n只返回")[0])
            instruction = frame["任务"]
            action = instruction.get("action") or instruction["动作"]
            assert PENDING not in prompt and PROPOSED not in prompt
            if action == "生成四级大纲候选":
                assert [item["id"] for item in instruction["已确认世界设定"]] == ["bell"]
            if action == "writing.expand_plot":
                assert [item["id"] for item in frame["上下文"]["采用章纲"]["settings"]] == ["bell"]
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)

    started = client.post(
        f"/api/branches/{branch}/seven-stage-runs",
        json={
            "start_from": "settings",
            "source_id": adopted,
            "account": "deepseek",
            "budget_limit": 1,
            "client_request_id": "only-adopted",
            "auto_confirm_planning": True,
            "auto_adopt_body": True,
        },
    )
    assert started.status_code == 202, started.text
    model = ModelChecksAdoption(settings=settings)
    drain(settings, model)
    execution = domain.task(started.json()["id"])
    assert execution["status"] == "needs_review", execution["error"]
    state = execution["result"]["seven_stage_run"]
    assert state["stage"] == "inspection" and state["setting_id"] == adopted
    assert model.calls[:2] == ["生成四级大纲候选", "writing.expand_plot"]
    assert domain.chapter(story["chapter_id"])["content"] == BODY


def test_manuscript_preserves_explicit_states_and_cloud_rejects_nonadopted_outline_binding(
    client, domain, story, tmp_path
):
    branch, chapter = story["branch_id"], story["chapter_id"]
    confirmed = adopt_settings(client, branch)
    put_and_confirm(client, **_outline_data(branch, chapter, confirmed["confirmation"]["id"]))
    package = export_manuscript(domain, branch)
    restored = Domain(Database(tmp_path / "restored-settings.sqlite"))
    import_manuscript(restored, package)
    recovered = export_manuscript(restored, branch)
    assert recovered.settings_history == package.settings_history
    assert recovered.outline_history == package.outline_history
    world = next(
        category for category in recovered.settings_history.versions[-1].categories if category.id == "world"
    )
    assert {rule.id: rule.adoption_state for rule in world.items} == {
        "bell": None,
        "pending-rule": "pending",
        "proposed-rule": "proposed",
    }

    overlaid = package.model_dump(mode="json")
    overlaid["outline_history"]["versions"][-1]["plan"]["volumes"][0]["units"][0]["chapters"][0][
        "setting_ids"
    ] = ["pending-rule"]
    refused = Domain(Database(tmp_path / "rejected-settings.sqlite"))
    with pytest.raises(DomainError) as rejected:
        import_manuscript(refused, ManuscriptPackage.model_validate(overlaid))
    assert rejected.value.code == "manuscript_outline" and not refused.db.all("SELECT id FROM novels")

    provisioning = ADMIN["Authorization"].split()[1]
    work = f"/api/sync/works/{branch}"
    with TestClient(create_sync_app(tmp_path / "cloud.sqlite", provisioning)) as cloud:
        author = grant(cloud, "author", work)
        push(cloud, author, parent("chapter-source", object_id=f"chapter:{chapter}"), work)
        settings = package.settings_history.versions[-1]
        push(
            cloud,
            author,
            parent(
                "settings-source",
                payload=settings_object(branch, settings).model_dump(mode="json"),
                object_id=f"settings-version:{settings.id}",
            ),
            work,
        )
        signed = package.outline_history.versions[-1]
        push(
            cloud,
            author,
            parent(
                "outline-source",
                payload=outline_object(branch, signed).model_dump(mode="json"),
                object_id=f"outline-version:{signed.id}",
            ),
            work,
        )
        for number, identifier in enumerate(("pending-rule", "proposed-rule"), 1):
            changed = outline_object(branch, signed).model_dump(mode="json")
            changed["version"]["id"] = f"bad-outline-{number}"
            changed["version"]["plan"]["volumes"][0]["units"][0]["chapters"][0]["setting_ids"] = [identifier]
            invalid = cloud.post(
                work + "/push",
                headers=author,
                json=parent(
                    f"bad-change-{number}", payload=changed, object_id=f"outline-version:bad-outline-{number}"
                ),
            )
            assert invalid.status_code == 409 and invalid.json()["code"] == "sync_outline_settings"
        assert cloud.get(work + "/checkpoint", headers=author).json()["cursor"] == 3
