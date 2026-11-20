"""Exercise the seven assistant entry points using one isolated two-chapter work."""

import argparse
import asyncio
import json
import os
import subprocess
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.budget import Budget, micro
from ainovel.db import uid
from ainovel.providers import ModelClient
from ainovel.settings import Settings
from ainovel.worker import Worker


ORIGINAL = "修钟人把账簿推到桌心，只翻开有印记的一页。学徒记下印记所在的位置，合上簿子，按先前约定去查门栓。"
IDEA = "学徒拿着一枚无声铜铃寻找师姐，靠可以回查的线索辨认同一枚铃，最后在旧屋归还。"
BODY = (
    "学徒在西门外摇了摇铜铃，铃没有响。他从旧簿里抄下断口的记号，沿着石坎下到山坳。",
    "师姐把匣中半片铜贴到铃身，断口严丝合缝。学徒把铃递回她掌心。师姐接过铜铃，仍旧没有摇响它。",
)


def request(client, method, path, **options):
    response = client.request(method, path, **options)
    if not response.is_success:
        raise RuntimeError(f"{method} {path} → {response.status_code} {response.text}")
    return response.json()


class Trial:
    def __init__(self, client, worker, account, evidence, budget):
        self.client = client
        self.worker = worker
        self.account = account
        self.evidence = evidence
        self.budget = budget
        self.turns = []
        self.tasks = []

    def http(self, method, path, **options):
        return request(self.client, method, path, **options)

    def calls(self):
        if not self.tasks:
            return []
        placeholders = ",".join("?" for _ in self.tasks)
        with self.worker.budget.db() as ledger:
            return [
                dict(row)
                for row in ledger.execute(
                    "SELECT c.task_id,c.charged,c.reserved,c.status,c.input_tokens,c.output_tokens,m.actual_model "
                    "FROM calls c LEFT JOIN call_metadata m ON m.call_id=c.id "
                    f"WHERE c.task_id IN ({placeholders}) ORDER BY c.created,c.id",
                    self.tasks,
                )
            ]

    def log(self, **details):
        calls = self.calls()
        data = {
            **details,
            "turns": self.turns,
            "calls": calls,
            "settled_cny": sum(row["charged"] or 0 for row in calls) / 1_000_000,
            "reserved_cny": sum(row["reserved"] for row in calls if row["charged"] is None) / 1_000_000,
        }
        (self.evidence / "assistant-trial.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def turn(self, stage, field, draft, question, *, scope=None, thread=None, selection=None):
        root = self.base + "/assistant/conversations"
        if thread is None:
            thread = self.http(
                "POST",
                root,
                json={
                    "stage": stage,
                    "scope_id": scope,
                    "subject": stage + " " + field,
                    "client_request_id": uid(),
                },
            )["id"]
        route = f"{root}/{thread}/turns"
        current = self.http("GET", route)
        remaining = micro(self.budget) - sum(
            row["reserved"] if row["charged"] is None else row["charged"] for row in self.calls()
        )
        if remaining <= 0:
            raise RuntimeError("本次真实验收预算已用完。")
        payload = {
            "target": field,
            "draft_text": draft,
            "question": question,
            "action": "review" if stage == "M6" else "refine",
            "target_key": field,
            "context_key": "live-acceptance",
            "account": self.account,
            "base_revision": current["conversation"]["revision"],
            "basis_sha256": current["basis"]["sha256"],
            "client_request_id": uid(),
            "budget_limit": remaining / 1_000_000,
        }
        if selection is not None:
            payload["selection"] = selection
        queued = self.http("POST", route, json=payload)["turn"]
        self.tasks.append(queued["task_id"])
        entry = {
            "stage": stage,
            "field": field,
            "thread": thread,
            "turn_id": queued["id"],
            "task_id": queued["task_id"],
        }
        self.turns.append(entry)
        self.log()
        if not asyncio.run(self.worker.run_once()):
            raise RuntimeError("未领取待验收任务。")
        turn = self.http("GET", route)["turns"][-1]
        entry.update(
            status=turn["status"],
            error=turn["error"],
            result=turn["result"],
            basis_current=turn["basis_current"],
        )
        self.log()
        if turn["status"] != "succeeded" or not turn["result"] or not turn["basis_current"]:
            raise RuntimeError(f"{stage} {field} 未完成，{turn['error']}")
        return thread, turn

    def use(self, thread, turn, field):
        value = next(item["value"] for item in turn["result"]["suggestions"] if item["field"] == field)
        self.http(
            "POST",
            f"{self.base}/assistant/conversations/{thread}/turns/{turn['id']}/apply-receipt",
            json={"field": field},
        )
        return value

    def run(self):
        branch = self.http("POST", "/api/novels", json={"title": "七台助手试写 · 遗铃"})["branch_id"]
        self.base = base = f"/api/branches/{branch}"
        subject = "一个关于遗失和归还的短篇。"
        thread, _ = self.turn("M1", "position", subject, "保留原意，润色核心定位并返回一条修改候选。")
        _, followup = self.turn(
            "M1",
            "position",
            subject,
            "请沿着上一轮继续调整，让定位具体到凭断口辨认、最终归还铜铃，返回修改候选。",
            thread=thread,
        )
        position = self.use(thread, followup, "position")
        self.http(
            "PUT",
            base + "/conception/project-brief",
            json={
                "base_revision": 0,
                "idempotency_key": uid(),
                "idea": IDEA,
                "brief": {
                    "position": position,
                    "track": "求证悬疑短篇",
                    "genre_ids": ["mystery"],
                    "audience": "喜欢回查线索的读者",
                    "distinction": "铜铃始终无声，以断口取证",
                    "length": "两章试写",
                    "ending": "师姐以合缝核认身份，学徒归还铜铃",
                    "cadence": "",
                    "final_image": "",
                },
            },
        )

        book = self.http(
            "POST", "/api/library/import", files={"file": ("自编验收资料.txt", ORIGINAL.encode())}
        )
        unit = self.http("GET", f"/api/library/books/{book['id']}/units")[0]["id"]
        session = self.http(
            "POST",
            "/api/reference-sessions",
            json={
                "unit_id": unit,
                "start": 0,
                "end": len(ORIGINAL.encode("utf-16-le")) // 2,
                "quote": ORIGINAL,
                "goal": "研究有限线索如何给出明确的下一步。",
                "branch_id": branch,
            },
        )
        self.turn(
            "M2",
            "technique",
            "从可回查的细节推进。",
            "提炼一条可迁移写法，只使用本次参考原文。",
            scope=session["id"],
        )

        settings_path = base + "/conception"
        state = self.http("GET", settings_path)
        world = next(category for category in state["categories"] if category["id"] == "world")
        world["items"].append(
            {"id": "silent-bell", "title": "铜铃无声", "detail": "铜铃不会发声，断口可以作比对证据。"}
        )
        state = self.http(
            "PUT",
            settings_path,
            json={
                "base_revision": state["revision"],
                "base_story_revision": state["story_revision"],
                "categories": state["categories"],
            },
        )
        self.turn(
            "M3",
            "detail",
            "铜铃不会发声，断口可以作比对证据。",
            "润色单条世界规则，保留铜铃始终无声。",
            scope="silent-bell",
        )
        confirmed = self.http(
            "POST",
            settings_path + "/confirm",
            json={
                "base_revision": state["revision"],
                "base_story_revision": state["story_revision"],
                "idempotency_key": uid(),
            },
        )["confirmation"]["id"]

        chapters = [self.http("POST", base + "/chapters", json={"title": name}) for name in ("试铃", "归还")]
        outline = base + "/outline-workspace"
        view = self.http(
            "PUT",
            outline,
            json={
                "base_revision": 0,
                "confirmation_id": confirmed,
                "plan": {
                    "summary": "从无声的铜铃取得可核对证据，再完成归还。",
                    "volumes": [
                        {
                            "id": "investigation",
                            "title": "查证",
                            "summary": "西门到旧屋",
                            "units": [
                                {
                                    "id": "verify",
                                    "title": "查断口",
                                    "summary": "核对并归还",
                                    "chapters": [
                                        {
                                            "chapter_id": row["id"],
                                            "goal": "追查铜铃归属",
                                            "obstacle": "铃声无法提供线索",
                                            "turn": "断口可以比对",
                                            "result": "前进到下一个证据",
                                            "setting_ids": ["silent-bell"],
                                        }
                                        for row in chapters
                                    ],
                                }
                            ],
                        }
                    ],
                },
            },
        )
        self.http("POST", outline + "/confirm", json={"base_revision": view["draft"]["revision"]})
        self.turn(
            "M4",
            "turn",
            "断口可以比对",
            "把第一个转折写成可执行的章纲动作，保留正式约定。",
            scope=chapters[0]["id"],
        )

        for chapter, body in zip(chapters, BODY, strict=True):
            self.http("PUT", f"/api/chapters/{chapter['id']}", json={"base_revision": 0, "content": body})
        selection = "铃没有响。"
        start = BODY[0].index(selection)
        thread, candidate = self.turn(
            "M5",
            "selection",
            selection,
            "润色所选文字。只描写这一刻，保留铜铃无声。",
            scope=chapters[0]["id"],
            selection={"start": start, "end": start + len(selection), "revision": 1},
        )
        replacement = self.use(thread, candidate, "selection")
        self.http(
            "PUT",
            f"/api/chapters/{chapters[0]['id']}",
            json={
                "base_revision": 1,
                "content": BODY[0][:start] + replacement + BODY[0][start + len(selection) :],
            },
        )

        for check in range(1, 11):
            self.turn(
                "M6", f"{check:02d}", "", "按本项规则说明依据、证据缺口和下一步核对建议，保留正式判定。"
            )
        self.turn("M7", "intro", "", "只根据开篇有效章稿起草交付简介，保留后续归还的悬念。")
        completion = self.http("GET", base + "/book-completion")
        if completion["latest"] or all(item["status"] == "green" for item in completion["checks"]):
            raise RuntimeError("试写意外触发了正式完本。")
        self.log(
            branch=branch, chapters=[row["id"] for row in chapters], completion_checks=completion["checks"]
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dotenv", type=Path, required=True)
    parser.add_argument("--account", default="deepseek")
    parser.add_argument("--budget", type=Decimal, required=True)
    parser.add_argument("--execute-live", action="store_true", required=True)
    args = parser.parse_args()
    if not args.budget.is_finite() or not 0 < args.budget <= 8:
        parser.error("真实验收总预算须在大于零且不超过 8 元的范围。")
    if not args.config.is_file() or not args.dotenv.is_file():
        parser.error("需要既有本机配置和凭据文件。")
    os.environ["AINOVEL_CONFIG"] = str(args.config.resolve())
    load_dotenv(args.dotenv, override=False)
    live = Settings.load()
    provider = live.providers.get(args.account)
    if (
        not provider
        or provider.kind != "deepseek"
        or not provider.key
        or provider.input_rate is None
        or provider.output_rate is None
    ):
        parser.error("需要已有凭据和单价的 DeepSeek 账户。")
    if urlsplit(provider.base_url).hostname != "api.deepseek.com":
        parser.error("真实验收使用现有 DeepSeek API 地址。")
    if not (live.data_dir / "usage.sqlite").is_file():
        parser.error("需要现有共享费用账本。")
    parent = live.data_dir / "acceptance" / "workbench-assistant"
    parent.mkdir(parents=True, exist_ok=True)
    evidence = Path(mkdtemp(prefix="yiling-", dir=parent))
    print(f"验收目录 {evidence}", flush=True)
    (evidence / "basis.json").write_text(
        json.dumps(
            {
                "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "account": args.account,
                "model": provider.model,
                "budget_cny": str(args.budget),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    isolated = replace(live, data_dir=evidence, providers={args.account: provider}, test_mode=True)
    shared = Budget(live)
    app = create_app(isolated)
    app.state.budget.path = shared.path
    worker = Worker(isolated, ModelClient(isolated, shared))
    worker.budget = shared
    with TestClient(app) as client:
        client.headers["X-Ainovel-CSRF"] = request(client, "GET", "/api/session")["csrf"]
        trial = Trial(client, worker, args.account, evidence, args.budget)
        try:
            trial.run()
        except Exception as error:
            trial.log(failed=str(error))
            raise
    print("七台助手真实验收已完成。", flush=True)


if __name__ == "__main__":
    main()
