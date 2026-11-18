"""One original three-chapter browser exercise; never a production model adapter."""

import hashlib
import json
from copy import deepcopy

from ainovel.db import DomainError
from e2e_authored_scope_fixture import bind_interpretation
from e2e_flow_policy_fixture import guard

TITLE = "潮尺与封账"
EDIT_MARKER = "把蓝绳系回旧尺"
EDIT_ORIGINAL = "他把旧尺平放在柜边，约定明早归还。"
EDIT_REPLACEMENT = "他把蓝绳系回旧尺，再把尺平放在柜边，约定明早归还。"
STRUCTURE_NOTE = "保留原账页和首次差值，两次记录分别可查。"
BOOK_FIXED = "原账须保留，可附更正说明。"
PREFIX = "/test/three-chapter"
BODY = {
    1: (
        "天刚亮，沈砚来到修船铺，向老韩借那把刻着缺口的旧尺。老韩先让他在借物纸上写下名字，"
        "又说明第三天傍晚收铺前必须归还。沈砚没有把借尺当成修闸的许可，只带上纸笔，沿着公开的小路去了闸口。\n\n"
        "第一处刻度旁积着薄泥。他擦净能看见的标记，把尺底抵在石沿，在纸上记下读数。到了第二处，他照样落尺，"
        "却发现两处数字差了两格。他没有立即断言谁改动过闸门，先在每行数字后记下落尺的位置和观察时刻。\n\n"
        "一阵浪拍来，水面又抬高了一点。沈砚把刚写好的数字圈住，注明这只是先后试量，尚不能当作同一时刻的比较。"
        "他将纸页折进小本，决定明天请老韩在另一处同时读尺，再把差异核实一遍。离开前，他回头认清两块石沿的位置，"
        "没有挪动闸板，也没有碰管理房里锁着的原账。"
    ),
    2: (
        "第二天清晨，沈砚带着昨天借来的旧尺回到闸口，老韩另持一把店里的尺，站在另一处石沿。两人先对齐尺上的零点，"
        "再约好听见同一声短哨才读数，免得把前后变化的水位误当成刻度差异。\n\n"
        "三次短哨过后，两人的记录仍差两格。沈砚没有添上原因，只把三组数字、时刻和落尺位置并排抄在复测纸上。"
        "守闸人看过记录，准许他们在公告板上张贴这些现场读数，并明确管理房的原账仍须留在柜中，不能混进公开的抄页。\n\n"
        "沈砚逐项核对张贴范围，把昨天的试量另标为先后测量，又将今天的同时复测单独列出。纸上没有母亲的姓名，"
        "也没有他尚未查明的责任判断。风掀起纸角，他用木夹固定，等围观的人看清日期，才收好自己的复测底稿。\n\n"
        "回到铺里，他擦干尺上的水迹。他把旧尺平放在柜边，约定明早归还。老韩点头，说封账时会带他去找守闸人，"
        "把借尺和公开抄页的事分别登记清楚，不能因为数字已经贴出就说全部疑点有了答案。"
    ),
    3: (
        "第三天早晨，沈砚取下柜边的旧尺，先看了一眼昨晚系回去的蓝绳。绳结贴着那道缺口，让他确认拿走的仍是老韩借出的那把。"
        "他将尺交到老韩手里，等老韩检查刻痕和尺边，才在借物纸的归还栏写下日期。借用至此结清，尺没有继续留在他身上。\n\n"
        "两人随后去了管理房。守闸人打开柜门，把原账摊在桌上，允许沈砚核对日期，却没有准许他拿走。沈砚拿自己的复测底稿，"
        "只对照公开读数对应的时间，将先后试量与同时复测分开说明；两格差异的原因仍记作待查，没有补写责任人的姓名。\n\n"
        "核对结束，守闸人把原账放回柜内，贴上封条，在登记页记下封存日期。沈砚看着封条两端压实，才收起属于自己的复测底稿。"
        "公告板上保留的只是已经获准公开的数字与测量方法，原账没有因此变成公开抄件，更没有转交到他手中。\n\n"
        "出门时，老韩甩了甩重新拿回的旧尺。沈砚把借物纸折好，与底稿分开保存。三天里能够确认的事已经落在纸上，"
        "未能解释的差异也留着清楚的位置；以后再查，便不必靠谁记得更响亮来争辩。"
    ),
}


def digest(value):
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class ThreeChapterModel:
    def __init__(self, app, fallback):
        self.app, self.fallback = app, fallback
        self.calls = []

    def owner(self, task):
        domain = self.app.state.domain
        if task["kind"] == "candidate_recheck":
            source = domain.artifact(task["payload"]["source_candidate_id"])
            task = domain.task(source["task_id"])
        payload = task["payload"]
        if (
            task["kind"] == "workflow"
            and payload.get("mode") == "assisted"
            and payload.get("chapters") == 3
            and TITLE in payload.get("brief", "")
        ):
            return task
        return None

    def response(self, instruction, context):
        action = instruction.get("动作")
        number = instruction.get("本章", {}).get("number", instruction.get("章节号"))
        if action == "整书完结检查":
            units = instruction["可引用原文"]
            milestones = [
                next(
                    (unit for unit in units if unit["chapter"] == chapter and quote in unit["text"]),
                    None,
                )
                for chapter, quote in (
                    (1, "两处数字差了两格"),
                    (2, "三次短哨过后，两人的记录仍差两格"),
                    (3, "借物纸的归还栏"),
                    (3, "把原账放回柜内，贴上封条"),
                )
            ]
            promises = []
            for condition in instruction["必须逐项核对的作者硬约定"]:
                unit = (
                    milestones[-1]
                    if condition == BOOK_FIXED
                    else next((unit for unit in units if condition in unit["text"]), None)
                )
                promises.append(
                    {
                        "condition": condition,
                        "result": "pass" if unit else "unknown",
                        "evidence": unit["source_id"] if unit else "",
                    }
                )
            return {
                "closed": all(milestones) and all(row["result"] == "pass" for row in promises),
                "promises": promises,
                "findings": [],
                "summary": "固定三章工程样例仅核对试量、同时复测、实际归还和原账封存；差异原因仍待查。"
                "逐项保留本次冻结硬约定和当前版本原文，不代表文学质量或真实模型验收。",
            }
        if action == "按保存稿反向整理长篇结构候选":
            units = instruction["可引用原文"]
            number = self.app.state.domain.chapter(units[0]["chapter_id"])["position"]
            sources = [
                {
                    **{key: unit[key] for key in ("chapter_id", "revision", "start", "end")},
                    "quote": unit["source_id"],
                }
                for unit in units
            ]
            record = (
                {"kind": "character", "title": "沈砚", "sources": sources}
                if number == 1
                else {
                    "kind": "event",
                    "title": "同时复测并确认公开读数范围" if number == 2 else "归还旧尺并封存原账",
                    "status": "actual",
                    "story_time": "第二天清晨" if number == 2 else "第三天早晨",
                    "sources": sources,
                }
            )
            value = {
                "records": [
                    {
                        "local_id": f"three-chapter-{number}",
                        "record": record,
                        "reasoning": "只整理本章实际出现的人物或事件；完整引用范围保留，原因和责任仍待查。",
                    }
                ],
                "unknowns": ["两格差异的原因尚未查明，不把封账当成调查已经结束。"],
                "coverage": "当前章全部提供的原文单元，不含其他作品或通用故事提案。",
            }
            if "场景范围要求" in instruction:
                scene = {
                    "title": "本章完整范围",
                    "source_ids": [unit["source_id"] for unit in units],
                    "story_time": "未知",
                    "audience": "author",
                    "character_id": None,
                    "note": "仅验证本章原文范围；未把章节号推定为精确故事时点。",
                }
                if instruction.get("场景证据契约版本") == "scene-event-refs/2":
                    scene["order_basis"] = {
                        "kind": "unknown",
                        "reason": "本样例未独立绑定精确故事时点，仅采用完整原文范围。",
                        "refs": [],
                    }
                else:
                    scene["story_order"] = None
                value["scenes"] = [scene]
            return value
        if action == "按章纲写完整正文":
            if number not in BODY:
                raise DomainError(
                    "three_chapter_fixture", "三章工程样例没有本章正文，不借通用替身补造。", 409
                )
            if number == 2 and not self.structure_notes(context):
                raise DomainError(
                    "three_chapter_structure_context",
                    "第二章实际结构上下文未带入第一章已采纳的作者说明，工程验证停止。",
                    409,
                )
            if number == 3 and not any(
                chapter.get("position") == 2 and EDIT_MARKER in chapter.get("content", "")
                for chapter in context.get("recent", [])
            ):
                raise DomainError(
                    "three_chapter_context",
                    "第三章实际上下文未带入第二章已采纳的蓝绳编辑标记，工程验证停止。",
                    409,
                )
            return BODY[number]
        if action == "仅依据作者原话整理本场景情节":
            return bind_interpretation(
                instruction,
                {
                    "items": [
                        {
                            "author_evidence": unit["source_id"],
                            "actor": "沈砚",
                            "action": "按本章原话推进",
                            "object": unit["text"],
                            "direction": "",
                            "result": unit["text"],
                            "certainty": "explicit",
                            "supplier": "",
                            "recipient": "",
                            "transfer_object": "",
                            "transfer_scope": "none",
                            "recipient_scope": "unspecified",
                            "scope_condition": "",
                        }
                        for unit in instruction["可引用原文"]
                    ],
                    "note": "固定三章工程样例逐句保留；不是通用语义或真实模型质量结论。",
                },
            )
        if action == "审查正文":
            text = instruction["正文"]
            checks = []
            table = instruction.get("固定条件编号表")
            conditions = table or [{"condition": value} for value in instruction["固定条件"]]
            for row in conditions:
                condition = row["condition"]
                evidence = condition if condition in text else ""
                if condition == BOOK_FIXED:
                    evidence = next(
                        (
                            quote
                            for quote in (
                                "没有挪动闸板，也没有碰管理房里锁着的原账。",
                                "原账仍须留在柜中，不能混进公开的抄页。",
                                "守闸人把原账放回柜内，贴上封条，在登记页记下封存日期。",
                            )
                            if quote in text
                        ),
                        "",
                    )
                verdict = "pass" if evidence else "unknown"
                check = {
                    "condition": row.get("condition_id", condition),
                    "chapter_result" if table is not None else "result": verdict,
                    "evidence": evidence,
                }
                if table is not None:
                    check.update(
                        scope=row["scope"],
                        applicability="unconditional",
                        chapter_obligation="required",
                        explanation="固定工程样例逐章核对原账仍被保留；许可附更正说明不表示每章必须新增说明。"
                        if evidence
                        else "本工程替身没有本项逐字依据，保持未知。",
                    )
                checks.append(check)
            findings = []
            if number == 2 and EDIT_MARKER not in text:
                findings.append(
                    {
                        "severity": "suggestion",
                        "chapter": 2,
                        "quote": EDIT_ORIGINAL,
                        "reason": "归还约定已清楚；可以补一个收尺动作，给第三章辨认同一把尺留下可见依据。",
                        "repair": EDIT_REPLACEMENT,
                    }
                )
            return {
                "checks": checks,
                "findings": findings,
                "summary": "三章固定工程样例：区分试量与复测、公开抄页与原账、借用与归还。建议不构成必须修改的情节条件；文学质量未验证。",
            }
        if action == "从正文提取事实候选与章节摘要":
            text = instruction["正文"]
            summary = (
                "旧尺已经归还老韩，原账封存在管理房；两格差异原因仍待查。"
                if "归还栏" in text
                else "同时复测仍差两格；仅获准公开现场读数和测量方法，原账未交付。"
                if "三次短哨" in text
                else "借尺试量发现两格差异；先后测量尚不能确认为同一时刻差异。"
            )
            return {
                "facts": [
                    {
                        "subject": "沈砚",
                        "kind": "event",
                        "statement": "旧尺已经归还老韩。"
                        if "归还栏" in text
                        else "同时复测仍差两格，现场读数获准公开，原账仍须留在柜中。"
                        if "三次短哨" in text
                        else "先后试量的两处数字差两格，尚未确定原因。",
                        "evidence": next(
                            unit["source_id"]
                            for unit in instruction["可引用原文"]
                            if (
                                "归还栏"
                                if "归还栏" in text
                                else "三次短哨"
                                if "三次短哨" in text
                                else "差了两格"
                            )
                            in unit["text"]
                        ),
                    }
                ],
                "summary": summary,
            }
        return None

    @staticmethod
    def structure_notes(context):
        return [
            {
                "record_id": row["id"],
                "revision": row["revision"],
                "notes_sha256": digest(row["payload"]["notes"]),
                "sources": row["payload"]["sources"],
            }
            for row in context.get("longform", {}).get("characters", [])
            if STRUCTURE_NOTE in row["payload"].get("notes", "")
        ]

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        task = self.app.state.domain.task(task_id)
        owner = self.owner(task)
        if owner is None:
            return await self.fallback.generate(account, task_id, system, prompt, max_output, **kwargs)
        # Body replies above use this fixture; retain the existing book router
        # for source-grounded structure maintenance on the same workflow.
        self.fallback.book_tasks.add(task_id)
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        while "原始任务" in instruction:
            instruction = instruction["原始任务"]
        context = frame["上下文"]
        events = self.app.state.db.all(
            "SELECT id,payload FROM task_events WHERE task_id=? AND kind='step_dispatched' ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if not events:
            raise AssertionError("三章替身必须由真实Worker.call先持久化派发事件。")
        dispatch = json.loads(events[0]["payload"])
        call_id = f"offline-three-chapter-{task_id}-{len(self.calls) + 1}"
        entry = {
            "workflow_id": owner["id"],
            "task_id": task_id,
            "attempt": task["attempt"],
            "call_id": call_id,
            "dispatch_event_id": events[0]["id"],
            "fingerprint": dispatch["fingerprint"],
            "action": instruction.get("动作", instruction.get("action")),
            "chapter": instruction.get("本章", {}).get("number", instruction.get("章节号")),
            "context_sha256": digest(context),
            "author_structure_note_in_context": bool(self.structure_notes(context)),
            "author_structure_note_sources": self.structure_notes(context),
            "author_body_edit_in_context": any(
                chapter.get("position") == 2 and EDIT_MARKER in chapter.get("content", "")
                for chapter in context.get("recent", [])
            ),
            "chapter2_context": [
                {
                    "id": chapter["id"],
                    "revision": chapter["revision"],
                    "content_sha256": digest(chapter["content"]),
                    "author_edit_marker": EDIT_MARKER in chapter["content"],
                }
                for chapter in context.get("recent", [])
                if chapter.get("position") == 2
            ],
            "status": "started",
        }
        self.calls.append(entry)
        try:
            value = self.response(instruction, context)
            result = (
                await self.fallback.generate(account, task_id, system, prompt, max_output, **kwargs)
                if value is None
                else {
                    "text": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                    "usage": {"input": 1, "output": 1},
                }
            )
        except Exception as error:
            entry.update(status="failed", error_code=getattr(error, "code", type(error).__name__))
            raise
        result = {**result, "call_id": call_id, "model": "offline-three-chapter-fixture"}
        entry.update(status="completed", result_sha256=digest(result["text"]))
        return result

    def proof(self, task_id):
        domain, db = self.app.state.domain, self.app.state.db
        task = domain.task(task_id)
        if self.owner(task) != task:
            raise DomainError("three_chapter_fixture", "只读取当前三章工程工作流的证据。", 404)
        calls = [entry for entry in self.calls if entry["workflow_id"] == task_id]
        ids = sorted({task_id, *(entry["task_id"] for entry in calls)})
        placeholders = ",".join("?" for _ in ids)
        steps = db.all(
            f"SELECT task_id,fingerprint,result,created FROM task_steps WHERE task_id IN({placeholders}) ORDER BY created,task_id,fingerprint",
            ids,
        )
        events = db.all(
            f"SELECT id,task_id,kind,payload FROM task_events WHERE task_id IN({placeholders}) ORDER BY id",
            ids,
        )
        with self.app.state.budget.db() as usage:
            ledger = [
                dict(row)
                for row in usage.execute(
                    f"SELECT id,task_id,status,reserved,charged FROM calls WHERE task_id IN({placeholders}) ORDER BY id",
                    ids,
                )
            ]
        return {
            "scope": "固定原创三章工程替身；零实付请求，不表示文学质量或真实模型能力。",
            "mock_calls": deepcopy(calls),
            "provider_ledger_rows": ledger,
            "usage_kind": "in_process_mock_usage_not_provider_billing",
            "completed_steps": [{**row, "result": json.loads(row["result"])} for row in steps],
            "events": [{**row, "payload": json.loads(row["payload"])} for row in events],
            "chapters": db.all(
                "SELECT id,position,revision,title,content FROM chapters WHERE branch_id=? ORDER BY position",
                (task["branch_id"],),
            ),
            "adoption_receipts": [
                {**row, "result": json.loads(row["result"])}
                for row in db.all(
                    "SELECT r.* FROM receipts r JOIN artifacts a ON a.id=r.artifact_id WHERE a.branch_id=? ORDER BY r.key",
                    (task["branch_id"],),
                )
            ],
            "structure_history": task["result"].get("structure_history", []),
            "task": task,
        }


def register_three_chapter_fixture(app, settings, temporary_directory):
    guard(settings, temporary_directory)
    if settings.port in {8765, 8766}:
        raise RuntimeError("三章浏览器工程夹具不使用日常端口。")

    @app.get(PREFIX + "/{task_id}/proof")
    def proof(task_id: str):
        return app.state.three_chapter_model.proof(task_id)


def three_chapter_model(app, fallback):
    model = ThreeChapterModel(app, fallback)
    app.state.three_chapter_model = model
    return model
