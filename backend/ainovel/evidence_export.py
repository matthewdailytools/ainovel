"""Export a frozen acceptance bundle without dispatching any model calls."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from time import time

from .backups import create_backup, unpack_verified
from .db import Database, dumps, uid
from .domain import Domain, char_count
from .settings import Settings


def revision_report(entry):
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


def export_evidence(settings, task_ids):
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
    with TemporaryDirectory(dir=settings.data_dir) as temporary:
        restored = Path(temporary)
        counts = unpack_verified(Path(backup["path"]), restored)
        report["restore"] = {"integrity": "ok", "counts": counts}
        db = Database(restored / "manuscripts.sqlite")
        domain = Domain(db)
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
                "completion": domain.completion(branch_id),
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
            ledger = restored / "usage-evidence.sqlite"
            if ledger.exists():
                with closing(sqlite3.connect(ledger)) as usage:
                    usage.row_factory = sqlite3.Row
                    calls = [
                        dict(c)
                        for c in usage.execute(
                            f"SELECT c.*,m.actual_model,m.request_id FROM calls c LEFT JOIN call_metadata m ON m.call_id=c.id WHERE task_id IN ({placeholders}) ORDER BY created",
                            related_ids,
                        )
                    ]
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
    args = parser.parse_args()
    print(json.dumps(export_evidence(Settings.load(), args.task), ensure_ascii=False))


if __name__ == "__main__":
    main()
