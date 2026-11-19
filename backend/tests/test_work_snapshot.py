from pathlib import Path

import pytest

from ainovel import reference_fork, reference_sync
from ainovel.backups import create_backup, unpack_verified
from ainovel.db import Database, uid
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.outline_workspace import read_outline
from ainovel.story_graph import graph
from ainovel.work_snapshot import RestoreInput, restore
from test_conception_workbench import confirm, path, save
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_project_brief import BRIEF, IDEA
from test_reference_flow import SOURCE, reference as reference_fixture, update as update_reference
from test_reference_sync import card
from test_workflow_inspection import author, item, report, source

reference = reference_fixture


@pytest.fixture
def snapshot_client(client):
    return client


def verify(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def route(branch):
    return f"/api/branches/{branch}/work-snapshots"


def outline(branch):
    return f"/api/branches/{branch}/outline-workspace"


def save_project(client, branch, revision, ending):
    return verify(
        client.put(
            f"{path(branch)}/project-brief",
            json={
                "base_revision": revision,
                "idempotency_key": uid(),
                "idea": IDEA,
                "brief": {**BRIEF, "ending": ending},
            },
        )
    )


def request_restore(client, branch, snapshot_id, mode="in_place", **fields):
    location = f"{route(branch)}/{snapshot_id}"
    preview = verify(client.get(location + "/preview"))
    return verify(
        client.post(
            location + "/restore",
            json={
                "basis_sha256": preview["basis_sha256"],
                "mode": mode,
                "idempotency_key": uid(),
                **fields,
            },
        ),
        201,
    )


def test_fork_rebinds_unconfirmed_promise_windows(snapshot_client, domain, story):
    client = snapshot_client
    branch = story["branch_id"]
    chapter_id = story["chapter_id"]
    chapter = {**chapter_plan(chapter_id, "查找缺口"), "setting_ids": []}
    pending = {
        "title": "门环的缺口",
        "notes": "以后用缺口确认来历。",
        "plant_chapter_id": chapter_id,
        "opens_chapter_id": chapter_id,
        "closes_chapter_id": chapter_id,
    }
    draft = verify(
        client.put(
            outline(branch),
            json={
                "base_revision": 0,
                "plan": {
                    "summary": "用缺口查出门环来历",
                    "volumes": [
                        {
                            "id": "v",
                            "title": "门环",
                            "summary": "追查缺口",
                            "units": [
                                {"id": "u", "title": "核对", "summary": "确认来历", "chapters": [chapter]}
                            ],
                        }
                    ],
                    "new_promises": [pending],
                },
            },
        )
    )["draft"]
    snapshot = verify(client.post(route(branch), json={"label": "未确认伏笔计划"}), 201)
    fork = request_restore(client, branch, snapshot["id"], "new_branch", branch_name="伏笔计划复本")
    target = fork["branch_id"]
    new_id = fork["chapters"][chapter_id]["id"]
    assert new_id != chapter_id
    restored = verify(client.get(outline(target)))["draft"]
    assert not restored["confirmed"]
    assert verify(client.get(outline(branch)))["draft"] == draft
    assert restored["plan"]["new_promises"] == [
        {key: new_id if key.endswith("_chapter_id") else value for key, value in pending.items()}
    ]
    assert graph(domain, branch)["records"] == graph(domain, target)["records"] == []
    confirmed = verify(
        client.post(outline(target) + "/confirm", json={"base_revision": restored["revision"]})
    )
    assert confirmed["promises"][0]["plant_chapter_id"] == new_id
    assert graph(domain, branch)["records"] == []
    assert graph(domain, target)["records"][0]["payload"]["title"] == pending["title"]


def test_snapshot_rebinds_project_and_keeps_card_addresses_and_length_targets(
    snapshot_client, domain, story, reference
):
    client = snapshot_client
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    domain.save(chapter_id, 0, "师姐在门内接过铜铃。")
    project = save_project(client, branch, 0, "铃归师姐。")
    endpoint = path(branch)
    state = verify(client.get(endpoint))
    authored_setting = {"id": "bell", "title": "门铃", "detail": "铜铃只在门内响。", "links": []}
    selected = [{"id": "world", "items": [authored_setting]}]
    candidate = domain.stage(
        {"novel_id": story["id"], "branch_id": branch},
        "conception_bundle",
        {"categories": selected},
        {
            "conception_revision": state["revision"],
            "story_revision": state["story_revision"],
            "project_confirmation_id": project["id"],
            "project_revision": project["revision"],
        },
    )
    adopted = verify(
        client.post(
            endpoint + "/adopt",
            json={
                "base_revision": state["revision"],
                "base_story_revision": state["story_revision"],
                "idempotency_key": uid(),
                "artifact_id": candidate["id"],
                "categories": selected,
            },
        )
    )
    setting = confirm(client, endpoint, adopted)["confirmation"]["id"]
    card = {
        "id": "pivot",
        "observation": "先给一部分信息",
        "interpretation": "留下要验证的缺口",
        "technique": "用局部收获触发第二步行动",
        "conditions": "下一步行动必须依赖已获得的部分线索",
        "exceptions": "换掉人物、地图和原场景",
        "evidence": SOURCE,
    }
    saved = update_reference(client, domain, reference, cards=[card], selected_ids=["pivot"])
    address = {"session_id": saved["id"], "card_id": "pivot", "revision": saved["revision"]}
    put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": 0,
            "confirmation_id": setting,
            "plan": {
                "summary": "门内归还",
                "volumes": [
                    {
                        "id": "v1",
                        "title": "遗铃",
                        "summary": "完成归还",
                        "units": [
                            {
                                "id": "u1",
                                "title": "师姐",
                                "summary": "找到主人",
                                "chapters": [
                                    {
                                        **chapter_plan(chapter_id, "验证门铃"),
                                        "reference_cards": [address],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        },
    )

    def length(branch_id, scope, identifier=None, minimum=None, prior=None):
        url = f"/api/branches/{branch_id}/workflow-inspections"
        scope_fields = {"scope": scope, **({"scope_id": identifier} if identifier else {})}
        if minimum is None:
            return verify(client.get(url, params=scope_fields))["preview"]["length_target"]
        return verify(
            client.put(
                url + "/length-targets",
                json={
                    **scope_fields,
                    "base_target_id": prior,
                    "targets": {"min_chars": minimum},
                },
            )
        )["preview"]["length_target"]

    initial_target = length(branch, "chapter", chapter_id, 1)
    snapshot = verify(
        client.post(
            route(branch),
            json={"scope": "book", "label": "归铃"},
        ),
        201,
    )
    later_target = length(branch, "chapter", chapter_id, 100, initial_target["id"])
    length(branch, "book", minimum=100)
    save_project(client, branch, project["revision"], "铜铃留在铺子里。")
    update_reference(client, domain, saved, cards=[{**card, "technique": "另一写法"}], selected_ids=["pivot"])

    restored = request_restore(client, branch, snapshot["id"])
    status = verify(client.get(endpoint))
    assert status["project"]["id"] == restored["project_id"] != project["id"]
    assert not status["project_impacts"]
    assert status["confirmation"]["current"]
    confirmed = verify(client.get(outline(branch)))["confirmed"]
    assert confirmed["setting_basis"]["reference_cards"] == [
        {
            **address,
            **{key: card[key] for key in ("technique", "conditions", "exceptions")},
            "source_title": "自拟参考.txt",
        }
    ]
    current_target = length(branch, "chapter", chapter_id)
    assert current_target["targets"]["min_chars"] == 1
    assert current_target["revision"] > later_target["revision"]
    assert length(branch, "book") is None
    assert length(branch, "book", minimum=1)["revision"] == 3

    fork = request_restore(client, branch, snapshot["id"], "new_branch", branch_name="归铃复本")
    assert (
        length(fork["branch_id"], "chapter", fork["chapters"][chapter_id]["id"])["targets"]["min_chars"] == 1
    )
    forked = verify(client.get(outline(fork["branch_id"])))["confirmed"]
    assert forked["setting_basis"]["reference_cards"] == confirmed["setting_basis"]["reference_cards"]


def test_snapshot_fork_reissues_exact_reference_lineage_without_raw_quotes(
    snapshot_client, domain, story, reference, tmp_path, monkeypatch
):
    client = snapshot_client
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    domain.save(chapter_id, 0, "师姐在门内接过铜铃。")
    save_project(client, branch, 0, "铃归师姐。")
    settings_id = setting_confirmation(client, branch)
    support = card("半幅图", id="support", technique="以余下疑问推进后一步。")
    unrelated = card("渡口掌柜", id="unrelated", technique="让边角线索留在背景。")
    first = update_reference(client, domain, reference, cards=[card("😀渡口"), support, unrelated])
    chosen = card("😀渡口", technique="先得到可核对的一半证据。")
    second = update_reference(client, domain, first, cards=[chosen, support, unrelated])
    catalogue = verify(client.get(outline(branch) + "/reference-options"))
    pinned = next(row for row in catalogue if row["card_id"] == chosen["id"])
    supporting = next(row for row in catalogue if row["card_id"] == support["id"])
    unrelated_address = next(row for row in catalogue if row["card_id"] == unrelated["id"])

    def address(source):
        return {key: source[key] for key in ("session_id", "card_id", "revision", "version_id")}

    def plan(*cards):
        return {
            "summary": "门内查证",
            "volumes": [
                {
                    "id": "v",
                    "title": "遗铃",
                    "summary": "查证铜铃",
                    "units": [
                        {
                            "id": "u",
                            "title": "师姐",
                            "summary": "找到线索",
                            "chapters": [
                                {
                                    **chapter_plan(chapter_id, "验证铜铃"),
                                    "reference_cards": [address(value) for value in cards],
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    confirmed = put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": 0,
            "confirmation_id": settings_id,
            "plan": plan(pinned, supporting),
        },
    )["confirmed"]
    later = card("😀渡口", technique="由前一层证据直接指向另一位证人。")
    third = update_reference(client, domain, second, cards=[later, support, unrelated])
    draft_card = next(
        row
        for row in verify(client.get(outline(branch) + "/reference-options"))
        if row["card_id"] == later["id"]
    )
    verify(
        client.put(
            outline(branch),
            json={
                "base_revision": confirmed["revision"],
                "confirmation_id": settings_id,
                "plan": plan(draft_card),
            },
        )
    )
    snapshot = verify(client.post(route(branch), json={"label": "保留手法卡来源"}), 201)
    current = card("😀渡口", technique="快照后改变手法。")
    update_reference(client, domain, third, cards=[current, support, unrelated])
    location = f"{route(branch)}/{snapshot['id']}"
    args = {
        "basis_sha256": verify(client.get(location + "/preview"))["basis_sha256"],
        "mode": "new_branch",
        "branch_name": "手法卡快照复本",
        "idempotency_key": uid(),
    }
    source_before = export_manuscript(domain, branch).reference_history
    first_revision = next(
        version
        for version in source_before.versions
        if version.card_id == chosen["id"] and version.revision == 1
    )
    native_catalog = reference_fork._native_catalog

    def missing_parent(tx, branch_id):
        versions, heads, excerpts = native_catalog(tx, branch_id)
        if branch_id == branch:
            versions.pop(first_revision.id)
        return versions, heads, excerpts

    with monkeypatch.context() as patch:
        patch.setattr(reference_fork, "_native_catalog", missing_parent)
        missing = client.post(location + "/restore", json=args)
        assert missing.status_code == 409, missing.text
        assert first_revision.id in missing.json()["message"]
        with domain.db.connect() as tx:
            assert not tx.execute("SELECT 1 FROM branches WHERE name=?", (args["branch_name"],)).fetchone()
            assert not tx.execute("SELECT 1 FROM receipts WHERE key=?", (args["idempotency_key"],)).fetchone()

    forked = verify(client.post(location + "/restore", json=args), 201)
    assert verify(client.post(location + "/restore", json=args), 201) == forked
    target = forked["branch_id"]
    assert forked["reference_sources"]["source_branch_id"] == branch
    mapping = forked["reference_sources"]["version_ids"]
    assert set(mapping) == {
        first_revision.id,
        pinned["version_id"],
        draft_card["version_id"],
        supporting["version_id"],
    }
    assert not set(mapping) & set(mapping.values())
    assert unrelated_address["version_id"] not in mapping
    assert export_manuscript(domain, branch).reference_history == source_before
    with domain.db.connect() as tx:
        assert not tx.execute(
            "SELECT 1 FROM portable_reference_excerpts WHERE branch_id=?", (target,)
        ).fetchone()
        for new in mapping.values():
            reference_sync.validate_reference_version(
                target, reference_sync.read_reference_version(tx, target, new)
            )
        version = reference_sync.read_reference_version(tx, target, mapping[draft_card["version_id"]])
        assert version.parent_id == mapping[pinned["version_id"]]
        assert version.card.technique == later["technique"]
        assert version.citation == next(
            row.citation for row in source_before.versions if row.id == draft_card["version_id"]
        )

    current = verify(client.get(outline(target)))
    assert not current["stale"]
    for row, old in ((current["confirmed"], pinned), (current["draft"], draft_card)):
        selected = row["setting_basis"]["reference_cards"][0]
        assert selected == {**old, "version_id": mapping[old["version_id"]]}
        assert row["plan"]["volumes"][0]["units"][0]["chapters"][0]["reference_cards"][0] == address(selected)
    options = verify(client.get(outline(target) + "/reference-options"))
    assert options == [{**supporting, "version_id": mapping[supporting["version_id"]]}]
    edited = put_and_confirm(
        client,
        outline(target),
        {
            "base_revision": current["draft"]["revision"],
            "confirmation_id": forked["setting_id"],
            "plan": {**current["draft"]["plan"], "summary": "改用另一位证人。"},
        },
    )["confirmed"]
    frozen = edited["setting_basis"]["reference_cards"][0]

    exported = export_manuscript(domain, target)
    assert not exported.reference_history.excerpts
    assert "😀渡口" not in exported.model_dump_json() and "半幅图" not in exported.model_dump_json()
    receiver = Domain(Database(tmp_path / "forked-reference-archive.sqlite"))
    import_manuscript(receiver, exported)
    assert read_outline(receiver, target)["confirmed"]["setting_basis"]["reference_cards"] == [frozen]


def test_volume_checkpoint_restores_manuscript_setting_outline_and_promise_in_both_modes(
    snapshot_client,
    domain,
    settings,
    story,
    tmp_path,
):
    client = snapshot_client
    branch = story["branch_id"]
    chapter_id = story["chapter_id"]
    project = save_project(client, branch, 0, "铃归师姐。")
    settled = domain.save(chapter_id, 0, "师姐在门内接过铜铃，铜铃响了。")
    inventory = {"id": "lamps", "title": "油灯库存", "detail": "库房只余两盏旧灯。"}
    setting_id = setting_confirmation(
        client, branch, [{"id": "resources", "title": "资源台账", "items": [inventory]}]
    )
    record_route = f"/api/branches/{branch}/story-records"
    planned = verify(
        client.post(
            record_route,
            json={
                "base_story_revision": domain.branch(branch)["story_revision"],
                "record": {
                    "kind": "promise",
                    "title": "铃归师姐",
                    "stages": [
                        {"status": "planned"},
                        {
                            "status": "resolved",
                            "sources": [
                                {
                                    "chapter_id": chapter_id,
                                    "revision": settled["revision"],
                                    "start": 0,
                                    "end": len(settled["content"]),
                                    "quote": settled["content"],
                                }
                            ],
                        },
                    ],
                },
            },
        ),
        201,
    )
    record_id = planned["records"][0]["id"]
    authored_plan = {
        "summary": "门内归还",
        "volumes": [
            {
                "id": "volume",
                "title": "遗铃",
                "summary": "追铃",
                "units": [
                    {
                        "id": "unit",
                        "title": "师姐",
                        "summary": "归还",
                        "chapters": [
                            {
                                **chapter_plan(chapter_id, "门内归铃"),
                                "setting_ids": ["bell", "lamps"],
                            },
                        ],
                    }
                ],
            }
        ],
        "promises": [
            {
                "promise_id": record_id,
                "plant_chapter_id": chapter_id,
                "opens_chapter_id": chapter_id,
                "closes_chapter_id": chapter_id,
            }
        ],
    }
    old_outline = put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": 0,
            "confirmation_id": setting_id,
            "plan": authored_plan,
        },
    )["confirmed"]
    checkpoint = verify(
        client.post(
            route(branch),
            json={
                "scope": "volume",
                "scope_id": "volume",
                "label": "卷末",
            },
        ),
        201,
    )
    assert checkpoint["outline_id"] == old_outline["id"]
    assert checkpoint["project_id"] == project["id"]
    old_resource = next(item for item in old_outline["setting_basis"]["items"] if item["id"] == "lamps")
    assert old_resource == {**inventory, "category_id": "resources", "category_title": "资源台账"}
    before = verify(client.get(f"{route(branch)}/{checkpoint['id']}/preview"))
    changed = domain.save(chapter_id, settled["revision"], "师姐在门外接过铜铃，没有听见声音。")
    changed_project = save_project(client, branch, project["revision"], "铃由学徒保管。")
    second = client.get(path(branch)).json()
    next(category for category in second["categories"] if category["id"] == "world")["items"][0]["detail"] = (
        "铜铃只在门外响。"
    )
    next(category for category in second["categories"] if category["id"] == "resources")["items"][0][
        "detail"
    ] = "库房补到五盏油灯。"
    second = confirm(client, path(branch), save(client, path(branch), second), key=uid())
    put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": old_outline["revision"],
            "confirmation_id": second["confirmation"]["id"],
            "plan": {**authored_plan, "summary": "暂存铜铃"},
        },
    )
    stale = client.post(
        f"{route(branch)}/{checkpoint['id']}/restore",
        json={
            "basis_sha256": before["basis_sha256"],
            "mode": "in_place",
            "idempotency_key": uid(),
        },
    )
    assert stale.status_code == 409
    snapshots = verify(client.get(route(branch)))
    assert any(row["id"] == checkpoint["id"] for row in snapshots)
    milestones = [row["label"] for row in snapshots if row["purpose"] == "milestone"]
    assert milestones.count("确认四级大纲") == 2
    assert milestones.count("确认立项") == 2
    assert all(row["purpose"] != "pre_restore" for row in snapshots)
    restored = request_restore(client, branch, checkpoint["id"])
    current = domain.chapter(chapter_id)
    assert current["revision"] > changed["revision"]
    assert current["content"] == settled["content"]
    assert verify(client.get(path(branch)))["confirmation"]["id"] == restored["setting_id"]
    assert restored["setting_id"] != setting_id
    returned_project = verify(client.get(f"{path(branch)}/project-brief"))
    assert returned_project["revision"] > changed_project["revision"]
    assert returned_project["brief"]["ending"] == project["brief"]["ending"]
    restored_outline = verify(client.get(outline(branch)))["confirmed"]
    assert restored_outline["plan"] == authored_plan
    assert restored_outline["setting_basis"]["confirmation_id"] == restored["setting_id"]
    assert old_resource in restored_outline["setting_basis"]["items"]
    row = graph(domain, branch)["records"][0]
    assert not row["invalid_reasons"]
    assert row["payload"]["stages"][-1]["sources"][0]["revision"] == current["revision"]
    assert any(row["id"] == restored["recovery_snapshot_id"] for row in verify(client.get(route(branch))))

    fork = request_restore(client, branch, checkpoint["id"], "new_branch", branch_name="卷末复本")
    fork_branch = fork["branch_id"]
    fork_chapter = fork["chapters"][chapter_id]["id"]
    assert fork_chapter != chapter_id
    assert domain.chapter(fork_chapter, fork_branch)["content"] == settled["content"]
    fork_outline = verify(client.get(outline(fork_branch)))["confirmed"]
    assert fork_outline["setting_basis"]["confirmation_id"] == fork["setting_id"]
    assert old_resource in fork_outline["setting_basis"]["items"]
    fork_plan = fork_outline["plan"]
    assert fork_plan["volumes"][0]["units"][0]["chapters"][0]["setting_ids"] == ["bell", "lamps"]
    assert fork_plan["promises"][0]["promise_id"] == fork["record_ids"][record_id] != record_id
    assert fork_plan["promises"][0]["closes_chapter_id"] == fork_chapter
    fork_record = graph(domain, fork_branch)["records"][0]
    assert not fork_record["invalid_reasons"]
    assert fork_record["payload"]["stages"][-1]["sources"][0]["chapter_id"] == fork_chapter
    assert domain.chapter(chapter_id)["revision"] == current["revision"]

    isolated = tmp_path / "isolated-work-snapshot"
    backup = create_backup(settings, domain.db)
    unpack_verified(Path(backup["path"]), isolated)
    recovered = Domain(Database(isolated / "manuscripts.sqlite"))
    assert recovered.artifact(checkpoint["id"])["payload"]["manifest_sha256"] == checkpoint["manifest_sha256"]
    assert (
        recovered.artifact(restored["id"])["payload"]["recovery_snapshot_id"]
        == restored["recovery_snapshot_id"]
    )
    assert old_resource in read_outline(recovered, branch)["confirmed"]["setting_basis"]["items"]
    assert old_resource in read_outline(recovered, fork_branch)["confirmed"]["setting_basis"]["items"]


def test_failed_restore_is_atomic_and_retry_is_idempotent(snapshot_client, domain, story, monkeypatch):
    client = snapshot_client
    branch = story["branch_id"]
    chapter_id = story["chapter_id"]
    old = domain.save(chapter_id, 0, "第一稿。")
    checkpoint = verify(client.post(route(branch), json={"label": "回到首稿"}), 201)
    changed = domain.save(chapter_id, old["revision"], "第二稿。")
    location = f"{route(branch)}/{checkpoint['id']}"
    preview = verify(client.get(location + "/preview"))
    params = {
        "basis_sha256": preview["basis_sha256"],
        "mode": "in_place",
        "idempotency_key": uid(),
    }

    def fault(*_):
        raise RuntimeError("simulated failure after restoring chapter and graph")

    with monkeypatch.context() as scoped:
        scoped.setattr("ainovel.work_snapshot._restore_setup", fault)
        with pytest.raises(RuntimeError, match="simulated failure"):
            restore(domain, branch, checkpoint["id"], RestoreInput(**params))
    assert domain.chapter(chapter_id) == changed
    assert len(verify(client.get(route(branch)))) == 1
    completed = verify(client.post(location + "/restore", json=params), 201)
    assert verify(client.post(location + "/restore", json=params), 201) == completed
    assert len(verify(client.get(route(branch)))) == 2


def test_rollback_keeps_numeric_review_but_blocks_completion_until_dependent_checks_are_repeated(
    snapshot_client, domain, story
):
    client = snapshot_client
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "学徒打开了门。")
    second = domain.create_chapter(branch, "归铃")
    saved = verify(
        client.post(route(branch), json={"scope": "chapter", "scope_id": first["id"], "label": "开门旧稿"}),
        201,
    )
    domain.save(first["id"], first["revision"], "学徒绕到后门。")
    second = domain.save(second["id"], 0, "学徒终于把铃送回给师姐。")
    base = f"/api/branches/{branch}"
    inspections = base + "/workflow-inspections"
    book_preview = verify(client.get(inspections))["preview"]
    issued = report(
        client,
        inspections,
        book_preview,
        length_targets={"expected_chapters": 2, "min_chars": 1},
        author_reviews=[
            author(row["check_id"], second) for row in book_preview["items"] if row["status"] == "pending"
        ],
    )
    assert all(row["current_status"] == "green" for row in issued["items"])
    completion = base + "/book-completion"
    decision = {
        "basis_sha256": verify(client.get(completion))["basis_sha256"],
        "read_note": "已通读合成故事的两个章节。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "verified",
        },
        "ending": {"note": "铜铃归还原主。", "sources": [source(second)]},
        "characters": {"note": "学徒和师姐重逢。", "sources": [source(second)]},
        "promises": {"note": "归铃的承诺已经兑现。", "sources": [source(second)]},
        "intro": "学徒循着线索寻找铜铃的主人。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [first["id"]],
        "materials_reviewed": True,
    }
    delivered = verify(client.post(completion + "/confirm", json=decision), 201)
    scope = {"scope": "chapter", "scope_id": second["id"]}
    scoped = report(
        client,
        inspections,
        verify(client.get(inspections, params=scope))["preview"],
        length_targets={"min_chars": 1},
    )

    forecast = verify(client.get(f"{route(branch)}/{saved['id']}/preview"))
    affected = {row["id"]: row for row in forecast["affected_reports"]}
    assert affected[delivered["id"]] == {
        "id": delivered["id"],
        "kind": "book_completion",
        "scope": "book",
        "scope_id": None,
        "check_ids": [],
    }
    assert affected[scoped["id"]]["scope_id"] == second["id"]
    assert "08" in affected[scoped["id"]]["check_ids"] and "09" not in affected[scoped["id"]]["check_ids"]

    request_restore(client, branch, saved["id"])
    current = verify(client.get(f"{inspections}/{scoped['id']}"))
    assert not current["current"] and item(current, "09")["current_status"] == "green"
    assert item(current, "08")["current_status"] == "outdated"
    assert not verify(client.get(f"{inspections}/{issued['id']}"))["current"]
    assert not verify(client.get(f"{completion}/history/{delivered['id']}"))["current"]
    decision["basis_sha256"] = verify(client.get(completion))["basis_sha256"]
    preview = verify(client.post(completion + "/preview", json=decision))
    assert next(check for check in preview["checks"] if check["id"] == "inspection")["status"] == "red"
    blocked = verify(client.post(completion + "/confirm", json=decision), 409)
    assert blocked["code"] == "completion_blocked"


@pytest.mark.parametrize("change_body", [False, True])
def test_partial_volume_leaves_other_plan_and_manuscript_untouched(
    snapshot_client,
    domain,
    story,
    change_body,
):
    client = snapshot_client
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "第一卷旧稿。")
    second = domain.create_chapter(branch, "第二卷第一章")
    second = domain.save(second["id"], 0, "第二卷旧稿。")
    setting = setting_confirmation(client, branch)
    graph_route = f"/api/branches/{branch}/story-records"
    recorded = verify(
        client.post(
            graph_route,
            json={
                "base_story_revision": domain.branch(branch)["story_revision"],
                "record": {
                    "kind": "character",
                    "title": "卷一学徒",
                    "sources": [
                        {
                            "chapter_id": first["id"],
                            "revision": first["revision"],
                            "start": 0,
                            "end": len(first["content"]),
                            "quote": first["content"],
                        }
                    ],
                },
            },
        ),
        201,
    )["records"][0]

    def volume(identifier, chapter_id, goal):
        return {
            "id": identifier,
            "title": identifier,
            "summary": goal,
            "units": [
                {
                    "id": f"{identifier}-unit",
                    "title": goal,
                    "summary": goal,
                    "chapters": [chapter_plan(chapter_id, goal)],
                }
            ],
        }

    original = {
        "summary": "旧总纲",
        "volumes": [volume("v1", first["id"], "旧目标"), volume("v2", second["id"], "旧续写")],
    }
    confirmed = put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": 0,
            "confirmation_id": setting,
            "plan": original,
        },
    )["confirmed"]
    saved = verify(
        client.post(
            route(branch),
            json={
                "label": "第一卷检查点",
                "scope": "volume",
                "scope_id": "v1",
            },
        ),
        201,
    )
    if change_body:
        domain.save(first["id"], first["revision"], "第一卷新稿。")
    newer_second = domain.save(second["id"], second["revision"], "第二卷保留的新稿。")
    edited = {
        "summary": "主线已有发展",
        "volumes": [volume("v1", first["id"], "改过目标"), volume("v2", second["id"], "当下第二卷")],
    }
    put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": confirmed["revision"],
            "confirmation_id": setting,
            "plan": edited,
        },
    )
    inspection_route = f"/api/branches/{branch}/workflow-inspections"
    scope = {"scope": "volume", "scope_id": "v2"}
    preview = verify(client.get(inspection_route, params=scope))["preview"]
    report = verify(
        client.post(
            inspection_route,
            json={
                **scope,
                "basis_sha256": preview["basis_sha256"],
                "length_targets": {"expected_chapters": 1, "min_chars": 1},
            },
        ),
        201,
    )
    request_restore(client, branch, saved["id"])
    assert domain.chapter(first["id"])["content"] == first["content"]
    assert domain.chapter(second["id"])["content"] == newer_second["content"]
    assert domain.chapter(second["id"])["revision"] == newer_second["revision"]
    view = verify(client.get(outline(branch)))
    assert view["confirmed"]["plan"]["summary"] == "主线已有发展"
    assert view["confirmed"]["plan"]["volumes"] == [original["volumes"][0], edited["volumes"][1]]
    assert view["confirmed"]["setting_basis"]["confirmation_id"] == setting
    current = verify(client.get(inspection_route, params=scope))
    historical = verify(client.get(f"{inspection_route}/{report['id']}"))
    assert current["latest"] == historical
    assert historical["current"] is not change_body
    assert historical["basis_sha256"] == report["basis_sha256"]
    assert historical["chapters"] == report["chapters"]
    assert item(historical, "09") == item(report, "09")
    assert item(historical, "09")["current_status"] == item(current["preview"], "09")["status"] == "green"
    assert item(historical, "08")["current_status"] == ("outdated" if change_body else "pending")
    revised_target = verify(
        client.put(
            inspection_route + "/length-targets",
            json={
                **scope,
                "base_target_id": report["length_target"]["id"],
                "targets": {"expected_chapters": 2},
            },
        )
    )
    assert item(revised_target["preview"], "09")["status"] == "red"
    assert item(revised_target["latest"], "09") == {**item(report, "09"), "current_status": "outdated"}
    restored = graph(domain, branch)["records"][0]
    assert restored["id"] == recorded["id"]
    assert restored["revision"] > recorded["revision"]
    assert not restored["invalid_reasons"]
    assert restored["payload"]["sources"][0]["revision"] == domain.chapter(first["id"])["revision"]
