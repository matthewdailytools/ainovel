"""Deterministic outputs for one original offline E2E source-correction fixture.

This module belongs to tests only. It does not load providers, call models, or
infer expected results for any source outside SOURCE.
"""

SOURCE = "林舟检查门锁并记录结果。【写作要求】先说明旧工具与刻线的对应，再写检查结果。"
BODY = (
    "【source-correction-e2e】"
    + (
        "林舟先把旧工具的尖端贴齐第一道刻线。第一道线标记锁舌关合的位置，第二道线只用来核对间隙，"
        "两道线不能互换。他合上门，转动锁舌，发现尖端仍与第一道线相齐，便把检查结果记在空白纸上。"
    )
    * 3
)
PROPOSE = "仅从原情节纠偏事项主体与指令性质"
VERIFY = "独立核对来源解释纠偏与全部事项保持"


def create_source_correction_fixture(domain, *, test_mode=False, account="openai_1"):
    """Create a stopped original toy only when an isolated test caller opts in.

    The E2E server must also enforce its own test-mode and temporary-data guards.
    There is no provider, worker, HTTP, adoption, or restoration here.
    """
    if test_mode is not True:
        raise ValueError("Only an explicitly isolated test-mode caller may seed this fixture")
    if not isinstance(account, str) or not account.strip():
        raise ValueError("An isolated fixture account must be explicitly configured by its caller")
    from ainovel.candidate_recheck import sha
    from ainovel.db import dumps
    from ainovel.models import WorkflowInput

    novel = domain.create_novel("后台验证 · 来源解释纠偏 · 林舟门锁", "原创隔离E2E，不是模型质量评价。")
    chapter = domain.create_chapter(novel["branch_id"], "门锁测试章")
    book_fixed = ["全书末章公开灯塔失修的责任归属。"]
    chapter_fixed = ["本章说明旧工具与刻线的对应。"]
    request = WorkflowInput(
        novel_id=novel["id"],
        mode="assisted",
        completion_scope="segment",
        brief="原创林舟门锁工程夹具；终局留待后章。",
        chapters=1,
        plots=[{"number": 1, "title": chapter["title"], "plot": SOURCE, "fixed": chapter_fixed}],
        fixed=book_fixed,
        min_chars=200,
        max_chars=600,
        adoption="automatic",
        account=account,
        plot_contract_check=True,
        budget_limit=5,
    ).model_dump()
    parent = domain.queue("workflow", request, novel["branch_id"])
    rows = []
    for index, (actor, action, obj, certainty, evidence) in enumerate(
        [
            ("林舟", "检查", "门锁并记录结果", "explicit", "林舟检查门锁并记录结果。"),
            (
                "未注明",
                "说明",
                "旧工具与刻线的对应",
                "ambiguous",
                "【写作要求】先说明旧工具与刻线的对应，再写检查结果。",
            ),
        ],
        1,
    ):
        rows.append(
            {
                "id": f"P{index:02}",
                "actor": actor,
                "action": action,
                "object": obj,
                "author_evidence": evidence,
                "direction": "",
                "result": "保留原检查和说明要求",
                "certainty": certainty,
                "transfer_object": "",
                "supplier": "",
                "recipient": "",
                "transfer_scope": "none",
                "recipient_scope": "unspecified",
                "scope_condition": "",
            }
        )
    text = BODY
    candidate = domain.stage(
        {
            "novel_id": novel["id"],
            "branch_id": novel["branch_id"],
            "chapter_id": chapter["id"],
            "base_revision": 0,
        },
        "chapter_draft",
        {
            "text": text,
            "fixed": book_fixed + chapter_fixed,
            "review_passed": False,
            "memory": {"facts": []},
            "review": {
                "checks": [
                    {"condition": condition, "result": "pass", "evidence": BODY}
                    for condition in book_fixed + chapter_fixed
                ],
                "findings": [],
                "summary": "隔离旧解释仍有主体和指令性质待核。",
            },
            "plot_audit": {
                "version": "author-plot-audit/2",
                "source_kind": "author_plot",
                "source": {"kind": "author_plot", "text_sha256": sha(SOURCE)},
                "items": rows,
                "interpretation_note": "原创夹具的旧解释，P02仍未知，不能当作作者新义务。",
                "checks": [
                    {
                        "item_id": row["id"],
                        "result": "pass",
                        "observed": "正文检查门锁并交代旧工具与刻线对应。",
                        "evidence": BODY,
                        "reason": "工程夹具已有正文观察，原解释性质仍需单独核对。",
                        "repair": "",
                    }
                    for row in rows
                ],
                "findings": [],
                "summary": "两项正文对照已有观察，P02原解释仍保留歧义。",
                "policy": "仅原创隔离夹具，不代表模型或文学质量通过。",
                "candidate_sha256": sha(BODY),
                "candidate_revision": 0,
                "status": "unverified",
            },
        },
        {"story_revision": domain.branch(novel["branch_id"])["story_revision"]},
        parent["id"],
    )
    result = {
        **parent["result"],
        "plan": {"chapters": request["plots"], "ending": "后章另核"},
        "next_chapter": 1,
        "waiting_artifact": candidate["id"],
        "latest_artifact": candidate["id"],
        "expected_story_revision": domain.branch(novel["branch_id"])["story_revision"],
    }
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET status='needs_review',result=? WHERE id=?", (dumps(result), parent["id"])
        )
    return {
        "novel_id": novel["id"],
        "branch_id": novel["branch_id"],
        "chapter_id": chapter["id"],
        "parent_id": parent["id"],
        "candidate_id": candidate["id"],
        "source": SOURCE,
        "selected_item_ids": ["P02"],
        "source_kind": "author_plot",
    }


def source_correction_output(instruction):
    """Return one toy response, or None for unrelated/malformed requests."""
    if not isinstance(instruction, dict):
        return None
    action = instruction.get("动作")
    if action not in {PROPOSE, VERIFY} or instruction.get("原情节") != SOURCE:
        return None
    rows = instruction.get("原解释事项")
    selected = instruction.get("允许纠偏事项")
    units = instruction.get("可引用原文")
    if (
        not isinstance(rows, list)
        or not rows
        or len(rows) > 12
        or not all(isinstance(row, dict) for row in rows)
        or [row.get("item_id") for row in rows] != [f"P{i:02}" for i in range(1, len(rows) + 1)]
        or not isinstance(selected, list)
        or not all(isinstance(item, str) for item in selected)
        or len(set(selected)) != len(selected)
        or not set(selected) <= {row["item_id"] for row in rows}
        or not isinstance(units, list)
        or not units
        or not all(
            isinstance(unit, dict)
            and isinstance(unit.get("source_id"), str)
            and isinstance(unit.get("text"), str)
            and bool(unit["text"])
            and unit["text"] in SOURCE
            for unit in units
        )
    ):
        return None
    proposal = []
    for row in rows:
        if not isinstance(row.get("actor"), str) or row.get("certainty") not in {"explicit", "ambiguous"}:
            return None
        narrative = row["item_id"] in selected and row.get("action") == "说明"
        quoted = row.get("author_evidence", "")
        unit = next((unit for unit in units if quoted and quoted in unit["text"]), units[0])
        proposal.append(
            {
                "item_id": row["item_id"],
                "actor": "" if narrative else row["actor"],
                "statement_kind": "narrative_requirement" if narrative else "story_event",
                "certainty": "explicit" if narrative else row["certainty"],
                "interpretation_reason": "隔离原创样例中说明属于标明的写作要求，其余事件保持原主体。",
                "evidence": unit["source_id"],
            }
        )
    if action == PROPOSE:
        return {"items": proposal}
    supplied = instruction.get("纠偏提案")
    if not isinstance(supplied, dict):
        return None
    proposed = supplied.get("items")
    if not isinstance(proposed, list) or len(proposed) != len(proposal):
        return None
    if any(
        not isinstance(actual, dict)
        or any(
            actual.get(key) != expected[key] for key in ("item_id", "actor", "statement_kind", "certainty")
        )
        for actual, expected in zip(proposed, proposal, strict=True)
    ):
        return None
    fixed = instruction.get("原固定条件仅核对是否保持")
    if not isinstance(fixed, list) or any(not isinstance(row, dict) for row in fixed):
        return None
    if [row.get("condition_id") for row in fixed] != [f"FIX{i:03}" for i in range(1, len(fixed) + 1)]:
        return None
    return {
        "item_checks": [
            {
                "item_id": row["item_id"],
                "result": "pass",
                "evidence": row["evidence"],
                "reason": "仅此原创夹具的全部P主体、指令性质及保留字段一致；不是模型质量评价。",
            }
            for row in proposal
        ],
        "fixed_checks": [
            {
                "condition_id": row["condition_id"],
                "result": "pass",
                "evidence": row["condition_id"],
                "reason": "仅核对原约定保持，不要求本章提前兑现未来结果。",
            }
            for row in fixed
        ],
        "summary": "隔离原创E2E确定性来源回执；不代表真实模型或文学质量通过。",
    }


def source_correction_body_output(instruction):
    """Only the exact marked toy can use deterministic prose-stage outputs."""
    if not isinstance(instruction, dict):
        return None
    action = instruction.get("动作")
    if action not in {"审查正文", "逐项对照作者情节解释与本次正文", "从正文提取事实候选与章节摘要"}:
        return None
    if instruction.get("正文", instruction.get("本次候选")) != BODY:
        return None
    units = instruction.get("可引用原文")
    if (
        not isinstance(units, list)
        or not units
        or not all(
            isinstance(row, dict)
            and isinstance(row.get("source_id"), str)
            and isinstance(row.get("text"), str)
            and row["text"]
            and row["text"] in BODY
            for row in units
        )
    ):
        return None
    evidence = units[0]["source_id"]
    if action == "审查正文":
        table = instruction.get("固定条件编号表")
        if not isinstance(table, list):
            return None
        checks = []
        for row in table:
            allowed = row["allowed_results"]
            verdict = (
                "pass" if "pass" in allowed else "not_triggered" if "not_triggered" in allowed else "unknown"
            )
            checks.append(
                {
                    "condition": row["condition_id"],
                    "chapter_result": verdict,
                    "evidence": evidence if verdict == "pass" else "",
                    "scope": row["scope"],
                    "applicability": "not_triggered" if verdict == "not_triggered" else "unconditional",
                    "chapter_obligation": "required" if row["scope"] == "chapter" else "not_required",
                    "explanation": "仅此原创夹具的本章保持；未到全书结果仍待整书核对。",
                }
            )
        return {"checks": checks, "findings": [], "summary": "原创隔离夹具本章守约，不表示全书或文学通过。"}
    if action == "逐项对照作者情节解释与本次正文":
        if instruction.get("作者原话") != SOURCE:
            return None
        items = instruction.get("独立整理的解释提案")
        if not isinstance(items, list) or [row.get("id") for row in items] != ["P01", "P02"]:
            return None
        return {
            "checks": [
                {
                    "item_id": row["id"],
                    "result": "pass",
                    "observed": "林舟检查门锁并记录结果，叙述交代旧工具与两道刻线的对应。",
                    "evidence": evidence,
                    "reason": "只用于原创夹具的逐P工程核对。",
                    "repair": "",
                }
                for row in items
            ],
            "summary": "原创隔离夹具逐P一致，不代表真实模型语义能力。",
        }
    return {
        "facts": [
            {
                "subject": "林舟",
                "kind": "event",
                "statement": "林舟检查门锁并把检查结果记在纸上。",
                "truth": "fact",
                "story_time": "未知",
                "evidence": evidence,
            }
        ],
        "summary": "林舟用旧工具与刻线核对门锁，记录结果。",
    }
