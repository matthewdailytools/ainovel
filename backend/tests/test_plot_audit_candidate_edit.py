from copy import deepcopy

import pytest

from ainovel.db import DomainError


def test_edit_archives_plot_audit_without_authorizing_automatic_adoption(client, domain, story):
    original = domain.save(story["chapter_id"], 0, "作者原稿😀𠮷")
    report = {
        "version": "author-plot-audit/1",
        "candidate_revision": 0,
        "status": "model_pass",
        "summary": "这是编辑前的工程样例报告",
        "items": [{"author_evidence": "只向师傅提出怀疑"}],
    }
    candidate = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": original["revision"],
        },
        "chapter_draft",
        {
            "text": "未采用候选",
            "review_passed": True,
            "plot_audit": deepcopy(report),
            "review": {"summary": "原审稿", "plot_audit": deepcopy(report)},
            "memory": {"summary": "旧摘要"},
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    path = "/api/artifacts/" + candidate["id"]
    for revision, text in enumerate(["作者编辑候选😀", "第二次编辑𠮷"], 1):
        response = client.patch(path, json={"text": text, "candidate_revision": revision - 1})
        assert response.status_code == 200
        edited = response.json()
        assert edited["candidate_revision"] == revision
        assert "plot_audit" not in edited["payload"] and "review" not in edited["payload"]
        assert edited["payload"]["previous_plot_audit"] == report
        assert edited["payload"]["previous_review"]["plot_audit"] == report
        assert not edited["payload"]["review_passed"] and edited["payload"]["memory"] == {}
        with pytest.raises(DomainError) as error:
            domain.accept(
                candidate["id"],
                "automatic-edited-plot-" + str(revision),
                require_review=True,
                candidate_revision=revision,
            )
        assert error.value.code == "review_required"
        assert domain.chapter(story["chapter_id"]) == original
