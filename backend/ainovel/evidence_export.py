"""Export a frozen acceptance bundle without dispatching any model calls."""

import argparse
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from time import time

from .backups import create_backup, unpack_verified
from .db import Database, DomainError, dumps, uid
from .domain import Domain, char_count
from .runtime import data_directory_gate
from .settings import Settings


def _seven_stage_evidence(domain, entry, folder):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from . import book_completion, setting_use_desktop, settings_sync, workflow_inspection
    from .conception import CONFIRMATION
    from .manuscript_exchange import native_revision, source_revision
    from .project_brief import KIND as PROJECT
    from .workflow_archive import read_workflow_entry, validate_workflow_object

    task = entry["task"]
    branch = task["branch_id"]
    state = task["result"].get("seven_stage_run")
    if not state or not state.get("stage"):
        raise DomainError("acceptance_evidence", "缺少七台接力状态，无法导出确切验收依据。", 409)
    artifacts = {row["id"]: row for row in entry["artifacts"]}
    related = {row["id"]: json.loads(row["payload"]) for row in entry["related_tasks"]}
    parent_id = task["id"]
    recorded_children = state["children"]
    # The child queue and the parent's checkpoint commit separately. Recover the
    # exact bound request even if the parent stopped before recording the child.
    unrecorded_children = [
        identifier
        for identifier, payload in related.items()
        if payload.get("seven_stage_run_id") == parent_id and identifier not in recorded_children
    ]
    child_ids = [*recorded_children, *unrecorded_children]
    active_chapters = {row["id"]: row["revision"] for row in entry["chapters"] if not row["archived"]}

    def dependencies(tx, source):
        result = {key: source.get(key) for key in ("project_id", "setting_id", "outline_id")}
        for key, kind in (("project_id", PROJECT), ("setting_id", CONFIRMATION)):
            if identifier := result[key]:
                row = artifacts.get(identifier)
                if not row or (row["kind"], row["status"]) != (kind, "accepted"):
                    raise DomainError("acceptance_evidence", f"缺少已确认的 {kind} 修订 {identifier}。", 409)
        if identifier := result["outline_id"]:
            row = tx.execute(
                "SELECT * FROM outline_revisions WHERE branch_id=? AND id=? AND confirmed=1",
                (branch, identifier),
            ).fetchone()
            if not row:
                raise DomainError("acceptance_evidence", f"缺少已确认的大纲修订 {identifier}。", 409)
            result["outline"] = {
                "id": identifier,
                "revision": row["revision"],
                "plan": json.loads(row["payload"]),
                "setting_basis": json.loads(row["setting_basis"]),
            }
        return result

    completed = []
    inspections = {}
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        for identifier in (parent_id, *child_ids):
            payload = related.get(identifier, {})
            request_id = payload.get("client_request_id") or ""
            bound = tx.execute("SELECT task_id FROM task_requests WHERE key=?", (request_id,)).fetchone()
            if (
                not bound
                or bound["task_id"] != identifier
                or (
                    identifier != parent_id
                    and (
                        payload.get("seven_stage_run_id") != parent_id
                        or not request_id.startswith(f"seven-stage-{parent_id}-")
                    )
                )
            ):
                raise DomainError("acceptance_evidence", f"任务 {identifier} 的原批次请求绑定缺失。", 409)
        run_dependencies = dependencies(tx, state)
        for item in state["completed_chapters"]:
            chapter_id, revision = item["id"], item["revision"]
            row = tx.execute(
                "SELECT r.id FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
                "WHERE c.branch_id=? AND r.chapter_id=? AND r.revision=?",
                (branch, chapter_id, revision),
            ).fetchone()
            if not row:
                raise DomainError("acceptance_evidence", f"缺少接力章稿 {chapter_id} 修订 {revision}。", 409)
            proof = native_revision(tx, branch, row["id"]).model_dump()
            receipt = None
            if identifier := item.get("artifact_id"):
                artifact = artifacts.get(identifier)
                if (
                    not artifact
                    or artifact["chapter_id"] != chapter_id
                    or artifact["status"] != "accepted"
                    or artifact["task_id"] not in child_ids
                ):
                    raise DomainError("acceptance_evidence", f"缺少原批次采纳的章稿产物 {identifier}。", 409)
                receipt = next(
                    (
                        record
                        for raw in tx.execute(
                            "SELECT result FROM receipts WHERE artifact_id=?", (identifier,)
                        )
                        if (record := json.loads(raw["result"])).get("revision") == revision
                    ),
                    None,
                )
                if receipt is None:
                    raise DomainError("acceptance_evidence", f"缺少章稿产物 {identifier} 的原采纳回执。", 409)
            completed.append(
                {
                    **proof,
                    "current": revision == active_chapters.get(chapter_id),
                    "artifact_id": item.get("artifact_id"),
                    "adoption_receipt": receipt,
                }
            )

        def inspection(identifier):
            if identifier not in inspections:
                record = read_workflow_entry(tx, branch, workflow_inspection.KIND, identifier)
                if not record or record.report.scope != "book":
                    raise DomainError(
                        "acceptance_evidence", f"缺少接力所引用的全书体检单 {identifier}。", 409
                    )
                inspections[identifier] = validate_workflow_object(
                    record,
                    branch,
                    lambda key: source_revision(tx, branch, key),
                    None,
                    setting_use=lambda key: setting_use_desktop.read_version(tx, branch, key),
                    settings=lambda key: settings_sync.read_settings_version(tx, branch, key),
                )
            return inspections[identifier]

        for identifier in (state.get("inspection_id"), state.get("selected_inspection_id")):
            if identifier:
                inspection(identifier)
        delivery = None
        delivery_dependencies = None
        if identifier := state.get("completion_id"):
            completion = read_workflow_entry(tx, branch, book_completion.KIND, identifier)
            if not completion:
                raise DomainError("acceptance_evidence", f"缺少接力所引用的完本记录 {identifier}。", 409)
            delivery = validate_workflow_object(
                completion, branch, lambda key: source_revision(tx, branch, key), inspection
            )
            delivery_dependencies = dependencies(tx, delivery.manifest.model_dump())
        elif state["stage"] == "done" or task["status"] == "succeeded":
            raise DomainError("acceptance_evidence", "接力已标记结束，但缺少固定的完本记录。", 409)

    # Mount only the official local M6/M7 routes on the verified, isolated database.
    # Report freshness and ZIP generation use the same code as the workbench.
    app = FastAPI()
    workflow_inspection.register_routes(app, domain)
    book_completion.register_routes(app, domain)
    base = f"/api/branches/{branch}"
    reports = []
    with TestClient(app) as client:
        if inspections:
            route = f"{base}/workflow-inspections"
            latest = client.get(route).raise_for_status().json()["latest"]
            for identifier, record in inspections.items():
                view = client.get(f"{route}/{identifier}").raise_for_status().json()
                reports.append(
                    {
                        **record.model_dump(),
                        "current": bool(view["current"] and latest and latest["id"] == identifier),
                        "basis_current": view.get("basis_reconciles", view["current"]),
                        "origin": view.get("origin", "local"),
                        "effective_statuses": {
                            item["check_id"]: item["current_status"] for item in view["items"]
                        },
                    }
                )
        archive = None
        if delivery:
            route = f"{base}/book-completion"
            latest = client.get(route).raise_for_status().json()["latest"]
            view = client.get(f"{route}/history/{delivery.id}").raise_for_status().json()
            response = client.get(f"{route}/{delivery.id}/export").raise_for_status()
            filename = task["id"] + "-交付包.zip"
            (folder / filename).write_bytes(response.content)
            archive = {
                **delivery.model_dump(exclude={"materials"}),
                "current": bool(view["current"] and latest and latest["id"] == delivery.id),
                "basis_current": view.get("basis_reconciles", view["current"]),
                "origin": view["origin"],
                "zip": {"file": filename, "sha256": hashlib.sha256(response.content).hexdigest()},
            }
    return {
        "stage": state["stage"],
        "child_task_ids": child_ids,
        "unrecorded_child_task_ids": unrecorded_children,
        "dependencies": run_dependencies,
        "delivery_dependencies": delivery_dependencies,
        "completed_chapters": completed,
        "inspection_id": state.get("inspection_id"),
        "inspections": reports,
        "delivery": archive,
    }


def _seven_stage_report(entry):
    task = entry["task"]
    run = entry["seven_stage"]
    lines = [
        f"# {entry['branch']['name']} · 七台接力验收",
        "",
        f"批次 `{task['id']}` · {task['status']} · 阶段 {run['stage']}",
        f"本批次记录 {len(run['completed_chapters'])} 个已保存章稿修订。",
        "作者文学质量与实际收益待单独验收。",
        "",
        "## M6 全书体检",
        "",
    ]
    for report in run["inspections"]:
        status = "当前采用" if report["current"] else "历史依据"
        items = "、".join(f"{item['check_id']} {item['status']}" for item in report["report"]["items"])
        lines.extend([f"- `{report['id']}` · {status} · 原判定 {items}", ""])
    if not run["inspections"]:
        lines.extend(["接力尚未记录全书体检。", ""])
    lines.extend(["## M7 完本交付", ""])
    if delivery := run["delivery"]:
        status = "当前采用" if delivery["current"] else "历史依据"
        lines.extend(
            [
                f"完本记录 `{delivery['id']}` · {status} · 原六项全绿。",
                f"对应全书体检 `{delivery['manifest']['inspection_id']}`。",
                f"[冻结交付包]({delivery['zip']['file']}) · SHA-256 `{delivery['zip']['sha256']}`",
            ]
        )
    else:
        lines.append("接力尚未记录已确认的完本交付。")
    billing = entry["billing_evidence"]
    if billing["state"] == "captured":
        lines.extend(
            [
                "",
                f"费用依据 · {billing['row_count']} 条调用记录 · "
                f"{json.dumps(entry['cost_by_currency'], ensure_ascii=False)}",
            ]
        )
    else:
        lines.extend(["", "所选账本快照中没有本批次可核验的费用行。"])
    lines.extend(["", "准确章稿、原回执、版本标识、任务和费用见 evidence.json。", ""])
    return "\n".join(lines)


def revision_report(entry):
    if "seven_stage" in entry:
        return _seven_stage_report(entry)
    task, completion = entry["task"], entry["completion"]
    result = task["result"]
    current = {c["id"]: c["revision"] for c in entry["chapters"] if not c["archived"]}
    lines = [
        f"# {entry['branch']['name']} · 修订与验收报告",
        "",
        f"任务 `{task['id']}`；执行状态：{task['status']}；故事修订 {entry['branch']['story_revision']}。",
        "",
        f"已保存 {completion['chapter_count']}/{completion['expected']} 章，{completion['chars']} 非空白字符。",
        f"模型辅助完结：{completion['production_complete']}；三层正式验收：{completion['accepted']}。",
        "作者文学质量与实际收益尚未验证。模型意见须结合所引用版本和全文判断。",
        "",
        f"待复检或空稿章节：{completion['missing_or_stale']}；篇幅未通过章节：{completion['length_failures']}。",
        f"当前全书修订轮次：{result.get('repair_round', 0)}；下一检查点：{result.get('next_chapter', '未建立')}。",
        "",
        f"配置估算费用（分别计币种）：{json.dumps(entry.get('cost_by_currency', {}), ensure_ascii=False)}。未知调用保留预留。",
        "",
        "## 控制任务与账户",
        "",
    ]
    waiting = next((a for a in entry["artifacts"] if a["id"] == result.get("waiting_artifact")), None)
    if waiting:
        review = json.loads(waiting["payload"]).get("review", {})
        lines[2:2] = [
            f"当前需审阅：[未采纳候选]({task['id']}-待审候选.md)。",
            review.get("summary", "尚无审查结果。"),
            "",
        ]
    for related in entry["related_tasks"]:
        if related["kind"] == "workflow":
            payload = json.loads(related["payload"])
            state = json.loads(related["result"])
            lines.append(
                f"- `{related['id']}`：{related['status']}；账户 {payload['account']}；"
                f"承接任务 {state.get('continued_from', '无')}；"
                f"作者输入章级情节 {len(payload.get('plots', []))} 条。"
            )
    reports = [("整书汇总", result.get("completion"))]
    reports += [(f"全文分段 {i}", r) for i, r in enumerate(result.get("volume_reports", []), 1)]
    reports += [(f"分层汇总 {i}", r) for i, r in enumerate(result.get("aggregate_reports", []), 1)]
    for title, report in reports:
        if not report:
            continue
        sources = report.get("source_versions", [])
        valid = sources and all(current.get(s["id"]) == s["revision"] for s in sources)
        source_label = "对应当前正文版本" if valid else "历史来源或未提供冻结版本，不作为当前稿通过依据"
        lines.extend(["", f"## {title}", "", source_label, "", report.get("coverage", ""), ""])
        lines.extend([f"模型闭合判断：{report.get('closed', '未提供')}。", report.get("summary", ""), ""])
        for finding in report.get("findings", []):
            lines.append(f"- 第 {finding['chapter']} 章 · {finding['severity']}：{finding['reason']}")
            lines.extend(["", *("> " + line for line in finding["quote"].splitlines()), ""])
            if finding.get("repair"):
                lines.extend([finding["repair"], ""])
        for promise in report.get("promises", []):
            lines.extend([f"- 承诺：{promise['condition']} · {promise['result']}", ""])
            if promise["result"] != "pass":
                lines.extend("> " + line for line in promise.get("evidence", "").splitlines())
            lines.append("")
    if waiting:
        payload = json.loads(waiting["payload"])
        lines.extend(
            [
                "",
                "## 待审候选（尚未采纳）",
                "",
                f"候选 `{waiting['id']}`；目标章节 `{waiting['chapter_id']}`；基线修订 {waiting['base_revision']}。",
                f"[候选全文]({task['id']}-待审候选.md)。当前已保存正文仍在独立正文文件中。",
                "",
            ]
        )
        review = payload.get("review", {})
        lines.append(review.get("summary", "尚无模型审查结果。"))
        for check in review.get("checks", []):
            lines.extend(["", f"- {check['condition']}：{check['result']}"])
        for finding in review.get("findings", []):
            lines.extend(
                ["", f"- 第 {finding['chapter']} 章 · {finding['severity']}：{finding['reason']}", ""]
            )
            lines.extend("> " + line for line in finding["quote"].splitlines())
            lines.extend(["", finding["repair"]])
    lines.extend(["", "## 运行与人工干预记录", ""])
    for event in entry["events"]:
        if event["kind"] in {
            "resumed",
            "paused",
            "cancelled",
            "budget_reconfigured",
            "model_reconfigured",
            "continued_with_account",
            "continued_from",
            "resume_requested",
            "pause_requested",
            "cancel_requested",
            "configuration_changed",
        }:
            lines.append(f"- {event['kind']}：{event['payload']}")
    for item in result.get("interventions", []):
        lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
    lines.extend(
        ["", "完整规划、全部版本产物、事件、来源和费用见同目录 evidence.json；正文另存为任务标识.md。", ""]
    )
    return "\n".join(lines)


def _ledger_calls(usage, task_ids):
    marks = ",".join("?" for _ in task_ids)
    return [
        dict(row)
        for row in usage.execute(
            "SELECT c.*,m.actual_model,m.request_id FROM calls c LEFT JOIN call_metadata m ON m.call_id=c.id "
            f"WHERE task_id IN ({marks}) ORDER BY c.created,c.id",
            task_ids,
        )
    ]


def export_evidence(settings, task_ids, *, usage_ledger=None):
    if usage_ledger is not None:
        usage_ledger = usage_ledger.resolve()
        if not usage_ledger.is_file():
            raise DomainError("acceptance_evidence", "指定的共享费用账本不存在。", 409)
        local_usage = settings.data_dir / "usage.sqlite"
        if local_usage.is_file() and local_usage.samefile(usage_ledger):
            raise DomainError("acceptance_evidence", "共享费用账本须与验收稿件目录的费用账本分开。", 409)
    with data_directory_gate(settings.data_dir, wait=True):
        backup = create_backup(settings, Database(settings.data_dir / "manuscripts.sqlite"))
    folder = settings.data_dir / "acceptance" / ("export-" + uid())
    folder.mkdir(parents=True)
    report = {
        "created": time(),
        "backup": backup,
        "human_creative_acceptance": "unverified",
        "human_benefit_acceptance": "unverified",
        "note": "从同一数据库备份恢复后导出。运行中任务只是该快照的进度，不代表整书完成。费用为配置价格估算；未知调用保留预留。",
        "tasks": [],
    }
    with TemporaryDirectory(dir=settings.data_dir) as temporary, ExitStack() as stack:
        restored = Path(temporary)
        counts = unpack_verified(Path(backup["path"]), restored)
        report["restore"] = {"integrity": "ok", "counts": counts}
        db = Database(restored / "manuscripts.sqlite")
        domain = Domain(db)
        ledger = usage_ledger if usage_ledger is not None else restored / "usage-evidence.sqlite"
        usage = None
        if usage_ledger is not None or ledger.is_file():
            usage = stack.enter_context(
                closing(sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True))
            )
            usage.row_factory = sqlite3.Row
            # Pin one read transaction before capturing any of the requested families.
            usage.execute("BEGIN")
            usage.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchone()
        for task_id in dict.fromkeys(task_ids):
            task = domain.task(task_id)
            branch_id = task["branch_id"]
            related_tasks = db.all("SELECT * FROM tasks WHERE branch_id=? ORDER BY created", (branch_id,))
            related_ids = [item["id"] for item in related_tasks]
            placeholders = ",".join("?" for _ in related_ids)
            manuscript = domain.export(branch_id)
            (folder / (task_id + ".md")).write_text(manuscript, encoding="utf-8", newline="\n")
            chapters = db.all("SELECT * FROM chapters WHERE branch_id=? ORDER BY position", (branch_id,))
            entry = {
                "task": task,
                "related_tasks": related_tasks,
                "branch": domain.branch(branch_id),
                "chapters": [{**c, "nonblank_chars": char_count(c["content"])} for c in chapters],
                "manuscript_sha256": hashlib.sha256(manuscript.encode()).hexdigest(),
                "facts": db.all("SELECT * FROM facts WHERE branch_id=?", (branch_id,)),
                "artifacts": db.all(
                    "SELECT * FROM artifacts WHERE branch_id=? ORDER BY created", (branch_id,)
                ),
                "events": db.all(
                    f"SELECT * FROM task_events WHERE task_id IN ({placeholders}) ORDER BY id", related_ids
                ),
                "steps": db.all(f"SELECT * FROM task_steps WHERE task_id IN ({placeholders})", related_ids),
            }
            if task["kind"] == "seven_stage_run":
                entry["seven_stage"] = _seven_stage_evidence(domain, entry, folder)
            else:
                entry["completion"] = domain.completion(branch_id)
            run = entry.get("seven_stage")
            if run:
                billed_ids = [task_id, *run["child_task_ids"]]
            else:
                billed_ids = related_ids if usage_ledger is None else [task_id]
            calls = _ledger_calls(usage, billed_ids) if usage is not None else []
            if run or usage_ledger is not None:
                entry["billing_evidence"] = {
                    "source": "shared_ledger" if usage_ledger is not None else "data_directory_backup",
                    "task_ids": billed_ids,
                    "state": "captured" if calls else "no_matching_calls",
                    "row_count": len(calls),
                }
            if usage is not None:
                entry["calls"] = calls
                entry["cost_by_currency"] = {
                    currency: {
                        "settled": sum(c["charged"] or 0 for c in calls if c["currency"] == currency)
                        / 1_000_000,
                        "held_unknown": sum(
                            c["reserved"] for c in calls if c["currency"] == currency and c["charged"] is None
                        )
                        / 1_000_000,
                    }
                    for currency in {c["currency"] for c in calls}
                }
            report["tasks"].append(entry)
            (folder / (task_id + "-修订报告.md")).write_text(
                revision_report(entry), encoding="utf-8", newline="\n"
            )
            if run := entry.get("seven_stage"):
                planning = ["# 接力使用的规划", "", task["payload"].get("idea") or "", ""]
                for key, title in (("project_id", "立项"), ("setting_id", "设定"), ("outline_id", "大纲")):
                    planning.extend([f"- {title} · `{run['dependencies'][key] or '尚未记录'}`"])
                planning.extend(["", "完整确认版本和章纲见 evidence.json。", ""])
            else:
                planning = ["# 采用设定与规划", "", task["payload"].get("brief", ""), ""]
                planning += ["## 固定条件", "", *task["payload"].get("fixed", []), ""]
                for plot in task["result"].get("plan", {}).get("chapters", []):
                    planning += [f"## 第 {plot['number']} 章 · {plot['title']}", "", plot["plot"], ""]
                    planning += ["固定条件：" + "；".join(plot.get("fixed", [])), ""]
            (folder / (task_id + "-规划.md")).write_text("\n".join(planning), encoding="utf-8", newline="\n")
            waiting = next(
                (a for a in entry["artifacts"] if a["id"] == task["result"].get("waiting_artifact")),
                None,
            )
            if waiting:
                candidate = json.loads(waiting["payload"])
                (folder / (task_id + "-待审候选.md")).write_text(
                    f"# 尚未采纳的候选\n\n候选 {waiting['id']}；基线修订 {waiting['base_revision']}。\n\n"
                    + candidate.get("text", json.dumps(candidate, ensure_ascii=False, indent=2))
                    + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
    (folder / "evidence.json").write_text(dumps(report), encoding="utf-8", newline="\n")
    lines = ["# 整书验收证据快照", "", report["note"], "", "作者创作质量与实际收益：未验证。", ""]
    for entry in report["tasks"]:
        task = entry["task"]
        if run := entry.get("seven_stage"):
            lines.extend(
                [
                    f"## {entry['branch']['name']} · 七台接力",
                    "",
                    f"批次 `{task['id']}` · {task['status']} · 阶段 {run['stage']}。",
                    f"[快照当前正文]({task['id']}.md) · SHA-256 `{entry['manuscript_sha256']}`。",
                    f"[接力修订与验收报告]({task['id']}-修订报告.md) · [接力规划]({task['id']}-规划.md)。",
                    "",
                ]
            )
            continue
        completed = entry["completion"]
        lines.extend(
            [
                f"## {entry['branch']['name']}",
                "",
                f"任务：{task['id']}；状态：{task['status']}；已保存 {completed['chapter_count']}/{completed['expected']} 章，{completed['chars']} 非空白字符。",
                "",
                f"模型辅助完结：{completed['production_complete']}；三层正式验收：{completed['accepted']}。",
                "",
                f"[正文]({task['id']}.md)；[修订与验收报告]({task['id']}-修订报告.md)；SHA-256：`{entry['manuscript_sha256']}`。",
                "",
                f"配置估算费用：{json.dumps(entry.get('cost_by_currency', {}), ensure_ascii=False)}。",
                "",
                f"[采用设定与规划]({task['id']}-规划.md)。",
                "",
                "规划、事实、失败候选、修订报告、恢复事件、作者干预和全部调用记录见 evidence.json；数据库与原件恢复结果为 ok。",
                "",
            ]
        )
    (folder / "README.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"folder": str(folder), "tasks": len(report["tasks"]), "restore": report["restore"]}


def main():
    parser = argparse.ArgumentParser(description="从实际备份恢复并导出验收证据，不调用模型")
    parser.add_argument("--task", action="append", required=True)
    parser.add_argument("--usage-ledger", type=Path, help="只读核对独立验收稿库关联的共享费用账本")
    args = parser.parse_args()
    print(
        json.dumps(
            export_evidence(Settings.load(), args.task, usage_ledger=args.usage_ledger), ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
