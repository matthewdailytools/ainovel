import asyncio

from ainovel.workflow_plan_preparation import prepare
from test_workflow_plan_preparation import claimed


def path(branch_id, chapter_id):
    return f"/api/branches/{branch_id}/chapters/{chapter_id}/writing-context"


def test_author_outline_is_chapter_bound_and_saved_optimistically(client, domain, story):
    branch, first = story["branch_id"], story["chapter_id"]
    second = domain.create_chapter(branch, "第二章")["id"]
    endpoint = path(branch, first)
    assert client.get(endpoint).json()["outline"] is None
    assert client.get(path(domain.create_novel("另一部书")["branch_id"], first)).status_code == 403
    assert client.put(f"{endpoint}/outline", json={"base_version": None, "text": "  "}).status_code == 422

    response = client.put(f"{endpoint}/outline", json={"base_version": None, "text": "使者先见到守门人。"})
    assert response.status_code == 200, response.text
    previous = response.json()
    assert previous["source"] == "author"
    assert client.get(path(branch, second)).json()["outline"] is None
    assert (
        client.put(f"{endpoint}/outline", json={"base_version": None, "text": "旧窗口。"}).status_code == 409
    )
    edited = client.put(
        f"{endpoint}/outline", json={"base_version": previous["version"], "text": "守门人先到码头。"}
    ).json()
    assert edited["version"] != previous["version"]
    assert client.get(endpoint).json()["outline"] == edited
    assert domain.artifact(previous["version"])["payload"]["text"] == previous["text"]
    assert domain.chapter(first)["revision"] == 0
    assert domain.chapter(first)["content"] == ""


def test_frozen_outline_binds_by_chapter_id_and_manual_takeover_preserves_receipt(
    client, domain, story, settings
):
    branch, first = story["branch_id"], story["chapter_id"]
    second = domain.create_chapter(branch, "第二章")["id"]
    worker, plot, chapter, task = claimed(settings, domain, story)
    assert asyncio.run(prepare(worker, plot, chapter)) is not None
    result = domain.task(task["id"])["result"]
    endpoint = path(branch, first)
    assert client.get(path(branch, second)).json()["outline"] is None
    frozen = client.get(endpoint).json()["outline"]
    assert (frozen["source"], frozen["text"], frozen["task_id"]) == (
        "frozen_ai",
        result["plot_preparations"]["1"]["effective_plot"],
        task["id"],
    )
    reorder = client.post(
        f"/api/chapters/{second}/move",
        json={"direction": "up", "base_story_revision": domain.branch(branch)["story_revision"]},
    )
    assert reorder.status_code == 200, reorder.text
    assert client.get(endpoint).json()["outline"] == frozen
    replacement = client.put(
        f"{endpoint}/outline", json={"base_version": frozen["version"], "text": "改用白灯作为暗号。"}
    ).json()
    assert replacement["source"] == "author"
    assert client.get(endpoint).json()["outline"] == replacement
    assert domain.task(task["id"])["result"] == result


def test_setting_links_to_exact_prior_revision_and_marks_historical_source(client, domain, story):
    branch, first = story["branch_id"], story["chapter_id"]
    second = domain.create_chapter(branch, "第二章")["id"]
    quote = "每次祭灯会折损十年寿元。"
    source = domain.save(first, 0, f"𠮷\n{quote}\n守门人默然。")
    response = client.post(
        "/api/facts",
        json={
            "chapter_id": first,
            "source_revision": source["revision"],
            "subject": "祭灯寿元代价",
            "kind": "world",
            "statement": quote,
            "evidence": quote,
        },
    )
    assert response.status_code == 201, response.text
    endpoint = path(branch, second)
    record = client.get(endpoint).json()["settings"][0]
    assert record["source_current"]
    loc = record["source"]
    assert loc["chapter_id"] == first and loc["quote"] == quote
    assert (loc["start"], loc["end"]) == (3, 3 + len(quote))
    jump = f"{endpoint}/settings/{record['id']}/source"
    params = {"revision": loc["revision"], "start": loc["start"]}
    excerpt = {"before": "𠮷\n", "quote": quote, "after": "\n守门人默然。"}
    assert client.get(jump, params=params).json() == excerpt
    assert client.get(jump, params={**params, "start": 0}).status_code == 409

    saved = domain.save(first, source["revision"], "每次祭灯会折损三年寿元。")
    old = client.get(endpoint).json()["settings"][0]
    assert not old["source_current"]
    assert old["source"] == loc
    assert saved["revision"] != old["source"]["revision"]
    assert client.get(jump, params=params).json() == excerpt
