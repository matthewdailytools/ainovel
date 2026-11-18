"""Run original new/old conception acceptance with canonical shared billing."""

import argparse
import asyncio
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import mkdtemp
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.budget import Budget
from ainovel.conception_generation import CONSISTENCY_VERSION, VERSION, WORLD_EXTRACTION_VERSION
from ainovel.domain import utf16_slice
from ainovel.providers import ModelClient
from ainovel.settings import Settings
from ainovel.story_graph import sources
from ainovel.worker import Worker

ROOT = Path(__file__).resolve().parent.parent

IDEA = (
    "写一部架空港城的悬疑小说。修钟学徒谷萤发现公钟每次错报都会改变工人能领到的工钱，"
    "母亲留下的旧签到册少了一页。她必须找到缺页并在公开复核时验证真实报时；不靠超自然能力，"
    "不以打败某个恶人直接解决制度问题。结局收束已发现的工钱差额和当事人的选择，下一代如何改制度可以留白。"
    "各类别用两至三个有区分度的短条目，以跨类别关联保持证据和人物选择连贯。"
)
ORIGINAL = [
    (
        "开门",
        "清晨六点，谷萤给港口公钟上了发条。她母亲从前就在这间钟房记工，周蕙接替她母亲照看账册。"
        "周蕙将新工登记簿摊平，指给谷萤看：按港口规矩，公钟报六点以前到场的人算足工，"
        "迟到的只领半天的钱。谷萤在旧签到册的第七和第九页之间看到针线断口。"
        "她没问周蕙缺页去哪了，只在新本封里夹进去一片取下的线头。",
    ),
    (
        "对表",
        "当天午后，谷萤请周蕙拿一块随身表到钟房来。谷萤把钟房里的一根短线移开，"
        "公钟恢复了原本的报时，周蕙在两本账上抄下同一个读数。"
        "这根短线是谁拴上的，周蕙没有回答。谷萤留下短线，决定等明天当着工人的面重新对表。",
    ),
]


def checked(response):
    response.raise_for_status()
    return response.json()


def write_json(path, value):
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")


def source_manifest():
    sources_to_pin = sorted((ROOT / "backend" / "ainovel").rglob("*.py"))
    sources_to_pin.extend([Path(__file__).resolve(), ROOT / "uv.lock"])
    return {
        path.relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest() for path in sources_to_pin
    }


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_quote(chapters, source):
    chapter = chapters[source["chapter_id"]]
    assert source["revision"] == chapter["revision"]
    _, excerpt, _ = utf16_slice(chapter["content"], source["start"], source["end"])
    assert excerpt == source["quote"]


def adopt_conception(client, base, artifact, current, selected, edits=None):
    selected = set(selected)
    edits = edits or {}
    assert set(edits) <= selected
    assert all(set(fields) <= {"title", "detail", "links"} for fields in edits.values())
    chosen = [
        {
            "id": category["id"],
            "items": [
                {**item, **edits.get(item["id"], {})} for item in category["items"] if item["id"] in selected
            ],
        }
        for category in artifact["payload"]["categories"]
    ]
    assert selected and len(selected) == sum(len(category["items"]) for category in chosen)
    adopted = checked(
        client.post(
            f"{base}/conception/adopt",
            json={
                "base_revision": current["revision"],
                "base_story_revision": current["story_revision"],
                "artifact_id": artifact["id"],
                "categories": [category for category in chosen if category["items"]],
                "idempotency_key": uuid4().hex,
            },
        )
    )
    return adopted


def verify_model_review(client, summary, artifact, evidence):
    identifier = summary["task_id"]
    payload = artifact["payload"]
    review = payload["model_review"]
    assert review["version"] == CONSISTENCY_VERSION
    target_ids = {row["id"] for row in payload["categories"]}
    assert all(issue["category_id"] in target_ids and issue["message"].strip() for issue in review["issues"])
    transcript = checked(client.get(f"/api/tasks/{identifier}/transcript"))
    assert {row["result"]["call_id"] for row in transcript["steps"]} <= {
        row["id"] for row in summary["calls"]
    }
    events = client.get(f"/api/tasks/{identifier}/events").raise_for_status().text
    emitted = [json.loads(line[6:]) for line in events.splitlines() if line.startswith("data: ")]
    dispatched = [
        row["payload"]
        for row in emitted
        if row.get("kind") == "step_dispatched" and row["payload"]["schema"] == "ConceptionConsistencyReview"
    ]
    assert any(row["action"] == "conception.review.bundle" for row in dispatched)
    # A structured output repair uses the same review schema and a distinct
    # action/fingerprint. The initial truncated call can have billing without
    # a successful transcript row.
    fingerprints = {row["fingerprint"] for row in dispatched}
    completed = [row for row in transcript["steps"] if row["fingerprint"] in fingerprints]
    assert completed and len(summary["calls"]) >= len(target_ids) + 1
    write_json(
        evidence / "model-review-verification.json",
        {
            "version": CONSISTENCY_VERSION,
            "artifact_id": artifact["id"],
            "dispatches": [
                {
                    "action": row["action"],
                    "fingerprint": row["fingerprint"],
                    "output_limit": row["request"]["max_output_tokens"],
                }
                for row in dispatched
            ],
            "call_ids": [row["result"]["call_id"] for row in completed],
            "issues": review["issues"],
            "verifier_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )


def apply_review(client, phase, evidence, decision_path):
    queue = read_json(evidence / "queued.json")
    assert queue["phase"] == phase
    branch = queue["branch"]
    base = f"/api/branches/{branch}"
    artifact = read_json(evidence / "candidate.json")
    assert artifact["branch_id"] == branch and artifact["task_id"] == queue["task"]
    decision = read_json(decision_path)
    assert decision["artifact_id"] == artifact["id"] and decision["note"].strip()
    current = checked(client.get(f"{base}/conception"))
    version = {"base_revision": current["revision"], "base_story_revision": current["story_revision"]}
    manuscripts = {
        row["id"]: checked(client.get(f"/api/chapters/{row['id']}"))
        for row in checked(client.get(f"{base}/chapters"))
    }
    if phase == "new":
        if (
            artifact["payload"].get("model_review")
            and not (evidence / "model-review-verification.json").exists()
        ):
            verify_model_review(client, read_json(evidence / "summary.json"), artifact, evidence)
        selected = set(decision["selected_ids"])
        adopted = adopt_conception(client, base, artifact, current, selected, decision.get("edited_items"))
        assert selected <= {item["id"] for category in adopted["categories"] for item in category["items"]}
    else:
        world_artifact = read_json(evidence / "world-candidate.json")
        assert world_artifact["id"] == decision["world_artifact_id"]
        assert world_artifact["branch_id"] == branch
        after_world = adopt_conception(
            client,
            base,
            world_artifact,
            current,
            decision["world_selected_ids"],
            decision.get("world_edited_items"),
        )
        assert after_world["story_revision"] == current["story_revision"]
        receipt = checked(
            client.post(
                f"{base}/story-extractions/{artifact['id']}/adopt",
                json={
                    "base_story_revision": version["base_story_revision"],
                    "candidate_revision": artifact["candidate_revision"],
                    "selected_ids": decision["selected_ids"],
                    "selected_scene_ids": decision.get("selected_scene_ids", []),
                    "idempotency_key": uuid4().hex,
                },
            )
        )
        adopted = checked(client.get(f"{base}/conception"))
        assert decision["selected_ids"] and set(receipt["record_ids"]) == set(decision["selected_ids"])
        assert adopted["observed"]
        assert adopted["categories"] == after_world["categories"]
    confirmed = checked(
        client.post(
            f"{base}/conception/confirm",
            json={
                "base_revision": adopted["revision"],
                "base_story_revision": adopted["story_revision"],
                "note": decision["note"],
                "idempotency_key": uuid4().hex,
            },
        )
    )
    assert confirmed["confirmation"]["current"]
    assert all(checked(client.get(f"/api/chapters/{key}")) == old for key, old in manuscripts.items())
    result = {
        "phase": phase,
        "artifact_id": artifact["id"],
        "selection": decision,
        "observed": confirmed["observed"],
        "issues": confirmed["issues"],
        "confirmation": confirmed["confirmation"],
        "chapter_versions_unchanged": len(manuscripts),
    }
    write_json(evidence / "applied-review.json", result)
    return result


def execute(client, worker, evidence, task, prefix=""):
    identifier = task["id"]
    start = perf_counter()
    assert asyncio.run(worker.run_once()), "未领取待验收任务"
    finished = checked(client.get(f"/api/tasks/{identifier}"))
    with worker.budget.db() as ledger:
        calls = [
            dict(row)
            for row in ledger.execute(
                "SELECT c.id,c.model,c.status,c.currency,c.reserved,c.charged,c.input_tokens,c.output_tokens,"
                "m.actual_model FROM calls c LEFT JOIN call_metadata m ON m.call_id=c.id WHERE task_id=? "
                "ORDER BY c.created,c.id",
                (identifier,),
            )
        ]
    summary = {
        "task_id": identifier,
        "status": finished["status"],
        "error": finished["error"],
        "artifact_id": finished["result"].get("artifact_id"),
        "elapsed_seconds": round(perf_counter() - start, 2),
        "calls": calls,
    }
    write_json(evidence / f"{prefix}summary.json", summary)
    write_json(evidence / f"{prefix}task.json", finished)
    assert calls, "验收需要同一任务关联的真实用量行"
    assert finished["status"] == "succeeded", finished["error"]
    assert all(row["status"] == "settled" and row["charged"] is not None for row in calls)
    artifact = checked(client.get(f"/api/artifacts/{summary['artifact_id']}"))
    write_json(evidence / f"{prefix}candidate.json", artifact)
    assert artifact["status"] == "candidate"
    return summary, artifact


def run_case(client, worker, account, phase, limit, evidence):
    novel = checked(client.post("/api/novels", json={"title": f"构思工作台真实验收 · {phase}"}))
    branch = novel["branch_id"]
    base = f"/api/branches/{branch}"
    if phase == "new":
        before = checked(client.get(f"{base}/conception"))
        task = checked(
            client.post(
                f"{base}/conception/generate",
                json={
                    "base_revision": before["revision"],
                    "base_story_revision": before["story_revision"],
                    "category_id": None,
                    "instruction": IDEA,
                    "account": account,
                    "client_request_id": uuid4().hex,
                    "budget_limit": limit,
                },
            )
        )
        chapters = {}
    else:
        chapters = {}
        for title, content in ORIGINAL:
            chapter = checked(client.post(f"{base}/chapters", json={"title": title}))
            identifier = chapter["id"]
            checked(client.put(f"/api/chapters/{identifier}", json={"base_revision": 0, "content": content}))
            chapters[identifier] = checked(client.get(f"/api/chapters/{identifier}"))
        before = checked(client.get(f"{base}/conception"))
        task = checked(
            client.post(
                f"{base}/story-extractions",
                json={
                    "base_story_revision": before["story_revision"],
                    "chapter_ids": list(chapters),
                    "account": account,
                    "client_request_id": uuid4().hex,
                    "budget_limit": limit * 0.75,
                    "max_output_tokens": 16_384,
                    "include_scene_ranges": True,
                },
            )
        )
    identifier = task["id"]
    write_json(evidence / "queued.json", {"task": identifier, "branch": branch, "phase": phase})
    summary, artifact = execute(client, worker, evidence, task)
    actual = sum(row["charged"] for row in summary["calls"])
    assert checked(client.get(f"{base}/conception")) == before
    assert checked(client.get(f"{base}/story-graph"))["records"] == []
    payload = artifact["payload"]
    if phase == "new":
        assert [row["id"] for row in payload["categories"]] == [row["id"] for row in before["categories"]]
        assert all(row["items"] for row in payload["categories"])
        assert checked(client.get(f"{base}/chapters")) == []
        verify_model_review(client, summary, artifact, evidence)
    else:
        assert [(row["id"], row["revision"]) for row in payload["source_manifest"]] == [
            (key, row["revision"]) for key, row in chapters.items()
        ]
        assert {"character", "event"} <= {row["record"]["kind"] for row in payload["records"]}
        for row in payload["records"]:
            for source in sources(row["record"]):
                check_quote(chapters, source)
        for chapter_id, chapter in chapters.items():
            current = checked(client.get(f"/api/chapters/{chapter_id}"))
            assert (current["content"], current["revision"]) == (chapter["content"], chapter["revision"])
        world_task = checked(
            client.post(
                f"{base}/conception/extract-world",
                json={
                    "base_revision": before["revision"],
                    "base_story_revision": before["story_revision"],
                    "chapter_ids": list(chapters),
                    "account": account,
                    "client_request_id": uuid4().hex,
                    "budget_limit": limit * 0.25,
                },
            )
        )
        write_json(
            evidence / "world-queued.json", {"task": world_task["id"], "branch": branch, "phase": phase}
        )
        world_summary, world_artifact = execute(client, worker, evidence, world_task, "world-")
        assert checked(client.get(f"{base}/conception")) == before
        assert checked(client.get(f"{base}/story-graph"))["records"] == []
        assert world_artifact["payload"]["categories"][0]["id"] == "world"
        assert world_artifact["payload"]["categories"][0]["items"]
        for item in world_artifact["payload"]["categories"][0]["items"]:
            check_quote(chapters, world_artifact["payload"]["source_evidence"][item["id"]])
        actual += sum(row["charged"] for row in world_summary["calls"])
    assert actual <= limit * 1_000_000
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("new", "old"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dotenv", type=Path, required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--budget", type=float)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--execute-live", action="store_true")
    mode.add_argument("--apply-review", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()

    if not args.config.is_file() or not args.dotenv.is_file():
        parser.error("必须指定既有的本机账户配置及凭据文件。")
    os.environ["AINOVEL_CONFIG"] = str(args.config.resolve())
    load_dotenv(args.dotenv, override=False)
    live = Settings.load()
    provider = live.providers.get(args.account)
    if not provider or not provider.key or provider.input_rate is None or provider.output_rate is None:
        parser.error("所选模型账户未完成本机配置。")
    domains = {"openai": "api.openai.com", "deepseek": "api.deepseek.com"}
    if urlsplit(provider.base_url).hostname != domains[provider.kind]:
        parser.error("验收只使用已配置的官方 API 端点。")
    canonical_db = live.data_dir / "usage.sqlite"
    if not canonical_db.is_file():
        parser.error("必须使用当前共享费用账本。")
    if args.execute_live and (
        args.budget is None
        or not 0 < args.budget <= ({"openai": 0.4, "deepseek": 2}[provider.kind])
        or args.evidence is not None
    ):
        parser.error("需要在共享费用账本内使用明确的单次验收上限。")
    with closing(sqlite3.connect(canonical_db.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("SELECT id FROM calls LIMIT 1")
    parent = live.data_dir / "acceptance" / "novel-conception"
    if args.apply_review:
        if args.evidence is None:
            parser.error("请提供此前验收的证据目录。")
        evidence = args.evidence.resolve()
        if evidence.parent != parent.resolve() or not (evidence / "manuscripts.sqlite").is_file():
            parser.error("验收审阅只允许操作既有的隔离数据目录。")
        if (evidence / "applied-review.json").exists():
            parser.error("已有本次验收的确认回执。")
        isolated = replace(live, data_dir=evidence, providers={}, test_mode=True)
        with TestClient(create_app(isolated)) as client:
            client.headers["X-Ainovel-CSRF"] = checked(client.get("/api/session"))["csrf"]
            result = apply_review(client, args.phase, evidence, args.apply_review)
        print(
            json.dumps(
                {"phase": args.phase, "confirmation_id": result["confirmation"]["id"]}, ensure_ascii=False
            )
        )
        return

    parent.mkdir(parents=True, exist_ok=True)
    evidence = Path(mkdtemp(prefix=f"{args.phase}-", dir=parent))
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    pinned = source_manifest()
    write_json(
        evidence / "basis.json",
        {
            "git_head": source,
            "phase": args.phase,
            "account": provider.public(),
            "budget_limit": args.budget,
            "generation_version": VERSION,
            "review_version": CONSISTENCY_VERSION,
            "world_extraction_version": WORLD_EXTRACTION_VERSION,
            "source_sha256": pinned,
        },
    )
    print(f"验收证据位于 {evidence}", flush=True)
    # The application and manuscript/fixtures use an isolated database. The real
    # provider reserves and settles calls in the original shared provider ledger.
    shared = Budget(live)
    isolated = replace(live, data_dir=evidence, providers={args.account: provider}, test_mode=True)
    app = create_app(isolated)
    worker = Worker(isolated, ModelClient(isolated, shared))
    worker.budget = shared
    app.state.budget.path = canonical_db
    try:
        with TestClient(app) as client:
            client.headers["X-Ainovel-CSRF"] = checked(client.get("/api/session"))["csrf"]
            summary = run_case(client, worker, args.account, args.phase, args.budget, evidence)
    finally:
        unchanged = source_manifest() == pinned
        write_json(evidence / "source-check.json", {"unchanged": unchanged})
    assert unchanged, "指定验收源码在调用期间变化，请按证据核对。"
    print(
        json.dumps(
            {key: val for key, val in summary.items() if key not in {"calls", "error"}}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
