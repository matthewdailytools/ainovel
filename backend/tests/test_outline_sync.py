from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ainovel import outline_archive, outline_sync, project_sync, reference_sync, settings_sync
from ainovel.api import create_app
from ainovel.cloud_sync import create_sync_app
from ainovel.db import Database, DomainError
from ainovel.desktop_sync import DesktopSync, Resolve, _headers
from ainovel.domain import Domain
from ainovel.manuscript_exchange import ManuscriptPackage, export_manuscript, import_manuscript
from ainovel.outline_workspace import ConfirmOutline, SaveOutline, confirm_outline, read_outline, save_outline
from ainovel.project_brief import ConfirmProject, confirm
from ainovel.reference_portability import ReferenceCardVersion
from ainovel.story_graph import SaveRecord, graph, save_record_tx
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_project_brief import BRIEF
from test_reference_flow import SOURCE, reference as reference_fixture, update
from test_reference_sync import card
from test_settings_sync import receipt

reference = reference_fixture


def prepare_target(source, branch_id, path):
    target = Domain(Database(path))
    import_manuscript(target, export_manuscript(source, branch_id))
    for domain in (source, target):
        with domain.db.connect(True) as tx:
            tx.executescript(project_sync.SCHEMA + settings_sync.SCHEMA + outline_sync.SCHEMA)
    return target


def plan(chapter):
    return {
        "summary": "走入古宅追查铜铃",
        "volumes": [
            {
                "id": "v",
                "title": "古宅",
                "summary": "找到铜铃线索",
                "units": [{"id": "u", "title": "追索", "summary": "先听铃再查门", "chapters": [chapter]}],
            }
        ],
    }


def window(chapter_id):
    return {key: chapter_id for key in ("plant_chapter_id", "opens_chapter_id", "closes_chapter_id")}


def record(tx, branch, payload, *, existing=None, identifier=None):
    revision = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch,)).fetchone()[0]
    return save_record_tx(
        tx,
        branch,
        SaveRecord(
            base_story_revision=revision,
            base_revision=existing["revision"] if existing else None,
            record=payload,
        ),
        existing["id"] if existing else None,
        new_identifier=identifier,
    )


def test_restored_formal_card_keeps_exact_address_through_outline_package_and_cloud(
    client, domain, settings, story, tmp_path, reference
):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    domain.save(chapter_id, 0, "古宅门内只有铜铃在响。")
    confirm(
        domain,
        branch,
        ConfirmProject(base_revision=0, idempotency_key="pinned-outline-card", idea="追查铜铃", brief=BRIEF),
    )
    setting_confirmation(client, branch)
    seed = export_manuscript(domain, branch)
    first = update(client, domain, reference, cards=[card("😀渡口")])
    update(client, domain, first, cards=[card("😀渡口", technique="只留下本机的疑问。")])
    package = export_manuscript(domain, branch).model_dump()
    history = package["reference_history"]
    native = next(item for item in history["versions"] if item["revision"] == 2)
    remote = {
        **native,
        "card": {**native["card"], "technique": "用局部证据改变人物的下一步。"},
    }
    remote["id"] = reference_sync._version_id(branch, ReferenceCardVersion.model_validate(remote))
    history["versions"].append(remote)
    history["adopted_heads"][0]["revision_id"] = remote["id"]

    path = tmp_path / "restored-formal-card"
    target = Domain(Database(path / "manuscripts.sqlite"))
    import_manuscript(target, ManuscriptPackage.model_validate(package))
    endpoint = f"/api/branches/{branch}/outline-workspace"
    address = {field: remote[field] for field in ("session_id", "card_id", "revision")}
    pinned = {**address, "version_id": remote["id"]}
    with TestClient(create_app(replace(settings, data_dir=path))) as restored:
        restored.headers["X-Ainovel-CSRF"] = restored.get("/api/session").json()["csrf"]
        available = restored.get(endpoint + "/reference-options")
        assert available.status_code == 200, available.text
        assert available.json() == [
            {
                **pinned,
                **{key: remote["card"][key] for key in ("technique", "conditions", "exceptions")},
                "source_title": remote["citation"]["title"],
            }
        ]
        source = restored.get(f"/api/branches/{branch}/reference-cards/{pinned['version_id']}/evidence")
        assert source.status_code == 200 and source.json()["source_state"] == "metadata_only"
        assert not source.json()["excerpt"]
        confirmation_id = restored.get(endpoint + "/setting-options").json()["confirmation_id"]

        def draft(ref):
            return {
                "base_revision": 0,
                "confirmation_id": confirmation_id,
                "plan": plan({**chapter_plan(chapter_id, "查证铜铃来历"), "reference_cards": [ref]}),
            }

        ambiguous = restored.put(endpoint, json=draft(address))
        assert ambiguous.status_code == 409 and ambiguous.json()["code"] == "reference_citation"
        incoherent = restored.put(endpoint, json=draft({**pinned, "revision": 1}))
        assert incoherent.status_code == 404 and incoherent.json()["code"] == "reference_citation"
        assert not restored.get(endpoint).json()["draft"]
        adopted = put_and_confirm(restored, endpoint, draft(pinned))["confirmed"]
    assert adopted["setting_basis"]["reference_cards"] == available.json()
    assert adopted["plan"]["volumes"][0]["units"][0]["chapters"][0]["reference_cards"] == [pinned]
    finalized = export_manuscript(target, branch)
    content = finalized.model_dump_json()
    assert SOURCE not in content and "😀渡口" not in content and '"excerpts"' not in content
    selected = finalized.outline_history.versions[0]
    assert selected.reference_cards[0].version_id == remote["id"]
    forged = finalized.model_dump()
    forged["outline_history"]["versions"][0]["reference_cards"][0]["technique"] = "改写冻结的手法"
    restored_again = Domain(Database(tmp_path / "restored-again.sqlite"))
    with pytest.raises(DomainError, match="冻结摘要"):
        import_manuscript(restored_again, ManuscriptPackage.model_validate(forged))
    missing = finalized.model_copy(update={"reference_history": None})
    with pytest.raises(DomainError, match="准确手法卡来源"):
        import_manuscript(restored_again, missing)
    import_manuscript(restored_again, finalized)
    assert (
        read_outline(restored_again, branch)["confirmed"]["setting_basis"]["reference_cards"]
        == available.json()
    )

    provision = "test-pinned-card-cloud-provision-token"
    origin = "http://127.0.0.1:8888"
    cloud_endpoint = f"/api/sync/works/{branch}"
    receiver = Domain(Database(tmp_path / "cloud-receiver.sqlite"))
    import_manuscript(receiver, seed)
    with TestClient(create_sync_app(tmp_path / "pinned-card-cloud.sqlite", provision)) as cloud:
        pushes = []

        def provision_device(device_id):
            result = cloud.post(
                cloud_endpoint + "/devices",
                headers={"Authorization": f"Bearer {provision}"},
                json={"device_id": device_id},
            )
            assert result.status_code == 201, result.text
            return result.json()["token"]

        def transport(sync_origin, token, method, route, payload, *, ledger_id=None):
            result = cloud.request(method, route, headers=_headers(token, ledger_id), json=payload)
            if not result.is_success:
                error = result.json()
                raise DomainError(error["code"], error["message"], result.status_code)
            if method == "POST" and route.endswith("/push"):
                pushes.append(payload["object_id"])
            return result.json()

        left, right = DesktopSync(target, transport), DesktopSync(receiver, transport)
        source_token, receiver_token = provision_device("restored-author"), provision_device("clean-reader")
        left.enable(branch, origin, source_token)
        right.enable(branch, origin, receiver_token)
        uploaded = left.run(branch, origin, source_token)
        assert not uploaded["deferred"] and not uploaded["conflicts"], uploaded
        assert pushes.index(f"reference-card-version:{remote['id']}") < pushes.index(
            f"outline-version:{selected.id}"
        )
        downloaded = right.run(branch, origin, receiver_token)
        assert not downloaded["deferred"] and not downloaded["conflicts"], downloaded
        adopted_in_cloud = read_outline(receiver, branch)["confirmed"]
        assert adopted_in_cloud["setting_basis"]["reference_cards"] == available.json()


def test_outline_package_restores_frozen_cards_and_defers_advanced_promise_records(
    client,
    domain,
    story,
    tmp_path,
    reference,
):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    chapter = domain.save(chapter_id, 0, "旧铃归还原主。")
    project = confirm(
        domain,
        branch,
        ConfirmProject(
            base_revision=0,
            idempotency_key="outline-package-project",
            idea="找到铃主人",
            brief=BRIEF,
        ),
    )
    settings = setting_confirmation(client, branch)
    known_card = {
        "id": "hidden-clue",
        "observation": "先出现旁证。",
        "interpretation": "压住未解疑问。",
        "technique": "先写门环刻纹。",
        "conditions": "人物能够核对旁证。",
        "exceptions": "替换原书地名及句子。",
        "evidence": SOURCE,
    }
    accepted = update(client, domain, reference, cards=[known_card])
    reference_id = {
        "session_id": accepted["id"],
        "card_id": known_card["id"],
        "revision": accepted["revision"],
    }
    payload = plan({**chapter_plan(chapter_id, "追查铃声"), "reference_cards": [reference_id]})
    payload["new_promises"] = [
        {"title": "铃纹的来源", "notes": "计划检查门环。", **window(chapter_id)},
        {"title": "归还铜铃", "notes": "需要明确真正失主。", **window(chapter_id)},
    ]
    saved = put_and_confirm(
        client,
        f"/api/branches/{branch}/outline-workspace",
        {
            "base_revision": 0,
            "confirmation_id": settings,
            "plan": payload,
        },
    )
    planned = export_manuscript(domain, branch)
    first = planned.outline_history.versions[0]
    assert first.id == saved["confirmed"]["id"]
    assert first.project_version_id == project["id"] and first.settings_version_id == settings
    assert not planned.outline_history.required_story_records
    alternate = first.model_copy(update={"id": "side-outline", "parent_id": first.id})
    packed_data = planned.model_dump()
    packed_data["outline_history"]["versions"].append(alternate.model_dump())
    packed_data["outline_history"]["source_devices"] = {first.id: "workstation-original"}
    restored = Domain(Database(tmp_path / "outline-planned.sqlite"))
    result = import_manuscript(restored, ManuscriptPackage.model_validate(packed_data))
    assert result["outline_restore"] == {"status": "adopted", "revision_id": first.id}
    view = read_outline(restored, branch)
    assert not view["stale"]
    assert view["confirmed"]["setting_basis"]["reference_cards"][0]["technique"] == known_card["technique"]
    assert (
        view["confirmed"]["setting_basis"]["manuscript_outline_source"]["recorded_device_id"]
        == "workstation-original"
    )
    assert all(row["state"] == "planned" for row in view["promises"])
    records = graph(restored, branch)["records"]
    assert len(records) == 2 and all(row["revision"] == 0 for row in records)
    reexported = export_manuscript(restored, branch).outline_history
    assert {row.id: row for row in reexported.versions} == {first.id: first, alternate.id: alternate}
    with restored.db.connect() as tx:
        assert not tx.execute("SELECT 1 FROM reference_sessions").fetchone()

    changed_project = planned.model_dump()
    changed_project.pop("story_history", None)
    prior_project = changed_project["project_history"]["versions"][0]
    changed_project["project_history"]["versions"].append(
        {
            **prior_project,
            "id": "new-project-basis",
            "parent_id": prior_project["id"],
            "idea": "改用主角查线索",
        }
    )
    changed_project["project_history"]["adopted_revision_id"] = "new-project-basis"
    historical = Domain(Database(tmp_path / "outline-old-project.sqlite"))
    pending_project = import_manuscript(historical, ManuscriptPackage.model_validate(changed_project))[
        "outline_restore"
    ]
    assert pending_project["status"] == "deferred" and "另一立项" in pending_project["reason"]
    assert export_manuscript(historical, branch).outline_history.adopted_revision_id == first.id
    assert graph(historical, branch)["records"] == []

    damaged = planned.model_dump()
    damaged["outline_history"]["versions"][0]["reference_cards"] = []
    rejected = Domain(Database(tmp_path / "outline-damaged.sqlite"))
    with pytest.raises(DomainError, match="手法卡缺少"):
        import_manuscript(rejected, ManuscriptPackage.model_validate(damaged))
    missing_settings = planned.model_dump()
    missing_settings.pop("settings_history")
    with pytest.raises(DomainError, match="大纲引用的设定版本"):
        import_manuscript(rejected, ManuscriptPackage.model_validate(missing_settings))
    with rejected.db.connect() as tx:
        assert not tx.execute("SELECT 1 FROM branches").fetchone()

    to_resolve = next(
        row for row in graph(domain, branch)["records"] if row["payload"]["title"] == "归还铜铃"
    )
    actual = {
        **to_resolve["payload"],
        "stages": [
            {"status": "planned"},
            {
                "status": "resolved",
                "sources": [
                    {
                        "chapter_id": chapter_id,
                        "revision": chapter["revision"],
                        "start": 0,
                        "end": len(chapter["content"]),
                        "quote": chapter["content"],
                    }
                ],
            },
        ],
    }
    with domain.db.connect(True) as tx:
        record(tx, branch, actual, existing=to_resolve)
    advanced = export_manuscript(domain, branch)
    assert advanced.outline_history.required_story_records == [to_resolve["id"]]
    assert advanced.story_history
    complete = Domain(Database(tmp_path / "outline-with-actual.sqlite"))
    adopted = import_manuscript(complete, advanced)
    assert adopted["outline_restore"]["status"] == "adopted"
    complete_statuses = {
        row["promise_id"]: row["state"] for row in read_outline(complete, branch)["promises"]
    }
    assert complete_statuses[to_resolve["id"]] == "resolved"
    false_seed = planned.model_dump()
    false_seed["outline_history"]["versions"][0]["promise_sources"][to_resolve["id"]] = actual
    with pytest.raises(DomainError, match="初始伏笔计划"):
        import_manuscript(rejected, ManuscriptPackage.model_validate(false_seed))
    with rejected.db.connect() as tx:
        assert not tx.execute("SELECT 1 FROM story_records").fetchone()
    deferred = Domain(Database(tmp_path / "outline-deferred.sqlite"))
    pending = import_manuscript(deferred, advanced.model_copy(update={"story_history": None}))[
        "outline_restore"
    ]
    assert pending["status"] == "deferred" and to_resolve["id"] in pending["reason"]
    assert export_manuscript(deferred, branch).outline_history == advanced.outline_history
    assert read_outline(deferred, branch)["confirmed"] is None
    assert graph(deferred, branch)["records"] == []
    with deferred.db.connect(True) as tx:
        existing = record(tx, branch, to_resolve["payload"], identifier=to_resolve["id"])
        assert outline_archive.restore_outline_archive(tx, branch)["status"] == "deferred"
        record(tx, branch, actual, existing={"id": existing, "revision": 0})
        assert outline_archive.restore_outline_archive(tx, branch)["status"] == "adopted"
    actual_states = {row["promise_id"]: row["state"] for row in read_outline(deferred, branch)["promises"]}
    assert actual_states[to_resolve["id"]] == "resolved"


def test_formal_outline_depending_on_stable_project_settings_and_frozen_cards(
    client, domain, story, tmp_path, reference
):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "师姐接过铜铃。")
    target = prepare_target(domain, branch, tmp_path / "target-outline.sqlite")
    project = confirm(
        domain,
        branch,
        ConfirmProject(base_revision=0, idempotency_key="outline-project", idea="寻找铜铃主人", brief=BRIEF),
    )
    settings = setting_confirmation(client, branch)
    card = {
        "id": "follow-hint",
        "observation": "只提供一部分线索。",
        "interpretation": "人物可以随后继续查证。",
        "technique": "先取得局部证据再确定行动。",
        "conditions": "仍有机会继续求证。",
        "exceptions": "替换原场景、道具和原句。",
        "evidence": SOURCE,
    }
    accepted = update(client, domain, reference, cards=[card])
    card_ref = {"session_id": accepted["id"], "card_id": card["id"], "revision": accepted["revision"]}
    payload = plan({**chapter_plan(chapter["id"], "查证铜铃主人"), "reference_cards": [card_ref]})
    payload["new_promises"] = [
        {"title": "铜铃归给谁", "notes": "最终解释主人身份。", **window(chapter["id"])}
    ]
    endpoint = f"/api/branches/{branch}/outline-workspace"
    confirmed = put_and_confirm(
        client, endpoint, {"base_revision": 0, "confirmation_id": settings, "plan": payload}
    )
    promise_id = confirmed["promises"][0]["promise_id"]
    with domain.db.connect() as tx:
        project_source, project_head = project_sync.draft_project_objects(tx, branch)
        settings_source, settings_head = settings_sync.draft_settings_objects(tx, branch)
        wrapper, head = outline_sync.draft_outline_objects(tx, branch)
    version = wrapper.version
    assert version.project_version_id == project["id"] and version.settings_version_id == settings
    assert version.id == confirmed["confirmed"]["id"]
    assert version.promise_sources[promise_id].stages[0].status == "planned"
    assert not version.promise_sources[promise_id].sources
    assert version.reference_cards[0].conditions == card["conditions"]
    assert version.reference_cards[0].exceptions == card["exceptions"]
    assert SOURCE not in wrapper.model_dump_json()
    object_id = f"outline-version:{version.id}"
    assert outline_sync.parse_outline_object(branch, object_id, wrapper.model_dump(), False) == wrapper
    with pytest.raises(DomainError, match="同一作品"):
        outline_sync.parse_outline_object("other-work", object_id, wrapper.model_dump(), False)

    source_existing = graph(domain, branch)["records"][0]
    evidence = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(chapter["content"]),
        "quote": chapter["content"],
    }
    actual = {
        **source_existing["payload"],
        "stages": [{"status": "planned"}, {"status": "resolved", "sources": [evidence]}],
    }
    with domain.db.connect(True) as tx:
        record(tx, branch, actual, existing=source_existing)
        assert outline_sync.draft_outline_objects(tx, branch) == [wrapper, head]
    with target.db.connect(True) as tx:
        project_sync.observe_project_version(tx, branch, project_source.version, receipt("project-version"))
        settings_sync.observe_settings_version(
            tx, branch, settings_source.version, receipt("settings-version")
        )
        outline_sync.observe_outline_version(tx, branch, version, receipt("outline-version"))
        with pytest.raises(DomainError, match="待采用对应立项"):
            outline_sync.apply_outline_head(tx, branch, head, receipt("outline-head"))
        project_sync.apply_project_head(tx, branch, project_head, receipt("project-head"))
        with pytest.raises(DomainError, match="待采用对应设定"):
            outline_sync.apply_outline_head(tx, branch, head, receipt("outline-head"))
        assert outline_sync.read_outline_head(tx, branch) is None
        assert not tx.execute("SELECT 1 FROM story_records").fetchone()
        settings_sync.apply_settings_head(tx, branch, settings_head, receipt("settings-head"))
        outline_sync.apply_outline_head(tx, branch, head, receipt("outline-head"))
        assert outline_sync.read_outline_head(tx, branch) == version.id
        assert outline_sync.draft_outline_objects(tx, branch) == [head]
        assert not tx.execute("SELECT 1 FROM reference_sessions").fetchone()

    view = read_outline(target, branch)
    assert not view["stale"]
    assert view["confirmed"]["id"] != version.id
    assert view["confirmed"]["setting_basis"]["confirmation_id"] != settings
    assert view["confirmed"]["setting_basis"]["reference_cards"][0]["revision"] == card_ref["revision"]
    assert view["promises"][0]["promise_id"] == promise_id and view["promises"][0]["state"] == "planned"
    local = graph(target, branch)["records"][0]
    changed = put_and_confirm(
        client,
        endpoint,
        {
            "base_revision": confirmed["confirmed"]["revision"],
            "confirmation_id": settings,
            "plan": {**confirmed["confirmed"]["plan"], "summary": "追查门环刻痕和铜铃"},
        },
    )
    with domain.db.connect() as tx:
        new_version = outline_sync.read_outline_version(tx, branch, changed["confirmed"]["id"])
    assert new_version.parent_id == version.id
    assert new_version.promise_sources == version.promise_sources
    with target.db.connect(True) as tx:
        # The same source head replay must never reset a later valid local stage.
        record(tx, branch, actual, existing=local)
        outline_sync.apply_outline_head(tx, branch, head, receipt("outline-head"))
        outline_sync.observe_outline_version(tx, branch, new_version, receipt("new-outline-version", 2))
        outline_sync.apply_outline_head(
            tx, branch, outline_sync.head_object(branch, new_version.id), receipt("new-outline-head", 3)
        )
        assert (
            tx.execute(
                "SELECT COUNT(*) FROM story_record_history WHERE record_id=?", (promise_id,)
            ).fetchone()[0]
            == 2
        )
    assert read_outline(target, branch)["promises"][0]["state"] == "resolved"


def test_outline_with_nonplan_graph_source_waits_without_fabricating_or_writing_seed(
    client, domain, story, tmp_path
):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "她从师姐手里取走铜铃。")
    target = prepare_target(domain, branch, tmp_path / "pending-outline.sqlite")
    existing_actual = {
        "kind": "promise",
        "title": "谁先拿到铜铃",
        "stages": [
            {
                "status": "resolved",
                "sources": [
                    {
                        "chapter_id": chapter["id"],
                        "revision": chapter["revision"],
                        "start": 0,
                        "end": len(chapter["content"]),
                        "quote": chapter["content"],
                    },
                ],
            }
        ],
    }
    with domain.db.connect(True) as tx:
        known = record(
            tx, branch, {"kind": "promise", "title": "铃身的缺口", "stages": [{"status": "planned"}]}
        )
        actual = record(tx, branch, existing_actual)
    payload = plan({**chapter_plan(chapter["id"], "追回师姐"), "setting_ids": []})
    payload["promises"] = [{"promise_id": key, **window(chapter["id"])} for key in (known, actual)]
    put_and_confirm(
        client, f"/api/branches/{branch}/outline-workspace", {"base_revision": 0, "plan": payload}
    )
    with domain.db.connect() as tx:
        wrapper, head = outline_sync.draft_outline_objects(tx, branch)
    assert set(wrapper.version.promise_sources) == {known}
    with target.db.connect(True) as tx:
        outline_sync.observe_outline_version(tx, branch, wrapper.version, receipt("outline-version"))
        with pytest.raises(DomainError, match="等待对应故事记录"):
            outline_sync.apply_outline_head(tx, branch, head, receipt("outline-head"))
        assert outline_sync.read_outline_head(tx, branch) is None
        assert not tx.execute("SELECT 1 FROM story_records").fetchone()
        record(tx, branch, existing_actual, identifier=actual)
        outline_sync.apply_outline_head(tx, branch, head, receipt("outline-head"))
    state = {row["id"]: row["payload"]["stages"][-1]["status"] for row in graph(target, branch)["records"]}
    assert state == {known: "planned", actual: "resolved"}


def test_desktop_outline_cloud_conflict_and_synchronized_graph_dependency(client, domain, story, tmp_path):
    branch = story["branch_id"]
    receiver = prepare_target(domain, branch, tmp_path / "desktop-outline-other.sqlite")
    origin = "http://127.0.0.1:8888"
    provision = "isolated-outline-sync-provision-abcdefgh"
    endpoint = f"/api/sync/works/{branch}"

    def revise(target, summary, extra_window=None):
        adopted = read_outline(target, branch)["confirmed"]
        content = {**adopted["plan"], "summary": summary}
        if extra_window:
            content["promises"] = [*content["promises"], extra_window]
        saved = save_outline(
            target,
            branch,
            SaveOutline(
                base_revision=adopted["revision"],
                confirmation_id=adopted["setting_basis"]["confirmation_id"],
                plan=content,
            ),
        )
        return confirm_outline(target, branch, ConfirmOutline(base_revision=saved["draft"]["revision"]))

    with TestClient(create_sync_app(tmp_path / "outline-cloud.sqlite", provision)) as cloud:

        def grant(device):
            result = cloud.post(
                endpoint + "/devices",
                headers={"Authorization": f"Bearer {provision}"},
                json={"device_id": device},
            )
            assert result.status_code == 201, result.text
            return result.json()["token"]

        def transport(sync_origin, token, method, route, payload, *, ledger_id=None):
            response = cloud.request(method, route, headers=_headers(token, ledger_id), json=payload)
            if not response.is_success:
                error = response.json()
                raise DomainError(error["code"], error["message"], response.status_code)
            return response.json()

        left, right = DesktopSync(domain, transport), DesktopSync(receiver, transport)
        left_token, right_token = grant("outline-left"), grant("outline-right")
        left.enable(branch, origin, left_token)
        right.enable(branch, origin, right_token)
        assert not left.run(branch, origin, left_token)["conflicts"]
        assert not right.run(branch, origin, right_token)["conflicts"]

        chapter = domain.save(story["chapter_id"], 0, "师姐在门内接过铜铃。")
        project = confirm(
            domain,
            branch,
            ConfirmProject(
                base_revision=0, idempotency_key="outline-initial-project", idea="送还铜铃", brief=BRIEF
            ),
        )
        settings_id = setting_confirmation(client, branch)
        content = plan(chapter_plan(chapter["id"], "辨认铃声"))
        content["new_promises"] = [{"title": "铜铃最终归谁", **window(chapter["id"])}]
        accepted = put_and_confirm(
            client,
            f"/api/branches/{branch}/outline-workspace",
            {"base_revision": 0, "confirmation_id": settings_id, "plan": content},
        )
        base_id = accepted["confirmed"]["id"]
        promise_id = accepted["promises"][0]["promise_id"]
        assert not left.run(branch, origin, left_token)["conflicts"]
        synced = right.run(branch, origin, right_token)
        assert not synced["conflicts"] and not synced["deferred"], synced
        assert synced["outline_source"]["id"] == base_id
        mirrored = read_outline(receiver, branch)
        assert mirrored["confirmed"]["id"] != base_id and not mirrored["stale"]
        assert mirrored["settings"][0]["setting"]["id"] == "bell"
        assert mirrored["promises"][0]["state"] == "planned"
        assert mirrored["promises"][0]["promise_id"] == promise_id
        assert receiver.chapter(chapter["id"])["content"] == chapter["content"]
        ordered = cloud.get(
            endpoint + "/pull",
            params={"after": 0, "limit": 50},
            headers={"Authorization": f"Bearer {right_token}"},
        )
        assert ordered.status_code == 200, ordered.text
        changes = ordered.json()["changes"]
        chapter_seq = next(
            row["sequence"]
            for row in changes
            if row["object_id"] == f"chapter:{chapter['id']}"
            and row["payload"]["head"]["content"] == chapter["content"]
        )
        story_head = next(row for row in changes if row["object_id"] == f"story:{promise_id}")
        story_version = next(
            row
            for row in changes
            if row["object_id"] == f"story-version:{story_head['payload']['revision_id']}"
        )
        outline_seq = next(row["sequence"] for row in changes if row["object_id"] == f"outline:{branch}")
        assert chapter_seq < story_version["sequence"] < story_head["sequence"] < outline_seq
        with receiver.db.connect() as tx:
            assert project_sync.read_project_head(tx, branch) == project["id"]
            assert settings_sync.read_settings_head(tx, branch) == settings_id
            assert outline_sync.read_outline_head(tx, branch) == base_id

        left_edit = revise(domain, "找到铃铛的原主人")["confirmed"]["id"]
        right_edit = revise(receiver, "核实师姐为何寄存铃铛")["confirmed"]["id"]
        assert not left.run(branch, origin, left_token)["conflicts"]
        competing = right.run(branch, origin, right_token)
        conflict = next(row for row in competing["conflicts"] if row["object_id"] == f"outline:{branch}")
        compared = right.compare(branch, origin, right_token, conflict["id"])
        assert compared["remote"]["base"]["payload"]["revision_id"] == base_id
        assert set(compared["outline_versions"]) == {base_id, left_edit, right_edit}
        assert compared["outline_versions"][right_edit]["plan"]["summary"] == "核实师姐为何寄存铃铛"
        resolved = right.resolve(
            branch,
            origin,
            right_token,
            conflict["id"],
            Resolve(
                origin=origin,
                token=right_token,
                choice="current",
                parent_revision=compared["remote"]["head_revision"],
                local_sha256=compared["local_sha256"],
                idempotency_key="adopt-cloud-outline",
            ),
        )
        assert not resolved["conflicts"] and not resolved["deferred"], resolved
        assert read_outline(receiver, branch)["confirmed"]["plan"]["summary"] == "找到铃铛的原主人"
        with receiver.db.connect() as tx:
            assert outline_sync.read_outline_head(tx, branch) == left_edit
            assert (
                outline_sync.read_outline_version(tx, branch, right_edit).plan.summary
                == "核实师姐为何寄存铃铛"
            )
        assert not left.run(branch, origin, left_token)["conflicts"]

        proof = {
            "chapter_id": chapter["id"],
            "revision": chapter["revision"],
            "start": 0,
            "end": len(chapter["content"]),
            "quote": chapter["content"],
        }
        local_promise = graph(receiver, branch)["records"][0]
        with receiver.db.connect(True) as tx:
            record(
                tx,
                branch,
                {
                    **local_promise["payload"],
                    "stages": [{"status": "planned"}, {"status": "resolved", "sources": [proof]}],
                },
                existing=local_promise,
            )
        with domain.db.connect(True) as tx:
            later_promise_id = record(
                tx,
                branch,
                {
                    "kind": "promise",
                    "title": "白纸封条出自何处",
                    "stages": [{"status": "resolved", "sources": [proof]}],
                },
            )
        later = revise(
            domain, "核对封条来源并保存", {"promise_id": later_promise_id, **window(chapter["id"])}
        )["confirmed"]["id"]
        assert not left.run(branch, origin, left_token)["conflicts"]
        synchronized = right.run(branch, origin, right_token)
        assert not synchronized["deferred"] and not synchronized["conflicts"], synchronized
        assert {(row["promise_id"], row["state"]) for row in read_outline(receiver, branch)["promises"]} == {
            (promise_id, "resolved"),
            (later_promise_id, "resolved"),
        }
        assert {row["id"] for row in graph(receiver, branch)["records"]} == {promise_id, later_promise_id}
        with receiver.db.connect() as tx:
            assert outline_sync.read_outline_head(tx, branch) == later
            remote = outline_sync.read_outline_version(tx, branch, later)
            assert remote.plan.promises[-1].promise_id == later_promise_id
            assert later_promise_id not in remote.promise_sources
