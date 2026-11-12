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
                f"[正文]({task['id']}.md)；SHA-256：`{entry['manuscript_sha256']}`。",
                "",
                f"配置估算费用：{json.dumps(entry.get('cost_by_currency', {}), ensure_ascii=False)}。",
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
