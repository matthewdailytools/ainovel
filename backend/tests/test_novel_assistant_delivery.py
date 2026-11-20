import asyncio

from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.worker import Worker
from test_assistant import Model
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_reference_flow import reference as reference_fixture
from test_work_snapshot import verify

reference = reference_fixture


def endpoint(branch):
    return f"/api/branches/{branch}/novel-assistant"


def queue_reply(client, settings, branch, stage, scope_id, cards, references=()):
    route = endpoint(branch)
    queued = verify(
        client.post(
            route + "/turns",
            json={
                "stage": stage,
                "scope_id": scope_id,
                "base_revision": 0,
                "question": "请整理这本书可以再核对的写作安排。",
                "account": "deepseek",
                "client_request_id": "delivery-context-" + branch,
                "references": list(references),
            },
        ),
        202,
    )
    model = Model([{"answer": "有可以分开核对的建议。", "cards": cards}])
    assert asyncio.run(Worker(settings, model).run_once())
    [turn] = verify(client.get(route))["turns"]
    assert turn["id"] == queued["turn"]["id"]
    assert turn["status"] == "succeeded", turn
    return turn


def proposal(destination, field, value):
    return {
        "destination": destination,
        "field": field,
        "title": "供作者核对",
        "value": value,
        "reason": "保留依据。",
    }


def request(turn, card, **edits):
    return {
        "turn_id": turn["id"],
        "card_id": card["id"],
        **{key: card[key] for key in ("stage", "scope_id", "field", "value")},
        **edits,
    }


def preview(client, route, data):
    return verify(client.post(route + "/preview", json=data))


def send(client, route, data, against):
    return verify(client.post(route, json={**data, "basis_sha256": against["basis_sha256"]}), 201)


def outline(client, branch, chapter):
    return put_and_confirm(
        client,
        f"/api/branches/{branch}/outline-workspace",
        {
            "base_revision": 0,
            "confirmation_id": setting_confirmation(client, branch),
            "plan": {
                "summary": "查找铜铃",
                "volumes": [
                    {
                        "id": "volume",
                        "title": "初见铜铃",
                        "summary": "循线寻找",
                        "units": [
                            {
                                "id": "unit",
                                "title": "问门人",
                                "summary": "向门人打听铃声",
                                "chapters": [chapter_plan(chapter, "识别铃声")],
                            }
                        ],
                    }
                ],
                "promises": [],
            },
        },
    )


def test_one_reply_delivers_author_edited_text_to_two_module_inboxes_without_writing_drafts(
    client, domain, settings, story
):
    branch = story["branch_id"]
    outline(client, branch, story["chapter_id"])
    turn = queue_reply(
        client,
        settings,
        branch,
        "M3",
        "bell",
        [proposal("D1", "detail", "门内钟声只传到门槛。"), proposal("D2", "summary", "先问来处，再听铃声。")],
        [{"stage": "M4", "scope_id": "unit"}],
    )
    settings_path = f"/api/branches/{branch}/conception"
    outline_path = f"/api/branches/{branch}/outline-workspace"
    setting_before, outline_before = verify(client.get(settings_path)), verify(client.get(outline_path))
    deliveries = endpoint(branch) + "/deliveries"
    setting_card, outline_card = turn["result"]["cards"]
    setting = request(turn, setting_card, value="作者改稿：铃声仅在闭门后可闻。")
    plan = request(turn, outline_card)
    settings_preview = preview(client, deliveries, setting)
    plan_preview = preview(client, deliveries, plan)
    assert settings_preview["before"] == "铜铃只在门内响。"
    assert plan_preview["before"] == "向门人打听铃声"
    assert settings_preview["target_label"].endswith("铃声规则")
    assert plan_preview["target_label"] == "单元纲 · 问门人"

    same_set = send(client, deliveries, setting, settings_preview)
    same_outline = send(client, deliveries, plan, plan_preview)
    assert send(client, deliveries, setting, settings_preview) == same_set
    assert same_set["turn_id"] == same_outline["turn_id"] == turn["id"]
    assert same_set["value"] == setting["value"]
    assert same_set["basis_current"] and same_outline["basis_current"]
    assert verify(client.get(settings_path)) == setting_before
    assert verify(client.get(outline_path)) == outline_before
    assert verify(client.get(endpoint(branch)))["turns"][0] == turn
    with TestClient(create_app(settings)) as reopened:
        assert verify(reopened.get(deliveries, params={"stage": "M3"})) == {"deliveries": [same_set]}
        assert verify(reopened.get(deliveries + f"/{same_outline['id']}")) == same_outline


def test_same_destination_repreview_versions_are_linear_and_reject_archived_or_cross_branch_targets(
    client, domain, settings, story
):
    branch, chapter = story["branch_id"], story["chapter_id"]
    domain.save(chapter, 0, "原稿：铃声未响。")
    target = domain.create_chapter(branch, "同书第二章")["id"]
    foreign = domain.create_novel("另一作品", "")["branch_id"]
    foreign_chapter = domain.create_chapter(foreign, "外部章节")["id"]
    turn = queue_reply(
        client, settings, branch, "M5", chapter, [proposal("D1", "text", "建议：钟外有脚步声。")]
    )
    [card] = turn["result"]["cards"]
    deliveries = endpoint(branch) + "/deliveries"
    source_delivery = request(turn, card)
    assert preview(client, deliveries, source_delivery)["before"] == "原稿：铃声未响。"
    assert (
        client.post(
            deliveries + "/preview", json={**source_delivery, "scope_id": foreign_chapter}
        ).status_code
        == 404
    )
    assert client.post(endpoint(foreign) + "/deliveries/preview", json=source_delivery).status_code == 404
    assert (
        client.post(deliveries + "/preview", json={**source_delivery, "field": "selection"}).status_code
        == 400
    )
    assert (
        client.post(deliveries + "/preview", json={**source_delivery, "value": ["不是文字"]}).status_code
        == 422
    )
    assert (
        client.post(deliveries + "/preview", json={**source_delivery, "card_id": "invented-card"}).status_code
        == 404
    )

    data = request(turn, card, scope_id=target, value="作者第一次送达。")
    first_preview = preview(client, deliveries, data)
    alternative = request(turn, card, scope_id=target, value="另一窗口的作者修改。")
    competing_preview = preview(client, deliveries, alternative)
    first = send(client, deliveries, data, first_preview)
    assert first["before"] == "" and first["basis_current"]
    stale = client.post(deliveries, json={**alternative, "basis_sha256": competing_preview["basis_sha256"]})
    assert stale.status_code == 409 and stale.json()["code"] == "novel_assistant_delivery_stale"
    wrong_text = client.post(deliveries, json={**alternative, "basis_sha256": first["basis_sha256"]})
    assert wrong_text.status_code == 409

    second_preview = preview(client, deliveries, alternative)
    domain.save(target, 0, "作者另写第二章。")
    domain.save(chapter, 1, "原稿已改变，仍保留原建议的来源。")
    old = verify(client.get(deliveries + f"/{first['id']}"))
    assert not old["basis_current"] and old["value"] == first["value"]
    assert (
        client.post(
            deliveries, json={**alternative, "basis_sha256": second_preview["basis_sha256"]}
        ).status_code
        == 409
    )

    fresh_preview = preview(client, deliveries, alternative)
    assert fresh_preview["before"] == "作者另写第二章。"
    second = send(client, deliveries, alternative, fresh_preview)
    assert second["id"] != first["id"] and second["basis_current"]
    assert send(client, deliveries, alternative, fresh_preview) == second
    current, history = verify(client.get(deliveries, params={"stage": "M5"}))["deliveries"]
    assert (current["id"], history["id"]) == (second["id"], first["id"])
    assert current["basis_current"] and not history["basis_current"]

    third_preview = preview(client, deliveries, data)
    verify(client.post(f"/api/chapters/{target}/archive", json={"base_revision": 1, "archived": True}))
    deleted = client.post(deliveries, json={**data, "basis_sha256": third_preview["basis_sha256"]})
    assert deleted.status_code == 409 and deleted.json()["code"] == "novel_assistant_delivery_stale"
    assert not verify(client.get(deliveries + f"/{second['id']}"))["basis_current"]
    assert domain.chapter(chapter)["content"] == "原稿已改变，仍保留原建议的来源。"


def test_catalogue_lists_real_scopes_and_editable_fields_for_all_seven_modules(
    client, domain, settings, story, reference
):
    branch, chapter = story["branch_id"], story["chapter_id"]
    outline(client, branch, chapter)
    domain.save(chapter, 0, "保留的第一章。")
    active = domain.create_chapter(branch, "未纳入章纲的章")["id"]
    archived = domain.create_chapter(branch, "已归档的章")["id"]
    verify(client.post(f"/api/chapters/{archived}/archive", json={"base_revision": 0, "archived": True}))
    route = endpoint(branch)
    options = {
        stage: {
            option["scope_id"]: {field["id"]: field["label"] for field in option["fields"]}
            for option in verify(client.get(route + "/destinations", params={"stage": stage}))["options"]
        }
        for stage in ("M1", "M2", "M3", "M4", "M5", "M6", "M7")
    }
    assert options["M1"][None]["idea"] == "创作思路" and "genre_ids" not in options["M1"][None]
    assert options["M2"] == {reference["id"]: {"goal": "拆解目标"}}
    assert options["M3"] == {"bell": {"title": "条目名称", "detail": "内容"}}
    assert set(options["M4"]) == {None, "volume", "unit", chapter}
    assert set(options["M4"][chapter]) == {"goal", "obstacle", "turn", "result"}
    assert set(options["M5"]) == {chapter, active} and set(options["M5"][chapter]) == {"text"}
    assert set(options["M6"]) == {None, "volume", chapter, active}
    assert list(options["M6"][None]) == [f"{index:02d}" for index in range(1, 11)]
    assert set(options["M7"][None]) == {"intro", "ending", "characters", "promises", "read_note"}

    turn = queue_reply(client, settings, branch, "M1", None, [proposal("D1", "idea", "从门内的铃声写起。")])
    [card] = turn["result"]["cards"]
    destinations = [
        ("M1", None, "idea", ""),
        ("M2", reference["id"], "goal", "有限信息下取得局部进展"),
        ("M3", "bell", "detail", "铜铃只在门内响。"),
        ("M4", chapter, "goal", "识别铃声"),
        ("M5", chapter, "text", "保留的第一章。"),
        ("M6", "volume", "05", ""),
        ("M7", None, "intro", ""),
    ]
    for stage, scope_id, field, before in destinations:
        data = request(turn, card, stage=stage, scope_id=scope_id, field=field, value="作者确认仅查看。")
        assert preview(client, route + "/deliveries", data)["before"] == before
    assert verify(client.get(route + "/deliveries")) == {"deliveries": []}
