"""Isolated observer contracts; fake outputs do not establish semantic accuracy."""

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from ainovel.book_applicability import ACTION, KEY, VERSION, assess, enabled
from ainovel.db import DomainError, dumps
from ainovel.models import ModuleInput
from ainovel.worker import Worker

MOTHER = "若涉及母亲的去向，仍保持失踪原因未明。"
LETTER = "若涉及遗失书信，不将未核实的线索作为真相。"
TEXT = "周舟收起工具，在灯下登记桥板的裂纹。😀𠮷\n他约好明日再来核验。"
OLD_REVIEW = "OLD_REVIEW_SECRET_54c9"
CANDIDATE_JUDGMENT = "CANDIDATE_JUDGMENT_SECRET_aa75"
EXPECTED_ANSWER = "EXPECTED_TRIGGER_SECRET_ba48"


class ObserverModel:
    def __init__(
        self,
        trigger="not_triggered",
        obligation="not_required",
        *,
        defect=None,
        invalid_rounds=3,
        explanation="只读本次正文及原安排；本次工程替身声明前提未出现，整书仍待核对。",
        raw_quote=False,
    ):
        self.trigger, self.obligation = trigger, obligation
        self.defect, self.invalid_rounds = defect, invalid_rounds
        self.explanation, self.raw_quote = explanation, raw_quote
        self.frames, self.schemas = [], []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.frames.append(deepcopy(frame))
        self.schemas.append(deepcopy(kwargs["schema"]))
        instruction = frame["任务"]
        assert instruction["动作"] == ACTION
        source = instruction["可引用原文"][0]
        checks = [
            {
                "condition_id": row["condition_id"],
                "trigger": self.trigger,
                "chapter_obligation": self.obligation,
                "trigger_clause": row["condition"].split("，")[0],
                "evidence": (source["text"] if self.raw_quote else source["source_id"])
                if self.trigger == "triggered"
                else "",
                "explanation": self.explanation + (f"（隔离响应{len(self.frames)}）" if self.defect else ""),
            }
            for row in instruction["全书条件原话"]
        ]
        if len(self.frames) <= self.invalid_rounds and checks:
            if self.defect == "missing":
                checks.pop()
            elif self.defect == "duplicate":
                checks[-1]["condition_id"] = checks[0]["condition_id"]
            elif self.defect == "extra":
                checks.append({**checks[0], "condition_id": "FIX999"})
            elif self.defect == "reordered":
                checks.reverse()
            elif self.defect == "body_as_clause":
                checks[0]["trigger_clause"] = source["text"]
            elif self.defect == "other_condition_clause":
                checks[0]["trigger_clause"] = checks[1]["trigger_clause"]
            elif self.defect == "condition_as_body":
                checks[0]["evidence"] = instruction["全书条件原话"][0]["condition"]
            elif self.defect == "missing_quote":
                checks[0]["evidence"] = ""
            elif self.defect == "bad_source":
                checks[0]["evidence"] = "SOURCE9999"
            elif self.defect == "whitespace_quote":
                checks[0]["evidence"] = " "
            elif self.defect == "whitespace_explanation":
                checks[0]["explanation"] = " "
        return {
            "text": dumps({"checks": checks}),
            "usage": {"input": 1, "output": 1},
            "call_id": f"offline-book-observer-{len(self.frames)}",
        }


def claimed_worker(settings, domain, story, model):
    saved = domain.save(story["chapter_id"], 0, TEXT)
    queued = domain.queue(
        "module",
        ModuleInput(
            action="review.chapter",
            novel_id=story["id"],
            branch_id=story["branch_id"],
            chapter_id=story["chapter_id"],
            base_revision=saved["revision"],
            instruction="隔离适用性工程测试，不生成正文。",
            account="deepseek",
        ).model_dump(),
    )
    progress = {**queued["result"], KEY: VERSION}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(progress), queued["id"]))
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    return worker


def observe(worker, *, fixed=None, origins=None, eligible=None, number=2, instructions=None, text=TEXT):
    fixed = ["本章记录裂纹。", MOTHER] if fixed is None else fixed
    origins = (
        [{"condition": value, "scope": "book" if value in (MOTHER, LETTER) else "chapter"} for value in fixed]
        if origins is None
        else origins
    )
    eligible = [MOTHER] if eligible is None else eligible
    instructions = (
        {"本章": {"number": number, "plot": "登记桥板裂纹，决定明日再核验。", "fixed": ["本章记录裂纹。"]}}
        if instructions is None
        else instructions
    )
    return asyncio.run(assess(worker, text, fixed, origins, number, instructions, eligible))


def test_independent_absence_observation_is_bound_and_does_not_change_prose(settings, domain, story):
    model = ObserverModel()
    worker = claimed_worker(settings, domain, story, model)
    before = domain.chapter(story["chapter_id"])
    fixed = ["本章记录裂纹。", MOTHER]
    result = observe(worker, fixed=fixed)
    assert result["version"] == VERSION and result["chapter_number"] == 2
    assert result["content_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert result["conditions_sha256"] == hashlib.sha256(dumps(fixed).encode()).hexdigest()
    assert result["checks"] == [
        {
            "condition_id": "FIX002",
            "trigger": "not_triggered",
            "chapter_obligation": "not_required",
            "trigger_clause": "若涉及母亲的去向",
            "evidence": "",
            "explanation": model.explanation,
        }
    ]
    assert len(model.frames) == 1
    assert domain.chapter(story["chapter_id"]) == before
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.db.all("SELECT * FROM facts") == []
    assert model.frames[0]["任务"]["全书条件原话"] == [{"condition_id": "FIX002", "condition": MOTHER}]
    assert model.frames[0]["任务"]["全书条件适用观察版本"] == VERSION


@pytest.mark.parametrize("raw_quote", [False, True])
def test_triggered_observation_resolves_source_id_or_exact_body_quote(settings, domain, story, raw_quote):
    model = ObserverModel("triggered", "required", raw_quote=raw_quote)
    worker = claimed_worker(settings, domain, story, model)
    body = "母亲留下的旧物被放在箱中。😀𠮷\n周舟仍不知她去了哪里。"
    result = observe(worker, text=body)
    assert result["checks"][0]["evidence"] == "母亲留下的旧物被放在箱中。😀𠮷"
    assert result["checks"][0]["trigger"] == "triggered"
    assert len(model.frames) == 1
    props = model.schemas[0]["$defs"]["ApplicabilityCheck"]["properties"]
    assert props["condition_id"]["enum"] == ["FIX002"]
    assert props["evidence"]["enum"] == ["SOURCE0001", "SOURCE0002", ""]


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "duplicate",
        "extra",
        "reordered",
        "body_as_clause",
        "other_condition_clause",
        "condition_as_body",
        "missing_quote",
        "bad_source",
        "whitespace_quote",
        "whitespace_explanation",
    ],
)
def test_invalid_observation_stops_after_two_repairs_without_publishing(settings, domain, story, defect):
    model = ObserverModel("triggered", "required", defect=defect)
    worker = claimed_worker(settings, domain, story, model)
    fixed = [MOTHER, "本章记录裂纹。", LETTER]
    with pytest.raises(DomainError) as stopped:
        observe(worker, fixed=fixed, eligible=[MOTHER, LETTER])
    assert stopped.value.code == "evidence"
    assert "全书条件独立适用性观察" in stopped.value.message
    assert len(model.frames) == 3
    assert all(
        frame["任务"]["全书条件原话"]
        == [{"condition_id": "FIX001", "condition": MOTHER}, {"condition_id": "FIX003", "condition": LETTER}]
        for frame in model.frames
    )
    assert model.frames[1]["任务"]["前次产物"]["checks"]
    assert domain.chapter(story["chapter_id"])["content"] == TEXT
    assert domain.db.all("SELECT * FROM artifacts") == []


def test_one_reference_repair_preserves_frozen_conditions_and_returns_new_observation(
    settings, domain, story
):
    model = ObserverModel("triggered", "required", defect="missing_quote", invalid_rounds=1)
    worker = claimed_worker(settings, domain, story, model)
    result = observe(worker)
    assert len(model.frames) == 2 and result["checks"][0]["trigger"] == "triggered"
    assert model.frames[1]["任务"]["前次产物"]["checks"][0]["evidence"] == ""
    assert model.frames[0]["任务"]["全书条件原话"] == model.frames[1]["任务"]["全书条件原话"]


def test_unknown_is_retained_even_if_explanation_contains_a_different_proposal(settings, domain, story):
    model = ObserverModel(
        "uncertain", "uncertain", explanation="本章似乎未涉及，或许应not_triggered，仍无法确认指代。"
    )
    worker = claimed_worker(settings, domain, story, model)
    result = observe(worker)
    assert result["checks"][0]["trigger"] == result["checks"][0]["chapter_obligation"] == "uncertain"
    assert result["checks"][0]["evidence"] == "" and len(model.frames) == 1


def test_explicit_chapter_deadline_survives_absent_trigger(settings, domain, story):
    condition = "若涉及母亲的去向，保留线索；第26章必须说明她的去向。"
    model = ObserverModel("not_triggered", "required")
    worker = claimed_worker(settings, domain, story, model)
    result = observe(
        worker,
        fixed=[condition],
        origins=[{"condition": condition, "scope": "book"}],
        eligible=[condition],
        number=26,
        instructions={"本章": {"number": 26, "plot": "本章查清并说明母亲去向。", "fixed": []}},
    )
    check = result["checks"][0]
    assert check["trigger"] == "not_triggered" and check["chapter_obligation"] == "required"
    instruction = model.frames[0]["任务"]
    assert instruction["章节号"] == 26
    assert instruction["全书条件原话"][0]["condition"] == condition
    assert "即使正文完全漏写" in instruction["本章义务"]


def test_old_review_candidate_judgment_and_expected_answer_never_reach_observer(settings, domain, story):
    model = ObserverModel()
    worker = claimed_worker(settings, domain, story, model)
    instructions = {
        "本章": {
            "number": 2,
            "plot": "登记桥板裂纹。",
            "fixed": ["本章记录裂纹。"],
            "review": OLD_REVIEW,
        },
        "旧报告": OLD_REVIEW,
        "本次候选判断": CANDIDATE_JUDGMENT,
        "预期答案": EXPECTED_ANSWER,
    }
    observe(worker, instructions=instructions)
    frame = model.frames[0]
    serialized = dumps(frame)
    assert all(marker not in serialized for marker in (OLD_REVIEW, CANDIDATE_JUDGMENT, EXPECTED_ANSWER))
    assert frame["上下文"] == {
        "scope": "独立观察仅本次正文、指定全书原条件及本章原安排；不含旧审稿、候选判断或预期答案"
    }


def test_no_eligible_condition_does_not_call_model_even_on_legacy_worker(settings, domain, story):
    model = ObserverModel()
    worker = claimed_worker(settings, domain, story, model)
    worker.task["result"].pop(KEY)
    result = observe(worker, eligible=[])
    assert result["checks"] == [] and len(model.frames) == 0
    assert enabled(worker) is False


@pytest.mark.parametrize("marker", [None, "book-applicability/future"])
def test_eligible_conditions_need_explicit_supported_policy(settings, domain, story, marker):
    model = ObserverModel()
    worker = claimed_worker(settings, domain, story, model)
    worker.task["result"][KEY] = marker
    with pytest.raises(DomainError) as stopped:
        observe(worker)
    assert stopped.value.code == "book_applicability_version" and len(model.frames) == 0


@pytest.mark.parametrize(
    "scope,condition", [("chapter", MOTHER), ("prohibition", MOTHER), ("book", "本章必须取得信件。")]
)
def test_eligibility_cannot_expand_to_chapter_prohibition_or_unconditional_item(
    settings, domain, story, scope, condition
):
    model = ObserverModel()
    worker = claimed_worker(settings, domain, story, model)
    with pytest.raises(DomainError) as stopped:
        observe(
            worker,
            fixed=[condition],
            origins=[{"condition": condition, "scope": scope}],
            eligible=[condition],
        )
    assert stopped.value.code == "book_applicability_source" and len(model.frames) == 0
