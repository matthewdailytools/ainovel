"""New reference reports receive bindings without changing paid model prompts."""

import hashlib

from ainovel.plot_audit import VERSION
from test_plot_audit import AUTHOR, Integrated
from test_reference_flow import reference as reference, run, update


def test_new_reference_draft_and_edited_candidate_check_have_actual_bindings(
    client, settings, domain, reference
):
    analysis, _ = run(client, settings, domain, reference["id"], "analyze")
    cards = analysis["payload"]["cards"]
    row = update(
        client,
        domain,
        reference,
        cards=cards,
        selected_ids=[cards[0]["id"]],
        plot=AUTHOR,
        fixed=["交易失败"],
        application_plan="保留作者交换方向",
    )
    model = Integrated()
    draft, _ = run(client, settings, domain, row["id"], "draft", model=model, plot_contract_check=True)
    report = draft["payload"]["reference_comparison"]["plot_audit"]
    assert report["version"] == VERSION and report["candidate_revision"] == 0
    assert report["source"]["text_sha256"] == hashlib.sha256(AUTHOR.encode()).hexdigest()
    assert report["candidate_sha256"] == hashlib.sha256(draft["payload"]["text"].encode()).hexdigest()
    assert "target" not in report
    # Newly opted-in tasks explicitly execute the server-frozen current contract.
    assert len(model.audits) == 3
    assert all(frame["任务"].get("情节核对契约版本") == VERSION for frame in model.audits)
    original = domain.artifact(draft["id"])
    text = original["payload"]["text"] + "他们保留原记录。"
    response = client.patch("/api/artifacts/" + draft["id"], json={"candidate_revision": 0, "text": text})
    assert response.status_code == 200
    edited = domain.artifact(draft["id"])
    old_report = edited["payload"]["previous_reference_comparison"]["plot_audit"]
    assert old_report == report
    checked, _ = run(
        client,
        settings,
        domain,
        row["id"],
        "check",
        model=Integrated(),
        plot_contract_check=True,
        candidate_id=edited["id"],
    )
    bound = checked["payload"]["plot_audit"]
    assert bound["candidate_revision"] == edited["candidate_revision"] == 1
    assert bound["candidate_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert checked["payload"]["target"]["candidate"] == {"id": edited["id"], "revision": 1}
    assert domain.artifact(draft["id"])["payload"]["previous_reference_comparison"]["plot_audit"] == report


def test_saved_reference_check_binds_saved_body_revision_separately_from_report_revision(
    client, settings, domain, reference, story
):
    analysis, _ = run(client, settings, domain, reference["id"], "analyze")
    cards = analysis["payload"]["cards"]
    row = update(
        client,
        domain,
        reference,
        cards=cards,
        selected_ids=[cards[0]["id"]],
        plot=AUTHOR,
        application_plan="保持原方向",
    )
    body = "沈砚把铜环带给货商，交易没有完成。"
    chapter = domain.save(story["chapter_id"], 0, body)
    checked, _ = run(
        client,
        settings,
        domain,
        row["id"],
        "check",
        model=Integrated(),
        plot_contract_check=True,
        check_saved=True,
    )
    report = checked["payload"]["plot_audit"]
    assert report["candidate_revision"] == checked["candidate_revision"] == 0
    assert report["target"] == {
        "kind": "saved_chapter",
        "chapter_id": chapter["id"],
        "revision": 1,
        "content_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    assert domain.chapter(chapter["id"]) == chapter
