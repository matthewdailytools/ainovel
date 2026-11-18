import json
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ainovel import conception, project_sync, settings_sync
from ainovel.api import create_app
from ainovel.cloud_sync import create_sync_app
from ainovel.db import Database, DomainError
from ainovel.desktop_sync import DesktopSync, Resolve
from ainovel.domain import Domain
from ainovel.manuscript_exchange import ManuscriptPackage, export_manuscript, import_manuscript
from ainovel.project_brief import ConfirmProject, confirm
from ainovel.setting_review import confirmed_world_basis
from test_conception_workbench import basis
from test_project_brief import BRIEF


def receipt(name, sequence=1):
    return SimpleNamespace(device_id="writing-laptop", change_id=name, sequence=sequence)


def enable(domain):
    with domain.db.connect(True) as tx:
        tx.executescript(project_sync.SCHEMA + settings_sync.SCHEMA)


def save_settings(domain, branch_id, categories, origins, reconcile):
    with domain.db.connect(True) as tx:
        branch = conception._branch(tx, branch_id)
        workspace = conception._workspace(tx, branch_id)
        conception._persist(tx, branch, workspace, categories, origins)
        return conception.confirm_tx(
            tx,
            branch_id,
            conception.ConfirmConception(
                base_revision=workspace["revision"] + 1,
                base_story_revision=branch["story_revision"],
                idempotency_key=f"confirmed-at-source-{workspace['revision']}",
                note="作者已核对取材来源和约束",
                reconciled_project_items=reconcile,
                reviewed_setting_findings=[found["id"] for found in conception.setting_findings(categories)],
            ),
        )


def test_settings_package_keeps_stale_project_side_branch_and_exact_source_evidence(domain, story, tmp_path):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    chapter = domain.save(chapter_id, 0, "旧铃📯在门外响。")
    first = confirm(
        domain,
        branch,
        ConfirmProject(
            base_revision=0,
            idempotency_key="m3-file-project-a",
            idea="追查来源",
            brief=BRIEF,
        ),
    )
    categories = conception.snapshot(domain, branch)["categories"]
    world = next(row for row in categories if row["id"] == "world")
    world["items"] = [{"id": "bell", "title": "门外的号角", "detail": "响声来自号角。", "links": []}]
    save_settings(
        domain,
        branch,
        categories,
        {
            "bell": {
                "origin": "ai",
                "artifact_id": "source-only-candidate",
                "project_confirmation_id": first["id"],
                "project_revision": 1,
                "evidence": {
                    "chapter_id": chapter_id,
                    "revision": chapter["revision"],
                    "start": 2,
                    "end": 4,
                    "quote": "📯",
                },
            }
        },
        [],
    )
    assert conception.snapshot(domain, branch)["confirmation"]["current"]
    source_package = export_manuscript(domain, branch)
    current = Domain(Database(tmp_path / "current-settings.sqlite"))
    import_manuscript(current, source_package)
    assert conception.snapshot(current, branch)["confirmation"]["current"]
    with current.db.connect() as tx:
        assert [item["id"] for item in confirmed_world_basis(tx, branch)["items"]] == []
        row = tx.execute("SELECT payload FROM artifacts WHERE kind=?", (conception.CONFIRMATION,)).fetchone()
        assert json.loads(row["payload"])["sources"][0]["current"]
    second = confirm(
        domain,
        branch,
        ConfirmProject(
            base_revision=1,
            idempotency_key="m3-file-project-b",
            idea="改为追查船夫",
            brief=BRIEF,
        ),
    )
    pack = export_manuscript(domain, branch)
    assert pack.project_history.adopted_revision_id == second["id"]
    root = pack.settings_history.versions[0]
    assert pack.settings_history.adopted_revision_id == root.id
    assert root.project_version_id == first["id"]
    payload = pack.model_dump()
    payload["settings_history"]["source_devices"] = {root.id: "writer-remote"}
    payload["settings_history"]["versions"].append(
        root.model_copy(
            update={
                "id": "alternate-settings",
                "parent_id": root.id,
                "note": "另一版保留。",
            }
        ).model_dump()
    )

    source = payload["revisions"][-1]
    payload["source_revisions"] = [source]
    replacement = "同编号的本机稿没有号角。"
    payload["revisions"][-1] = {
        **source,
        "id": "different-native-head",
        "content": replacement,
        "sha256": sha256(replacement.encode()).hexdigest(),
    }
    frozen = ManuscriptPackage.model_validate(payload)
    restored = Domain(Database(tmp_path / "restored-settings.sqlite"))
    import_manuscript(restored, frozen)
    assert restored.chapter(chapter_id)["content"] == replacement
    state = conception.snapshot(restored, branch)
    assert not state["confirmation"]["current"]
    history = export_manuscript(restored, branch).settings_history
    assert history == frozen.settings_history
    with restored.db.connect() as tx:
        row = tx.execute(
            "SELECT payload,basis FROM artifacts WHERE kind=?", (conception.CONFIRMATION,)
        ).fetchone()
        official = json.loads(row["payload"])
        basis = json.loads(row["basis"])
        assert official["project_confirmation_id"] == first["id"]
        assert basis["manuscript_settings_source"]["recorded_device_id"] == "writer-remote"
        assert "sync_settings_source" not in basis
        assert official["origins"]["bell"]["origin"] == "archive"
        assert official["origins"]["bell"]["source_origin"] == "ai"
        assert official["origins"]["bell"]["source_artifact_id"] == "source-only-candidate"
        assert official["origins"]["bell"]["evidence_revision_id"] == source["id"]
        assert official["sources"][0]["evidence"]["quote"] == "📯"
        assert not official["sources"][0]["current"]

    missing = {**payload, "source_revisions": []}
    unopened = Domain(Database(tmp_path / "unopened-settings.sqlite"))
    with pytest.raises(DomainError, match="准确正文修订"):
        import_manuscript(unopened, ManuscriptPackage.model_validate(missing))
    with unopened.db.connect() as tx:
        assert not tx.execute("SELECT 1 FROM novels").fetchone()
        assert not tx.execute("SELECT 1 FROM manuscript_settings_versions").fetchone()

    with pytest.raises(DomainError, match="缺少设定引用的立项版本"):
        import_manuscript(unopened, ManuscriptPackage.model_validate({**payload, "project_history": None}))
    damaged = frozen.model_dump()
    damaged["settings_history"]["versions"][0]["origins"]["bell"]["evidence"]["quote_sha256"] = "0" * 64
    with pytest.raises(DomainError, match="设定引用与源正文不符"):
        import_manuscript(unopened, ManuscriptPackage.model_validate(damaged))
    unrelated = frozen.model_dump()
    unrelated["settings_history"]["versions"][0]["origins"]["bell"]["evidence"]["chapter_id"] = (
        "different-work-chapter"
    )
    with pytest.raises(DomainError, match="准确正文修订"):
        import_manuscript(unopened, ManuscriptPackage.model_validate(unrelated))
    with unopened.db.connect() as tx:
        assert not tx.execute("SELECT 1 FROM branches").fetchone()
    with pytest.raises(ValidationError, match="设定父修订"):
        ManuscriptPackage.model_validate(
            {
                **payload,
                "settings_history": {
                    "adopted_revision_id": "alternate-settings",
                    "versions": [payload["settings_history"]["versions"][-1]],
                },
            }
        )


def test_native_settings_sync_rechecks_reviews_sources_and_adopted_project(domain, story, tmp_path):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "响起📯，并非铜铃。")
    target = Domain(Database(tmp_path / "target.sqlite"))
    import_manuscript(target, export_manuscript(domain, branch))
    enable(domain)
    enable(target)
    first, second = [
        confirm(
            domain,
            branch,
            ConfirmProject(
                base_revision=index,
                idempotency_key=f"settings-origin-{index}",
                idea="追查铜铃的来历",
                brief=BRIEF,
            ),
        )
        for index in range(2)
    ]
    categories = conception.snapshot(domain, branch)["categories"]
    world = next(row for row in categories if row["id"] == "world")
    world["items"] = [
        {"id": "author-rule", "title": "鸣响", "detail": "门外铜铃沉默。", "links": []},
        {"id": "body-rule", "title": "鸣响", "detail": "门外听到号角。", "links": []},
    ]
    origins = {
        "body-rule": {
            "origin": "ai",
            "artifact_id": "remote-candidate",
            "project_confirmation_id": first["id"],
            "project_revision": 1,
            "evidence": {
                "chapter_id": chapter["id"],
                "revision": chapter["revision"],
                "start": 2,
                "end": 4,
                "quote": "📯",
            },
        },
    }
    formal = save_settings(domain, branch, categories, origins, ["body-rule"])
    assert formal["confirmation"]["current"]
    with domain.db.connect() as tx:
        projects = [
            value.version
            for value in project_sync.draft_project_objects(tx, branch)
            if isinstance(value, project_sync.ProjectVersionObject)
        ]
        wrapper, head = settings_sync.draft_settings_objects(tx, branch)
    version = wrapper.version
    assert version.project_version_id == second["id"]
    assert version.origins["body-rule"].project_version_id == first["id"]
    assert version.reviewed_setting_findings
    object_id = f"settings-version:{version.id}"
    payload = wrapper.model_dump()
    assert settings_sync.parse_settings_object(branch, object_id, payload, False) == wrapper
    with pytest.raises(DomainError, match="同一作品"):
        settings_sync.parse_settings_object("other-work", object_id, payload, False)
    payload["version"]["reviewed_setting_findings"] = []
    with pytest.raises(DomainError, match="冲突核对"):
        settings_sync.parse_settings_object(branch, object_id, payload, False)

    with target.db.connect(True) as tx:
        with pytest.raises(DomainError, match="立项历史"):
            settings_sync.observe_settings_version(tx, branch, version, receipt("setting-v1"))
        for number, project in enumerate(projects, 1):
            project_sync.observe_project_version(tx, branch, project, receipt(f"project-v{number}", number))
        body = version.origins["body-rule"]
        broken = body.model_copy(update={"evidence": body.evidence.model_copy(update={"start": 3})})
        with pytest.raises(DomainError, match="选区无效"):
            settings_sync.observe_settings_version(
                tx,
                branch,
                version.model_copy(update={"origins": {**version.origins, "body-rule": broken}}),
                receipt("setting-v1"),
            )
        settings_sync.observe_settings_version(tx, branch, version, receipt("setting-v1", 3))
        with pytest.raises(DomainError, match="不同内容"):
            settings_sync.observe_settings_version(
                tx, branch, version.model_copy(update={"note": "篡改历史"}), receipt("changed", 4)
            )
        project_sync.apply_project_head(
            tx, branch, project_sync.head_object(branch, first["id"]), receipt("head-p1")
        )
        with pytest.raises(DomainError, match="待采用"):
            settings_sync.apply_settings_head(tx, branch, head, receipt("settings-head"))
        assert settings_sync.read_settings_head(tx, branch) is None
        project_sync.apply_project_head(
            tx, branch, project_sync.head_object(branch, second["id"]), receipt("head-p2")
        )
        settings_sync.apply_settings_head(tx, branch, head, receipt("settings-head", 6))
        settings_sync.apply_settings_head(tx, branch, head, receipt("settings-head", 6))
        assert settings_sync.draft_settings_objects(tx, branch) == [head]
        assert settings_sync.current_settings_source(tx, branch)["id"] == version.id
        assert [item["id"] for item in confirmed_world_basis(tx, branch)["items"]] == ["author-rule"]
        rows = tx.execute(
            "SELECT payload,basis FROM artifacts WHERE kind=?", (conception.CONFIRMATION,)
        ).fetchall()
        assert len(rows) == 1
        copied = json.loads(rows[0]["payload"])
        source = json.loads(rows[0]["basis"])["sync_settings_source"]
        assert source["revision_id"] == version.id
        assert source["project_version_id"] == second["id"]
        assert copied["project_confirmation_id"] != source["project_version_id"]
        assert copied["observed"] == []
        assert copied["reconciled_project_items"] == ["body-rule"]
        assert copied["reviewed_setting_findings"] == version.reviewed_setting_findings
        assert copied["sources"][0]["evidence"]["quote"] == "📯"
        assert copied["origins"]["author-rule"]["origin"] == "sync"
        assert copied["origins"]["author-rule"]["source_origin"] == "author"
        assert copied["origins"]["body-rule"]["source_artifact_id"] == "remote-candidate"
    assert conception.snapshot(target, branch)["confirmation"]["current"]


def test_settings_head_preserves_unconfirmed_local_draft(domain, story, tmp_path):
    branch = story["branch_id"]
    target = Domain(Database(tmp_path / "draft.sqlite"))
    import_manuscript(target, export_manuscript(domain, branch))
    enable(domain)
    enable(target)
    categories = conception.snapshot(domain, branch)["categories"]
    categories[0]["items"] = [{"id": "source", "title": "源端锁定", "detail": "源端已确认", "links": []}]
    save_settings(domain, branch, categories, {}, [])
    with domain.db.connect() as tx:
        version, head = settings_sync.draft_settings_objects(tx, branch)
    with target.db.connect(True) as tx:
        workspace = conception._workspace(tx, branch)
        local = workspace["categories"]
        local[0]["items"] = [{"id": "local", "title": "本机草稿", "detail": "仍在编辑", "links": []}]
        conception._persist(tx, conception._branch(tx, branch), workspace, local)
        settings_sync.observe_settings_version(tx, branch, version.version, receipt("setting-v1"))
        with pytest.raises(DomainError, match="保留草稿"):
            settings_sync.apply_settings_head(tx, branch, head, receipt("settings-head", 2))
        assert settings_sync.read_settings_head(tx, branch) is None
        assert settings_sync.read_settings_version(tx, branch, version.version.id) == version.version
        assert conception._workspace(tx, branch)["categories"][0]["items"][0]["id"] == "local"


def test_formal_settings_sync_conflict_and_pending_project_with_real_cloud(domain, story, settings, tmp_path):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "响起📯，并非铜铃。")
    other_settings = replace(settings, data_dir=tmp_path / "other-settings")
    other = Domain(Database(other_settings.data_dir / "manuscripts.sqlite"))
    import_manuscript(other, export_manuscript(domain, branch))
    origin = "http://127.0.0.1:8888"
    provisioning = "isolated-settings-sync-provision-abcdefgh"
    prefix = f"/api/sync/works/{branch}"

    def save_project(target, base, idea, key):
        return confirm(
            target,
            branch,
            ConfirmProject(base_revision=base, idempotency_key=key, idea=idea, brief=BRIEF),
        )

    def edit_world(target, text, initial_origins=None):
        categories = conception.snapshot(target, branch)["categories"]
        world = next(row for row in categories if row["id"] == "world")
        if world["items"]:
            world["items"][0]["detail"] = text
        else:
            world["items"] = [
                {"id": "bell-rule", "title": "鸣响", "detail": text, "links": []},
                {"id": "horn-evidence", "title": "鸣响", "detail": "异响来自号角。", "links": []},
            ]
        return save_settings(target, branch, categories, initial_origins or {}, [])

    with TestClient(create_sync_app(tmp_path / "settings-cloud.sqlite", provisioning)) as cloud:

        def grant(device):
            granted = cloud.post(
                prefix + "/devices",
                headers={"Authorization": f"Bearer {provisioning}"},
                json={"device_id": device},
            )
            assert granted.status_code == 201, granted.text
            return granted.json()["token"]

        def transport(sync_origin, token, method, route, payload):
            response = cloud.request(
                method, route, headers={"Authorization": f"Bearer {token}"}, json=payload
            )
            if not response.is_success:
                detail = response.json()
                raise DomainError(detail["code"], detail["message"], response.status_code)
            return response.json()

        left, right = DesktopSync(domain, transport), DesktopSync(other, transport)
        left_token, right_token = grant("settings-left"), grant("settings-right")
        left.enable(branch, origin, left_token)
        right.enable(branch, origin, right_token)
        project = save_project(domain, 0, "根据门铃识别师姐", "m3-project-base")
        initial = edit_world(
            domain,
            "夜里只有门内才响。",
            {
                "horn-evidence": {
                    "origin": "ai",
                    "evidence": {
                        "chapter_id": chapter["id"],
                        "revision": chapter["revision"],
                        "start": 2,
                        "end": 4,
                        "quote": "📯",
                    },
                }
            },
        )
        initial_id = initial["confirmation"]["id"]
        assert not left.run(branch, origin, left_token)["conflicts"]
        first = right.run(branch, origin, right_token)
        assert not first["conflicts"] and not first["deferred"], first
        synced = conception.snapshot(other, branch)
        assert synced["confirmation"]["current"] and synced["confirmation"]["id"] != initial_id
        assert synced["sources"][0]["evidence"]["quote"] == "📯"
        assert all(row["reviewed"] for row in synced["setting_findings"])
        with other.db.connect() as tx:
            assert project_sync.read_project_head(tx, branch) == project["id"]
            assert settings_sync.read_settings_head(tx, branch) == initial_id

        left_edit = edit_world(domain, "把铃放到梁下才响。")["confirmation"]["id"]
        right_edit = edit_world(other, "只过午夜才响。")["confirmation"]["id"]
        assert not left.run(branch, origin, left_token)["conflicts"]
        right_state = right.run(branch, origin, right_token)
        conflict = next(row for row in right_state["conflicts"] if row["object_id"] == f"settings:{branch}")
        compared = right.compare(branch, origin, right_token, conflict["id"])
        assert compared["remote"]["base"]["payload"]["revision_id"] == initial_id
        assert set(compared["settings_versions"]) == {initial_id, left_edit, right_edit}
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
                idempotency_key="resolve-real-settings",
            ),
        )
        assert not resolved["conflicts"] and not resolved["deferred"], resolved
        with other.db.connect() as tx:
            assert settings_sync.read_settings_head(tx, branch) == left_edit
            kept = settings_sync.read_settings_version(tx, branch, right_edit)
            assert (
                next(row for row in kept.categories if row.id == "world").items[0].detail == "只过午夜才响。"
            )
        assert not left.run(branch, origin, left_token)["conflicts"]

        combined = "把铃放到梁下，过午夜才响。"
        route = f"/api/branches/{branch}/conception"
        with TestClient(create_app(other_settings)) as author:
            author.headers["X-Ainovel-CSRF"] = author.get("/api/session").json()["csrf"]
            draft = author.get(route).json()
            assert draft["confirmation"]["current"]
            world = next(row for row in draft["categories"] if row["id"] == "world")
            assert world["items"][0]["detail"] == "把铃放到梁下才响。"
            world["items"][0]["detail"] = combined
            request = {**basis(draft), "categories": draft["categories"]}
            inspected = author.post(route + "/setting-inspection", json=request)
            assert inspected.status_code == 200, inspected.text
            findings = inspected.json()["findings"]
            assert [finding["kind"] for finding in findings] == ["same_name"]
            saved = author.put(route, json=request)
            assert saved.status_code == 200, saved.text
            assert not saved.json()["confirmation"]["current"]
            sign = {
                **basis(saved.json()),
                "idempotency_key": "settings-author-combined-after-resolution",
                "note": f"以 {left_edit} 为底稿，参考 {right_edit}。",
            }
            needs_review = author.post(route + "/confirm", json=sign)
            assert needs_review.status_code == 409, needs_review.text
            assert needs_review.json()["code"] == "conception_setting_review"
            signed = author.post(
                route + "/confirm",
                json={**sign, "reviewed_setting_findings": [finding["id"] for finding in findings]},
            )
            assert signed.status_code == 200, signed.text
            combined_id = signed.json()["confirmation"]["id"]

        for adapter, token in ((right, right_token), (left, left_token)):
            state = adapter.run(branch, origin, token)
            assert not state["conflicts"] and not state["deferred"], state
        for device in (domain, other):
            assert conception.snapshot(device, branch)["confirmation"]["current"]
            with device.db.connect() as tx:
                assert settings_sync.read_settings_head(tx, branch) == combined_id
                version = settings_sync.read_settings_version(tx, branch, combined_id)
                assert version.parent_id == left_edit and version.project_version_id == project["id"]
                world = next(row for row in version.categories if row.id == "world")
                assert world.items[0].detail == combined
                assert version.note == sign["note"]
                for prior_id in (initial_id, left_edit, right_edit):
                    assert settings_sync.read_settings_version(tx, branch, prior_id)

        save_project(domain, 1, "作者改变铜铃的用途", "m3-project-left")
        save_project(other, 1, "作者在另一台设备改变结局", "m3-project-right")
        later = edit_world(domain, "铜铃用于认路。")["confirmation"]["id"]
        independent = domain.create_chapter(branch, "独立章")
        domain.save(independent["id"], 0, "这段正文照常同步。")
        assert not left.run(branch, origin, left_token)["conflicts"]
        next_state = right.run(branch, origin, right_token)
        project_conflict = next(
            row for row in next_state["conflicts"] if row["object_id"] == f"project:{branch}"
        )
        pending = next(
            (row for row in next_state["deferred"] if row["object_id"] == f"settings:{branch}"), None
        )
        assert pending is not None, next_state
        assert "待采用对应立项" in pending["reason"]
        assert other.chapter(independent["id"])["content"] == "这段正文照常同步。"
        with other.db.connect() as tx:
            assert settings_sync.read_settings_head(tx, branch) == combined_id
            assert settings_sync.read_settings_version(tx, branch, later)
        parent = right.compare(branch, origin, right_token, project_conflict["id"])
        accepted = right.resolve(
            branch,
            origin,
            right_token,
            project_conflict["id"],
            Resolve(
                origin=origin,
                token=right_token,
                choice="current",
                parent_revision=parent["remote"]["head_revision"],
                local_sha256=parent["local_sha256"],
                idempotency_key="resolve-project-for-settings",
            ),
        )
        assert not accepted["deferred"] and not accepted["conflicts"], accepted
        with other.db.connect() as tx:
            assert settings_sync.read_settings_head(tx, branch) == later
        assert conception.snapshot(other, branch)["confirmation"]["current"]
