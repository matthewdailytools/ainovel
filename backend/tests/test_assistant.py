import asyncio
from dataclasses import replace
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from ainovel.api import create_app
from ainovel.backups import create_backup, restore_backup
from ainovel.db import Database
from ainovel.domain import Domain
from ainovel.library import Library
from ainovel.worker import Worker
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_reference_flow import SOURCE as PRIVATE_REFERENCE, reference as reference_fixture
from test_work_snapshot import verify

reference = reference_fixture


def endpoint(story):
    return f"/api/branches/{story['branch_id']}/assistant/conversations"


def conversation(client, story, stage, scope_id=None, **extra):
    return verify(
        client.post(
            endpoint(story),
            json={
                "stage": stage,
                "scope_id": scope_id,
                "subject": f"{stage} 核对",
                "client_request_id": f"start-{stage}-test",
                **extra,
            },
        ),
        201,
    )


def turn_path(client, story, scope):
    route = f"{endpoint(story)}/{scope['id']}/turns"
    return route, verify(client.get(route))


def turn_request(page, question, key, **extra):
    return {
        "base_revision": page["conversation"]["revision"],
        "basis_sha256": page["basis"]["sha256"],
        "question": question,
        "account": "deepseek",
        "client_request_id": key,
        **extra,
    }


class Model:
    def __init__(self, replies=None, during_call=None):
        self.calls = []
        self.replies = list(replies or [])
        self.during_call = during_call

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        self.calls.append(prompt)
        if self.during_call:
            self.during_call()
        result = self.replies.pop(0) if self.replies else {"answer": "按当前依据继续。"}
        return {
            "text": json.dumps(result, ensure_ascii=False),
            "usage": {"input": 1, "output": 1},
            "call_id": "assistant-test",
        }


def test_whole_library_backup_recovers_conversation_sources_and_draft_receipts(
    client,
    domain,
    settings,
    story,
    tmp_path,
):
    chapter = story["chapter_id"]
    domain.save(chapter, 0, "😀\r\n铜铃。其余正文。")
    thread = conversation(client, story, "M5", chapter)
    route, initial = turn_path(client, story, thread)
    request = turn_request(
        initial,
        "请核对选区。",
        "backup-assistant-turn",
        target="selection",
        action="refine",
        draft_text="铜铃。",
        selection={"start": 4, "end": 7, "revision": 1},
    )
    queued = verify(client.post(route, json=request), 202)["turn"]
    model = Model(
        [
            {
                "answer": "保留物件。",
                "source_ids": ["S1"],
                "suggestions": [{"field": "selection", "value": "旧铜铃。", "reason": "说明年代。"}],
            }
        ]
    )
    assert asyncio.run(Worker(settings, model).run_once())
    receipt = f"{route}/{queued['id']}/apply-receipt"
    assert verify(client.post(receipt, json={"field": "selection"})) == {"applied_fields": ["selection"]}
    before = verify(client.get(route))
    [approved] = before["turns"]
    assert approved["result"]["citations"][0]["excerpt"] == "铜铃。"
    assert approved["task_id"] == queued["task_id"]
    pending = verify(
        client.post(
            route,
            json=turn_request(before, "再讨论提示节奏。", "backup-pending-turn"),
        ),
        202,
    )["turn"]
    archive = verify(client.post("/api/backups"))
    assert verify(client.post(f"/api/backups/{archive['id']}/verify"))["integrity"] == "ok"

    isolated = replace(settings, data_dir=tmp_path / "recovered-conversations")
    assert restore_backup(isolated, None, Path(archive["path"]))["usage_ledger"] == "kept_current"
    assert verify(client.get(route))["turns"][-1]["status"] == "queued"
    with TestClient(create_app(isolated)) as recovered:
        received = verify(recovered.get(route))
        assert received["conversation"]["id"] == thread["id"]
        assert received["basis"] == before["basis"]
        assert received["turns"][0] == approved
        stopped = received["turns"][1]
        assert stopped["id"] == pending["id"] and stopped["task_id"] == pending["task_id"]
        assert stopped["status"] == "blocked_runtime" and stopped["result"] is None
        assert "从备份恢复" in stopped["error"]
        assert verify(recovered.get(f"/api/chapters/{chapter}"))["content"] == "😀\r\n铜铃。其余正文。"
    assert len(model.calls) == 1


def test_pre_assistant_library_initializes_empty_conversations_after_real_restore(settings, tmp_path):
    old = replace(settings, data_dir=tmp_path / "pre-assistant")
    original = Domain(Database(old.data_dir / "manuscripts.sqlite"))
    Library(original.db, old.data_dir)
    story = original.create_novel("旧版作品", "")
    chapter = original.create_chapter(story["branch_id"], "第一章")["id"]
    saved = original.save(chapter, 0, "旧库正文和修订保持。")
    with original.db.connect(True) as tx:
        # Recreate the pre-assistant schema in this disposable fixture without changing older tables.
        tx.execute("DROP TABLE assistant_apply_receipts")
        tx.execute("DROP TABLE assistant_turns")
        tx.execute("DROP TABLE assistant_conversations")
    archive = create_backup(old, original.db)

    target = replace(settings, data_dir=tmp_path / "recovered-old-library")
    restore_backup(target, None, Path(archive["path"]))
    with TestClient(create_app(target)) as app:
        app.headers["X-Ainovel-CSRF"] = verify(app.get("/api/session"))["csrf"]
        assert verify(app.get(f"/api/chapters/{chapter}")) == saved
        assert verify(app.get(endpoint(story))) == {"conversations": []}
        thread = conversation(app, story, "M5", chapter)
        _, ready = turn_path(app, story, thread)
        assert ready["basis"]["versions"][0]["id"] == chapter and ready["turns"] == []
    with original.db.connect() as tx:
        assert not tx.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_conversations'").fetchone()


def test_assistant_keeps_turns_and_queue_atomic_and_separate_from_legacy_tasks(
    client, domain, settings, story
):
    thread = conversation(client, story, "M1", draft_key="draft-for-this-form")
    assert thread == conversation(client, story, "M1", draft_key="draft-for-this-form")
    other = domain.create_novel("其他作品", "")
    assert (
        client.get(
            f"/api/branches/{other['branch_id']}/assistant/conversations/{thread['id']}/turns"
        ).status_code
        == 404
    )
    assert verify(client.get(endpoint(story) + "?stage=M1"))["conversations"] == []
    assert verify(client.get(endpoint(story) + "?stage=M1&draft_key=draft-for-this-form"))[
        "conversations"
    ] == [thread]
    route, page = turn_path(client, story, thread)
    first = turn_request(
        page,
        "请选择建议题材。",
        "turn-initial-one",
        action="suggest",
        target="genre_ids",
        target_key="editor-field",
        context_key="author-local-context",
        draft_text="一名学徒循铜铃追索线索。",
    )
    queued = verify(client.post(route, json=first), 202)
    assert verify(client.post(route, json=first), 202) == queued
    assert client.post(route, json={**first, "question": "另一个问题"}).json()["code"] == "idempotency"
    assert client.post(route, json={**first, "client_request_id": "rival-request"}).json()["code"] == (
        "assistant_changed"
    )
    assert verify(client.get("/api/tasks", params={"branch_id": story["branch_id"]})) == []
    assert verify(client.get(f"/api/tasks/{queued['turn']['task_id']}"))["status"] == "queued"

    model = Model(
        [
            {
                "answer": "可以主悬疑、辅仙侠。",
                "suggestions": [
                    {"field": "genre_ids", "value": ["mystery", "xianxia"], "reason": "寻物主线和门规并存。"},
                ],
            }
        ]
    )
    assert asyncio.run(Worker(settings, model).run_once())
    saved = verify(client.get(route))
    [initial] = saved["turns"]
    assert initial["basis_current"] and initial["result"]["suggestions"][0]["value"] == ["mystery", "xianxia"]
    assert initial["target_key"] == "editor-field" and initial["context_key"] == "author-local-context"
    assert "editor-field" not in model.calls[0] and "author-local-context" not in model.calls[0]
    assert "一名学徒循铜铃追索线索。" in model.calls[0]
    assert verify(client.get(f"/api/branches/{story['branch_id']}/conception/project-brief")) is None

    follow = turn_request(saved, "为什么主类型适合？", "turn-second-two")
    second = verify(client.post(route, json=follow), 202)
    assert asyncio.run(Worker(settings, model).run_once())
    assert "请选择建议题材。" in model.calls[-1] and "可以主悬疑、辅仙侠。" in model.calls[-1]
    front = verify(client.get(route, params={"limit": 1}))
    assert front["next_before"] == 2 and [row["id"] for row in front["turns"]] == [second["turn"]["id"]]
    earlier = verify(client.get(route, params={"limit": 1, "before": front["next_before"]}))
    assert earlier["turns"] == [initial] and earlier["next_before"] is None


def test_assistant_writing_selection_is_exact_and_old_outputs_cannot_be_current(
    client, domain, settings, story
):
    chapter = story["chapter_id"]
    other = domain.create_chapter(story["branch_id"], "邻章")["id"]
    domain.save(chapter, 0, "😀\r\n铜铃。未选中的本章正文。")
    domain.save(other, 0, "未授权的邻章正文。")
    thread = conversation(client, story, "M5", chapter)
    route, page = turn_path(client, story, thread)
    request = turn_request(
        page,
        "调整选区语气。",
        "writing-range-one",
        target="selection",
        action="refine",
        draft_text="铜铃。",
        selection={"start": 4, "end": 7, "revision": 1},
    )
    mismatched = client.post(route, json={**request, "draft_text": "字句不符。"})
    assert mismatched.status_code == 409 and verify(client.get(route))["turns"] == []
    verify(client.post(route, json=request), 202)
    model = Model(
        [
            {
                "answer": "可用原有物件，语气稍收。",
                "source_ids": ["S1"],
                "suggestions": [
                    {"field": "selection", "value": "旧铜铃。", "reason": "突出磨损痕迹。"},
                ],
            }
        ],
        during_call=lambda: domain.save(chapter, 1, "作者在生成过程改稿。"),
    )
    assert asyncio.run(Worker(settings, model).run_once())
    assert all(text not in model.calls[0] for text in ("未选中的本章正文。", "未授权的邻章正文。"))
    current = verify(client.get(route))
    [historical] = current["turns"]
    assert historical["status"] == "succeeded" and not historical["basis_current"]
    assert historical["result"]["suggestions"][0]["value"] == "旧铜铃。"
    assert historical["result"]["citations"] == [
        {
            "kind": "selection",
            "id": chapter,
            "label": "本轮保存稿选区",
            "revision": 1,
            "excerpt": "铜铃。",
        }
    ]
    receipt = f"{route}/{historical['id']}/apply-receipt"
    assert client.post(receipt, json={"field": "text"}).status_code == 409
    for _ in range(2):
        assert verify(client.post(receipt, json={"field": "selection"})) == {"applied_fields": ["selection"]}
    historical = verify(client.get(route))["turns"][0]
    assert historical["applied_fields"] == ["selection"]
    assert domain.chapter(chapter)["content"] == "作者在生成过程改稿。"
    assert (
        client.post(
            route,
            json={
                **request,
                "base_revision": 1,
                "client_request_id": "changed-selection-one",
            },
        ).json()["code"]
        == "assistant_basis"
    )
    archived = client.post(f"/api/chapters/{chapter}/archive", json={"base_revision": 2, "archived": True})
    assert archived.is_success
    assert verify(client.get(route))["basis"] is None
    assert verify(client.get(route))["turns"] == [historical]


def test_assistant_stale_or_cancelled_queued_turns_do_not_dispatch(client, domain, settings, story):
    chapter = story["chapter_id"]
    thread = conversation(client, story, "M5", chapter)
    route, first = turn_path(client, story, thread)
    cancel_turn = verify(
        client.post(route, json=turn_request(first, "先等待", "cancel-before-dispatch")), 202
    )["turn"]
    cancelled = verify(client.post(f"/api/tasks/{cancel_turn['task_id']}/cancel"))
    assert cancelled["status"] == "cancelled"
    next_page = verify(client.get(route))
    verify(client.post(route, json=turn_request(next_page, "核对当前稿", "stale-before-dispatch")), 202)
    domain.save(chapter, 0, "作者发布了新一稿。")
    model = Model()
    assert asyncio.run(Worker(settings, model).run_once())
    assert model.calls == []
    turns = verify(client.get(route))["turns"]
    assert [item["status"] for item in turns] == ["cancelled", "blocked_runtime"]
    assert not any(item["basis_current"] or item["result"] for item in turns)
    current = verify(client.get(route))
    verify(client.post(route, json=turn_request(current, "读取当前对象", "current-retry-turn")), 202)
    assert asyncio.run(Worker(settings, model).run_once())
    assert len(model.calls) == 1 and verify(client.get(route))["turns"][-1]["basis_current"]


def test_assistant_grounds_reference_setting_outline_and_volume_in_the_chosen_object(
    client,
    domain,
    settings,
    story,
    reference,
):
    branch = story["branch_id"]
    chapter = story["chapter_id"]
    setting_id = setting_confirmation(client, branch)
    outline_path = f"/api/branches/{branch}/outline-workspace"
    put_and_confirm(
        client,
        outline_path,
        {
            "base_revision": 0,
            "confirmation_id": setting_id,
            "plan": {
                "summary": "返回古宅",
                "new_promises": [
                    {
                        "title": "铜铃旧痕",
                        "plant_chapter_id": chapter,
                        "opens_chapter_id": chapter,
                        "closes_chapter_id": chapter,
                    }
                ],
                "volumes": [
                    {
                        "id": "volume",
                        "title": "旧铃线索",
                        "summary": "分卷查证",
                        "units": [
                            {
                                "id": "unit",
                                "title": "回家",
                                "summary": "核对证据",
                                "chapters": [chapter_plan(chapter, "识别铜铃断口")],
                            }
                        ],
                    }
                ],
            },
        },
    )
    model = Model()
    scopes = [
        ("M2", reference["id"], "goal", "有限信息下取得局部进展"),
        ("M3", "bell", "detail", "铜铃只在门内响。"),
        ("M4", chapter, "goal", "识别铜铃断口"),
        ("M5", chapter, None, "识别铜铃断口"),
        ("M6", "volume", "07", "伏笔、悬念与承诺"),
    ]
    for stage, identifier, target, sent in scopes:
        thread = conversation(client, story, stage, identifier)
        route, baseline = turn_path(client, story, thread)
        verify(
            client.post(
                route,
                json=turn_request(
                    baseline,
                    "核对当前对象",
                    f"verify-scope-{stage}",
                    target=target,
                ),
            ),
            202,
        )
        assert asyncio.run(Worker(settings, model).run_once())
        assert sent in model.calls[-1]
        assert verify(client.get(route))["turns"][0]["status"] == "succeeded"
    assert PRIVATE_REFERENCE in model.calls[0]
    assert setting_id in model.calls[2] and "promise_windows" in model.calls[2]
    other = domain.create_novel("隔离作品", "")
    rejected = client.post(
        endpoint(other),
        json={
            "stage": "M2",
            "scope_id": reference["id"],
            "subject": "其他作品",
            "client_request_id": "other-scope-m2",
        },
    )
    assert rejected.status_code == 404 and rejected.json()["code"] == "assistant_scope"


def test_reference_assistant_sends_only_the_native_selected_excerpt(client, domain, settings, story):
    before, chosen, after = "段外前文😀", "门吱呀开了。", "未选的其他段落。"
    library = Library(domain.db, settings.data_dir)
    book = library.import_file("自拟片段.txt", (before + chosen + after).encode())
    unit = domain.db.one("SELECT id FROM source_units WHERE book_id=?", (book["id"],))
    start = len(before.encode("utf-16-le")) // 2
    selected = verify(
        client.post(
            "/api/reference-sessions",
            json={
                "unit_id": unit["id"],
                "start": start,
                "end": start + len(chosen),
                "quote": chosen,
                "goal": "核对原句中的动作",
                "branch_id": story["branch_id"],
            },
        ),
        201,
    )
    thread = conversation(client, story, "M2", selected["id"])
    route, baseline = turn_path(client, story, thread)
    verify(client.post(route, json=turn_request(baseline, "读原文", "native-reference-span")), 202)
    model = Model([{"answer": "选段写门开启。", "source_ids": ["S1"]}])
    assert asyncio.run(Worker(settings, model).run_once())
    [prompt] = model.calls
    assert chosen in prompt and before not in prompt and after not in prompt
    [citation] = verify(client.get(route))["turns"][0]["result"]["citations"]
    assert citation["kind"] == "reference_excerpt" and citation["id"] == selected["id"]
    assert citation["excerpt"] == chosen


def test_inspection_assistant_uses_only_selected_check_basis_and_exact_current_excerpts(
    client,
    domain,
    settings,
    story,
):
    chapter = story["chapter_id"]
    branch = story["branch_id"]
    first, second = "第一项专属正文。", "第二项专属正文。"
    bullet = "😀\r\n"
    saved = domain.save(chapter, 0, bullet + first + second)
    setting_id = setting_confirmation(client, branch)
    put_and_confirm(
        client,
        f"/api/branches/{branch}/outline-workspace",
        {
            "base_revision": 0,
            "confirmation_id": setting_id,
            "plan": {
                "summary": "核对本章",
                "volumes": [
                    {
                        "id": "audit-volume",
                        "title": "验收",
                        "summary": "核对本章",
                        "units": [
                            {
                                "id": "audit-unit",
                                "title": "验收",
                                "summary": "核对本章",
                                "chapters": [chapter_plan(chapter, "章纲专属目标")],
                            }
                        ],
                    }
                ],
            },
        },
    )
    endpoint = f"/api/branches/{branch}/workflow-inspections"
    scope = {"scope": "chapter", "scope_id": chapter}
    preview = verify(client.get(endpoint, params=scope))["preview"]
    reviews = []
    for identifier, text, start in (("01", first, 4), ("02", second, 4 + len(first))):
        reviews.append(
            {
                "check_id": identifier,
                "status": "green",
                "basis_note": f"{identifier} 已核对",
                "reason": f"{identifier} 有依据",
                "evidence": [
                    {
                        "chapter_id": chapter,
                        "revision": saved["revision"],
                        "start": start,
                        "end": start + len(text),
                        "quote": text,
                    }
                ],
            }
        )
    report = verify(
        client.post(
            endpoint,
            json={
                **scope,
                "basis_sha256": preview["basis_sha256"],
                "author_reviews": reviews,
            },
        ),
        201,
    )
    thread = conversation(client, story, "M6", chapter)
    route, initial = turn_path(client, story, thread)
    model = Model(
        [
            {"answer": first},
            {"answer": "这次只核对章纲。", "source_ids": ["S2"]},
        ]
    )
    for identifier in ("01", "02"):
        baseline = verify(client.get(route))
        verify(
            client.post(
                route,
                json=turn_request(
                    baseline,
                    "按当前依据复核",
                    f"inspect-selected-{identifier}",
                    target=identifier,
                    action="review",
                ),
            ),
            202,
        )
        assert asyncio.run(Worker(settings, model).run_once())
    assert len(initial["basis"]["checks"]) == 10
    assert first in model.calls[0] and second not in model.calls[0] and "章纲专属目标" not in model.calls[0]
    assert second in model.calls[1] and first not in model.calls[1] and "章纲专属目标" in model.calls[1]
    [citation] = verify(client.get(route))["turns"][-1]["result"]["citations"]
    assert citation["excerpt"] == second
    assert verify(client.get(endpoint, params=scope))["latest"]["id"] == report["id"]


def test_assistant_m6_and_m7_read_current_rules_without_signing_or_leaking_intro_sources(
    client,
    domain,
    settings,
    story,
):
    first = story["chapter_id"]
    second = domain.create_chapter(story["branch_id"], "次章")["id"]
    domain.save(first, 0, "首章只写铜铃。")
    domain.save(second, 0, "末章机关揭密。")

    checks = []
    for scope in (None, first):
        inspection = conversation(client, story, "M6", scope, client_request_id=f"inspection-{scope}")
        _, page = turn_path(client, story, inspection)
        checks.append(page["basis"]["checks"])
    assert all(
        [row["id"] for row in collection] == [f"{i:02d}" for i in range(1, 11)] for collection in checks
    )

    completion = conversation(client, story, "M7")
    route, initial = turn_path(client, story, completion)
    assert len(initial["basis"]["checks"]) == 6
    model = Model([{"answer": "末章机关揭密。"}, {"answer": "开头介绍铜铃。"}])
    verify(
        client.post(
            route,
            json=turn_request(
                initial,
                "讨论整书简介",
                "intro-whole-draft",
                action="suggest",
                target="intro",
                spoiler_scope="whole",
            ),
        ),
        202,
    )
    assert asyncio.run(Worker(settings, model).run_once())
    assert "末章机关揭密。" in model.calls[0]
    previous = verify(client.get(route))
    verify(
        client.post(
            route,
            json=turn_request(
                previous,
                "只讨论开篇简介",
                "intro-opening-draft",
                action="suggest",
                target="intro",
            ),
        ),
        202,
    )
    assert asyncio.run(Worker(settings, model).run_once())
    assert "首章只写铜铃。" in model.calls[1]
    assert "末章机关揭密。" not in model.calls[1] and "讨论整书简介" not in model.calls[1]
    assert verify(client.get(f"/api/branches/{story['branch_id']}/book-completion"))["latest"] is None


@pytest.mark.parametrize(
    "untrusted_reply",
    [
        {"answer": "错引", "source_ids": ["S666"]},
        {"answer": "越界修改", "suggestions": [{"field": "ending", "value": "误改结尾", "reason": "越界"}]},
        {"answer": "字段过长", "suggestions": [{"field": "position", "value": "甲" * 701, "reason": "超长"}]},
    ],
)
def test_assistant_rejects_invented_source_and_invalid_field_values(client, settings, story, untrusted_reply):
    thread = conversation(client, story, "M1")
    route, baseline = turn_path(client, story, thread)
    verify(
        client.post(
            route,
            json=turn_request(
                baseline,
                "核对定位",
                "untrusted-reply",
                action="suggest",
                target="position",
            ),
        ),
        202,
    )
    assert asyncio.run(Worker(settings, Model([untrusted_reply])).run_once())
    [reply] = verify(client.get(route))["turns"]
    assert reply["status"] == "blocked_runtime" and reply["result"] is None
