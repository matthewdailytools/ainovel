"""Opt-in paid evaluations. All outputs and records stay in the user's data directory."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from time import time

from .backups import create_backup, verify_backup
from .db import Database, dumps, uid
from .domain import Domain
from .library import Library
from .models import WorkflowInput
from .settings import ROOT, Settings
from .worker import Worker


async def evaluate(args):
    settings = Settings.load()
    db = Database(settings.data_dir / "manuscripts.sqlite")
    domain = Domain(db)
    Library(db, settings.data_dir)
    worker = Worker(settings)
    story = json.loads((ROOT / "fixtures/original-story.json").read_text("utf-8"))
    folder = settings.data_dir / "acceptance"
    folder.mkdir(exist_ok=True)
    record_id = uid()
    path = folder / (record_id + ".json")
    record = {
        "id": record_id,
        "started": time(),
        "stage": args.stage,
        "chapters": args.chapters,
        "user_caps": {"openai": "20 USD shared", "deepseek": "80 CNY"},
        "fixture_sha256": hashlib.sha256(dumps(story).encode()).hexdigest(),
        "tasks": [],
        "human_acceptance": "unverified",
    }
    path.write_text(dumps(record), encoding="utf-8")
    print(dumps({"record": str(path), "stage": args.stage}), flush=True)
    accounts = args.accounts.split(",")
    if any(account not in settings.providers for account in accounts):
        raise ValueError("Unknown account")
    novel = domain.create_novel(
        ("验收样例" if args.stage == "samples" else story["title"]) + " · " + record_id[:6], story["brief"]
    )
    cases = (
        [(account, "assisted", repeat) for account in accounts for repeat in range(1, 4)]
        if args.stage == "samples"
        else [(accounts[0], "assisted", 1), (accounts[-1], "automatic", 1)]
    )
    for account, mode, repeat in cases:
        provider = settings.providers[account]
        count = 1 if args.stage == "samples" else args.chapters
        plots = (
            [
                {
                    "number": 1,
                    "title": "渡口未成的交易",
                    "plot": "沈砚在渡口与货商谈换铜环。对方临时加价，沈砚付不起，双方没有成交。二人克制收场，不发生任何战斗或肢体冲突。沈砚改向熟人借旧尺，结尾明确本次交易失败这一结果。不要写后续章节。",
                    "fixed": ["本次交易没有成交。", "双方没有发生战斗或肢体冲突。"],
                }
            ]
            if args.stage == "samples"
            else story["chapters"]
            if count == 36
            else [
                {
                    "number": index + 1,
                    "title": f"第{index + 1}章 潮尺档案",
                    "plot": "将以下连续情节写成一个完整章节，保留各结果："
                    + " ".join(
                        p["plot"] for p in story["chapters"][index * 36 // count : (index + 1) * 36 // count]
                    ),
                    "fixed": [],
                }
                for index in range(count)
            ]
        )
        spec = WorkflowInput(
            novel_id=novel["id"],
            mode=mode,
            brief=story["brief"]
            if args.stage != "samples"
            else "仅写一篇有明确结果的原创渡口短场景，不扩展为后续故事。主角沈砚是普通修堤匠。",
            fixed=story["fixed"],
            plots=plots if mode == "assisted" else [],
            chapters=count,
            min_chars=500 if args.stage == "samples" else args.min_chars,
            max_chars=900 if args.stage == "samples" else args.max_chars,
            account=account,
            adoption="automatic",
            budget_limit=args.run_limit,
        )
        branch_id = uid()
        with db.connect(True) as tx:
            tx.execute(
                "INSERT INTO branches(id,novel_id,name,mode,created) VALUES(?,?,?,?,?)",
                (branch_id, novel["id"], f"{mode} · {account} · R{repeat}", mode, time()),
            )
        task = domain.queue("workflow", spec.model_dump(), branch_id)
        entry = {
            "task_id": task["id"],
            "branch_id": branch_id,
            "mode": mode,
            "account": account,
            "model": provider.model,
            "repeat": repeat,
            "started": time(),
            "status": "queued",
        }
        record["tasks"].append(entry)
        path.write_text(dumps(record), encoding="utf-8")
        print(dumps({"task": task["id"], "account": account, "mode": mode, "repeat": repeat}), flush=True)
        while True:
            await worker.run_once()
            state = domain.task(task["id"])
            if state["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(1)
        entry.update(
            status=state["status"],
            error=state["error"],
            finished=time(),
            completion=domain.completion(branch_id),
        )
        (folder / (task["id"] + ".md")).write_text(domain.export(branch_id), encoding="utf-8", newline="\n")
        path.write_text(dumps(record), encoding="utf-8")
        print(
            dumps(
                {
                    "task": task["id"],
                    "status": state["status"],
                    "error": state["error"],
                    "chapters": entry["completion"]["chapter_count"],
                }
            ),
            flush=True,
        )
        # Do not multiply a configuration or transport failure into repeated paid attempts.
        if state["status"] == "blocked_runtime":
            record["stopped_reason"] = state["error"]
            break
    backup = create_backup(settings, db)
    record["backup"] = {**backup, "verification": verify_backup(Path(backup["path"]))}
    record["budget"] = worker.budget.report()
    record["finished"] = time()
    path.write_text(dumps(record), encoding="utf-8")
    print(
        dumps({"finished": str(path), "statuses": [entry["status"] for entry in record["tasks"]]}), flush=True
    )


def main():
    parser = argparse.ArgumentParser(description="显式执行真实模型评测，始终受共享费用账本保护")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--stage", choices=("samples", "books"), default="samples")
    parser.add_argument("--accounts", default="openai_1,openai_2,deepseek")
    parser.add_argument("--chapters", type=int, default=36, choices=range(1, 37))
    parser.add_argument("--min-chars", type=int, default=2500)
    parser.add_argument("--max-chars", type=int, default=3500)
    parser.add_argument("--run-limit", type=float, default=5)
    args = parser.parse_args()
    if not args.execute:
        parser.error("需显式 --execute；本命令会调用付费模型。")
    asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()
