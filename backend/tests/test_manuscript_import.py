from copy import deepcopy
from uuid import uuid4

import pytest


TEXT = "前言😀\n第一章 渡口\n她登上渡船。\n第二章 铜牌\n她收起铜牌。\n"


def preview(client, story, text=TEXT, encoding="utf-8-sig", raw=None):
    result = client.post(
        f"/api/branches/{story['branch_id']}/import-previews",
        files={"file": ("长篇.txt", raw if raw is not None else text.encode(encoding), "text/plain")},
        data={"encoding": encoding},
    )
    assert result.status_code == 201, result.text
    return result.json()


def confirmation(row):
    return {
        "base_story_revision": row["base_story_revision"],
        "client_request_id": uuid4().hex,
        "mappings": [
            {k: s[k] for k in ("title", "start", "end")} | {"action": "append"} for s in row["segments"]
        ],
    }


def confirm(client, story, row, body):
    return client.post(f"/api/branches/{story['branch_id']}/import-previews/{row['id']}/confirm", json=body)


def test_preview_does_not_write_and_confirmation_is_complete_idempotent(client, domain, story):
    before = domain.db.all("SELECT * FROM chapters")
    row = preview(client, story)
    assert len(row["segments"]) == 3
    assert domain.db.all("SELECT * FROM chapters") == before
    body = confirmation(row)
    result = confirm(client, story, row, body)
    assert result.status_code == 200, result.text
    assert confirm(client, story, row, body).json() == result.json()
    ids = [r["chapter_id"] for r in result.json()["results"]]
    assert "".join(domain.chapter(i)["content"] for i in ids) == TEXT
    assert len(domain.db.all("SELECT * FROM chapters")) == len(before) + 3
    changed = deepcopy(body)
    changed["mappings"][0]["title"] = "不同标题"
    assert confirm(client, story, row, changed).status_code == 409


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_chinese_encodings_preserve_original_bytes(client, domain, story, encoding):
    raw = TEXT.encode(encoding)
    row = preview(client, story, encoding=encoding, raw=raw)
    stored = domain.db.one("SELECT raw,content FROM manuscript_imports WHERE id=?", (row["id"],))
    assert stored["raw"] == raw and stored["content"] == TEXT


def test_whole_transaction_rejects_late_invalid_range_and_source_conflict(client, domain, story):
    row = preview(client, story)
    body = confirmation(row)
    body["mappings"][-1]["end"] += 1
    assert confirm(client, story, row, body).status_code == 400
    assert len(domain.db.all("SELECT * FROM chapters")) == 1
    assert not domain.db.one("SELECT receipt FROM manuscript_imports WHERE id=?", (row["id"],))["receipt"]
    domain.save(story["chapter_id"], 0, "作者并行编辑")
    assert confirm(client, story, row, confirmation(row)).status_code == 409
    assert domain.chapter(story["chapter_id"])["content"] == "作者并行编辑"


def test_duplicate_and_same_title_mapping_are_explicit_and_restore_original(client, domain, story):
    original = domain.save(story["chapter_id"], 0, "原稿保留")
    row = preview(client, story, "第一章\n新稿")
    body = confirmation(row)
    body["mappings"][0].update(
        action="replace", chapter_id=original["id"], base_revision=original["revision"]
    )
    assert confirm(client, story, row, body).status_code == 200
    assert (
        domain.db.one("SELECT content FROM revisions WHERE chapter_id=? AND revision=1", (original["id"],))[
            "content"
        ]
        == "原稿保留"
    )
    again = preview(client, story, "第一章\n新稿")
    assert again["segments"][0]["same_content"] == [original["id"]]
    body = confirmation(again)
    assert confirm(client, story, again, body).status_code == 409
    body["mappings"][0]["allow_duplicate"] = True
    assert confirm(client, story, again, body).status_code == 200


def test_manual_boundaries_and_skip_preserve_full_coverage(client, domain, story):
    row = preview(client, story)
    body = confirmation(row)
    first, second, last = body["mappings"]
    body["mappings"] = [{**first, "action": "skip"}, {**second, "end": last["end"], "title": "手动合并两章"}]
    result = confirm(client, story, row, body)
    assert result.status_code == 200, result.text
    assert result.json()["results"][0]["action"] == "skip"
    assert len(domain.db.all("SELECT * FROM chapters")) == 2


def test_corrected_split_and_append_order_survive_draft_and_receipt(client, domain, story):
    original = domain.save(story["chapter_id"], 0, "现有正文，请保留")
    front = "卷首说明😀\n"
    first = "第一章 错写的题名\n她登上渡船。\n## 这里是小节标题\n水下有响声。\n"
    second = "第二章 新桥\n她去了新桥。\n"
    text = front + first + second
    branch = story["branch_id"]
    row = preview(client, story, text)
    assert len(row["segments"]) == 4
    values = confirmation(row)
    skip, split, subsection, last = values["mappings"]
    values["mappings"] = [
        {**skip, "action": "skip"},
        {**split, "title": "渡船听声", "end": subsection["end"], "append_order": 2},
        {**last, "title": "新桥", "append_order": 1},
    ]
    endpoint = f"/api/branches/{branch}/import-previews/{row['id']}"
    saved = client.patch(endpoint + "/draft", json={"base_revision": 0, "mappings": values["mappings"]})
    assert saved.status_code == 200, saved.text
    reopened = client.get(endpoint).json()
    assert reopened["draft"] == saved.json()["draft"]
    values.update(draft_revision=reopened["draft"]["revision"], mappings=reopened["draft"]["mappings"])

    duplicate_position = deepcopy(values)
    duplicate_position["mappings"][2]["append_order"] = 2
    assert confirm(client, story, row, duplicate_position).status_code == 400
    assert client.get(endpoint).json()["receipt"] is None
    assert client.get(f"/api/branches/{branch}/chapters").json()[0]["id"] == original["id"]
    shuffled_ranges = {**values, "mappings": list(reversed(values["mappings"]))}
    assert confirm(client, story, row, shuffled_ranges).status_code == 400
    assert domain.chapter(original["id"]) == original

    result = confirm(client, story, row, values)
    assert result.status_code == 200, result.text
    receipt = result.json()
    assert receipt == client.get(endpoint).json()["receipt"]
    assert receipt == confirm(client, story, row, values).json()
    assert receipt["raw_hash"] == row["raw_hash"]
    skipped, first_receipt, second_receipt = receipt["results"]
    assert skipped["action"] == "skip"
    assert (first_receipt["position"], second_receipt["position"]) == (3, 2)
    chapters = client.get(f"/api/branches/{branch}/chapters").json()
    assert [entry["title"] for entry in chapters] == [original["title"], "新桥", "渡船听声"]
    assert all(not entry["stale"] for entry in chapters)
    assert [domain.chapter(entry["id"])["content"] for entry in chapters] == [
        original["content"],
        second,
        first,
    ]


def test_previewed_replacement_title_cannot_override_a_later_rename(client, domain, story):
    original = domain.save(story["chapter_id"], 0, "替换前的正文。")
    row = preview(client, story, "第一章 导入的标题\n替换后的正文。")
    values = confirmation(row)
    values["mappings"][0].update(
        action="replace",
        chapter_id=original["id"],
        base_revision=original["revision"],
        expected_title=original["title"],
    )
    renamed = client.patch(f"/api/chapters/{original['id']}", json={"title": "作者在另一窗修改"})
    assert renamed.status_code == 200, renamed.text
    assert confirm(client, story, row, values).status_code == 409
    assert domain.chapter(original["id"]) == renamed.json()

    values["mappings"][0]["expected_title"] = renamed.json()["title"]
    response = confirm(client, story, row, values)
    assert response.status_code == 200, response.text
    [result] = response.json()["results"]
    assert result["previous_title"] == renamed.json()["title"]
    assert result["previous_revision"] == original["revision"]
    previous = client.get(f"/api/chapters/{original['id']}/history").json()
    assert any(
        r["revision"] == original["revision"] and r["content"] == original["content"] for r in previous
    )


def test_surrogate_boundaries_and_cross_branch_targets_are_rejected(client, domain, story):
    row = preview(client, story, "😀章")
    body = confirmation(row)
    body["mappings"][0]["end"] = 1
    assert confirm(client, story, row, body).status_code == 400
    other = domain.create_novel("另一本书", "")
    target = domain.create_chapter(other["branch_id"], "另一章")
    body = confirmation(row)
    body["mappings"][0].update(action="replace", chapter_id=target["id"], base_revision=0)
    assert confirm(client, story, row, body).status_code == 409
    assert domain.chapter(target["id"])["content"] == ""


def test_saved_manual_mapping_reopens_and_conflicts_do_not_overwrite(client, domain, story):
    row = preview(client, story)
    body = confirmation(row)
    body["mappings"][0]["action"] = "skip"
    url = f"/api/branches/{story['branch_id']}/import-previews/{row['id']}/draft"
    response = client.patch(url, json={"base_revision": 0, "mappings": body["mappings"]})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["draft"]["revision"] == 1 and saved["draft"]["mappings"][0]["action"] == "skip"
    assert client.patch(url, json={"base_revision": 0, "mappings": body["mappings"]}).status_code == 409
    body["draft_revision"] = 0
    assert confirm(client, story, row, body).status_code == 409
    body["draft_revision"] = 1
    assert confirm(client, story, row, body).status_code == 200


def test_import_backup_restores_original_file_mapping_and_receipt(client, domain, story, settings, tmp_path):
    from ainovel.backups import create_backup, unpack_verified
    from ainovel.db import Database
    from ainovel.manuscript_import import get_import, public_preview
    from ainovel.domain import Domain

    row = preview(client, story)
    body = confirmation(row)
    body["mappings"][0]["action"] = "skip"
    assert (
        client.patch(
            f"/api/branches/{story['branch_id']}/import-previews/{row['id']}/draft",
            json={"base_revision": 0, "mappings": body["mappings"]},
        ).status_code
        == 200
    )
    assert confirm(client, story, row, body).status_code == 200
    snapshot = create_backup(settings, domain.db)
    target = tmp_path / "import-restore"
    target.mkdir()
    unpack_verified(settings.data_dir / "backups" / (snapshot["id"] + ".zip"), target)
    restored = Domain(Database(target / "manuscripts.sqlite"))
    raw = get_import(restored, story["branch_id"], row["id"])
    assert raw["raw"] == TEXT.encode("utf-8-sig")
    saved = public_preview(restored, raw)
    assert saved["draft"]["mappings"][0]["action"] == "skip"
    assert saved["receipt"]["results"][0]["action"] == "skip"


def test_late_write_failure_rolls_back_chapters_revisions_and_receipt(client, domain, story, monkeypatch):
    row = preview(client, story)
    original = domain.revise_tx
    from ainovel.domain import Domain

    calls = 0

    def injected(tx, chapter, content, cause):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("isolated injected write failure")
        return original(tx, chapter, content, cause)

    before = {t: domain.db.all("SELECT * FROM " + t) for t in ("chapters", "revisions", "branches")}
    monkeypatch.setattr(Domain, "revise_tx", staticmethod(injected))
    with pytest.raises(RuntimeError, match="injected write failure"):
        confirm(client, story, row, confirmation(row))
    assert before == {t: domain.db.all("SELECT * FROM " + t) for t in before}
    assert not domain.db.one("SELECT receipt FROM manuscript_imports WHERE id=?", (row["id"],))["receipt"]
