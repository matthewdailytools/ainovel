"""Opt-in resumable long-form evaluation through the existing local API/Worker."""

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from time import sleep, time
from uuid import uuid4

import httpx

from .models import WorkflowInput
from .quality_reference import write_json
from .settings import ROOT, Settings

ACTIVE = {"queued", "running"}
EVALUATION_WRITING_PROFILE = (
    "第三人称限定视角，克制白话，人物选择有依据，遵守能力和信息边界。"
    "涉及测量或数值比较时先交代共同固定基准、单位与方向，前后一致；"
    "基准不能对齐时保留原始观测和待验证推测，不写成确定证据。"
    "估计与实测精度分开，自查算术及高低关系，不为解释而重复段落。"
)


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class RunSpec:
    stage: str = "ten"
    account: str = "deepseek"
    modes: tuple[str, ...] = ("assisted",)
    min_chars: int = 2500
    max_chars: int = 3500
    run_limit: float = 10

    def __post_init__(self):
        if self.stage not in {"ten", "books"}:
            raise ValueError("Stage must be ten or books; chapter counts are not silently reduced")
        if (
            not self.modes
            or len(set(self.modes)) != len(self.modes)
            or set(self.modes) - {"assisted", "automatic"}
        ):
            raise ValueError("Choose distinct assisted/automatic modes")
        if not 200 <= self.min_chars <= self.max_chars <= 15000 or self.min_chars > 10000:
            raise ValueError("Invalid chapter length range")
        if not 0 < self.run_limit < float("inf"):
            raise ValueError("An explicit positive per-workflow money limit is required")

    @property
    def chapters(self):
        return 10 if self.stage == "ten" else 36


def workflow_payload(
    spec,
    story,
    novel_id,
    mode,
    *,
    character_field_repair=False,
    plot_contract_check=False,
    structure_max_output_tokens=8192,
):
    if len(story["chapters"]) != 36 or [row["number"] for row in story["chapters"]] != list(range(1, 37)):
        raise ValueError("The original 36-chapter fixture must be complete and ordered")
    # A ten-chapter gate is the start of the original novel, not a compressed book.
    scope = (
        "\n本轮仅验证长篇开篇连续十章：发现异常、复测、追查初步证据及信息边界；"
        "仍是原定36章故事的开篇，不把全书结局压缩到第十章，不提前完成母亲去向及主冲突收束。"
        "批次结束可以保留后续承诺，不能冒充整书完结。"
        if spec.stage == "ten"
        else ""
    )
    return WorkflowInput(
        novel_id=novel_id,
        mode=mode,
        completion_scope="segment" if spec.stage == "ten" else "whole_book",
        brief=story["brief"] + scope,
        fixed=story["fixed"],
        plots=deepcopy(story["chapters"][: spec.chapters]) if mode == "assisted" else [],
        chapters=spec.chapters,
        account=spec.account,
        adoption="automatic",
        structured_maintenance=True,
        character_field_repair=character_field_repair,
        plot_contract_check=plot_contract_check,
        structure_max_output_tokens=structure_max_output_tokens,
        scene_maintenance=True,
        min_chars=spec.min_chars,
        max_chars=spec.max_chars,
        writing_profile=EVALUATION_WRITING_PROFILE,
        budget_limit=spec.run_limit,
    ).model_dump(exclude_none=True)


def read_usage(data_dir, task_ids):
    """Read the full ledger without creating, resetting or editing any fee record."""
    path = data_dir / "usage.sqlite"
    if not path.is_file():
        return {"status": "unavailable", "reason": "Usage ledger not found", "calls": []}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in task_ids)
        calls = (
            [
                dict(row)
                for row in db.execute(
                    f"SELECT c.*,m.actual_model,m.request_id FROM calls c LEFT JOIN call_metadata m ON m.call_id=c.id WHERE c.task_id IN ({placeholders}) ORDER BY c.created,c.id",
                    tuple(task_ids),
                )
            ]
            if task_ids
            else []
        )
    costs = {}
    for call in calls:
        amounts = costs.setdefault(call["currency"], {"settled": 0, "reserved_unknown": 0})
        amounts["settled" if call["charged"] is not None else "reserved_unknown"] += (
            call["charged"] if call["charged"] is not None else call["reserved"]
        ) / 1_000_000
    return {"status": "read_only_snapshot", "calls": calls, "cost_by_currency": costs}


class LongformEvaluation:
    def __init__(
        self,
        client,
        folder,
        spec,
        story,
        data_dir=None,
        *,
        character_field_repair=None,
        plot_contract_check=None,
        structure_max_output_tokens=None,
    ):
        self.client, self.folder, self.spec, self.story = client, folder, spec, story
        self.data_dir = data_dir
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "run.json"
        configuration = json.loads(json.dumps(asdict(spec)))
        fixture = signature(story)
        self.record = (
            json.loads(self.path.read_text("utf-8"))
            if self.path.exists()
            else {
                "schema": 1,
                "configuration": configuration,
                "fixture_sha256": fixture,
                "original_brief": story["brief"],
                "scope": "连续开篇十章，不能作为整书通过"
                if spec.stage == "ten"
                else "同原创简报、各模式独立36章",
                "started": time(),
                "operations": {},
                "workflows": {},
                "observations": [],
                "interventions": [],
                "engineering": "unverified",
                "literary_quality": "unverified",
                "benefit": "unverified",
                "user_caps": {"openai_shared_USD": 20, "deepseek_CNY": 80},
            }
        )
        if self.record["configuration"] != configuration or self.record["fixture_sha256"] != fixture:
            raise ValueError("Frozen fixture or evaluation configuration changed; use a new run directory")
        # Keep legacy RunSpec/configuration hashes intact. Missing options on old
        # runs retain disabled checks and the former 8192-token structure limit.
        defaults = {
            "character_field_repair": False,
            "plot_contract_check": False,
            "structure_max_output_tokens": 8192,
        }
        explicit = {
            "character_field_repair": character_field_repair,
            "plot_contract_check": plot_contract_check,
            "structure_max_output_tokens": structure_max_output_tokens,
        }

        def valid_option(key, value):
            if key == "structure_max_output_tokens":
                return type(value) is int and 64 <= value <= 16384
            return type(value) is bool

        saved = self.record.get("workflow_options", {})
        if (
            not isinstance(saved, dict)
            or set(saved) - set(defaults)
            or any(not valid_option(k, v) for k, v in saved.items())
            or any(v is not None and not valid_option(k, v) for k, v in explicit.items())
        ):
            raise ValueError("Invalid frozen workflow options")
        options = {**defaults, **saved}
        if self.path.exists():
            for key, value in explicit.items():
                if value is not None and value != options[key]:
                    policy = (
                        "structure output limit" if key == "structure_max_output_tokens" else "repair policy"
                    )
                    raise ValueError(f"Frozen {policy} changed; use a new run directory")
        else:
            options = {k: defaults[k] if v is None else v for k, v in explicit.items()}
            self.record["workflow_options"] = options
        self.character_field_repair = options["character_field_repair"]
        self.plot_contract_check = options["plot_contract_check"]
        self.structure_max_output_tokens = options["structure_max_output_tokens"]

    def save(self):
        write_json(self.path, self.record)

    def request(self, method, path, **kwargs):
        response = self.client.request(method, "/api" + path, **kwargs)
        response.raise_for_status()
        return response

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs).json()

    def create_novel(self):
        previous = self.record["operations"].get("novel")
        if previous:
            if previous["status"] != "done":
                raise RuntimeError("Novel creation outcome unknown; inspect and reconcile before resuming")
            return previous["result"]
        self.record["operations"]["novel"] = {"status": "dispatching", "started": time()}
        self.save()
        novel = self.request(
            "POST",
            "/novels",
            json={
                "title": f"长篇质量验证 · {self.story['title']} · {self.spec.stage} · {self.folder.name[-6:]}",
                "brief": self.story["brief"],
            },
        ).json()
        self.record["operations"]["novel"].update(status="done", result=novel, finished=time())
        self.save()
        return novel

    def dispatch(self, mode, novel):
        workflows = self.record["workflows"]
        if mode not in workflows:
            payload = workflow_payload(
                self.spec,
                self.story,
                novel["id"],
                mode,
                character_field_repair=self.character_field_repair,
                plot_contract_check=self.plot_contract_check,
                structure_max_output_tokens=self.structure_max_output_tokens,
            )
            # No source branch/prose is inherited. Both modes start from the same brief.
            payload["client_request_id"] = uuid4().hex
            workflows[mode] = {"payload": payload, "payload_sha256": signature(payload), "started": time()}
            self.save()
        entry = workflows[mode]
        if self.spec.stage == "ten" and entry["payload"].get("completion_scope") != "segment":
            raise ValueError(
                "Legacy ten-chapter payload used whole-book completion; preserve it for read-only observation and start an explicit segment run"
            )
        if signature(entry["payload"]) != entry["payload_sha256"]:
            raise ValueError("Frozen workflow payload is damaged")
        if entry["payload"].get("character_field_repair", False) is not self.character_field_repair:
            raise ValueError("Frozen payload differs from the evaluation repair policy")
        if entry["payload"].get("plot_contract_check", False) is not self.plot_contract_check:
            raise ValueError("Frozen payload differs from the evaluation plot-check policy")
        structure_limit = entry["payload"].get("structure_max_output_tokens", 8192)
        if type(structure_limit) is not int or structure_limit != self.structure_max_output_tokens:
            raise ValueError("Frozen payload differs from the evaluation structure output limit")
        if "id" not in entry:
            task = self.request("POST", "/workflows", json=entry["payload"]).json()
            entry.update(id=task["id"], branch_id=task["branch_id"])
            self.save()
            print(json.dumps({"mode": mode, "task": task["id"], "branch": task["branch_id"]}), flush=True)
        return entry["id"]

    def observe(self, task_id):
        """Read an existing task only; never resumes/retries or adopts a waiting artifact."""
        while True:
            task = self.get(f"/tasks/{task_id}")
            if task["kind"] != "workflow":
                raise ValueError("Only workflow tasks can be observed by the long-form evaluator")
            identity = (task["novel_id"], task["branch_id"], signature(task["payload"]))
            entry = self.record["workflows"].setdefault(
                "observed:" + task_id, {"id": task_id, "observed_only": True}
            )
            if entry.get("identity") and entry["identity"] != list(identity):
                raise ValueError("Observed task identity/payload changed")
            entry["identity"] = list(identity)
            entry.update(status=task["status"], error=task.get("error", ""), observed=time())
            progress = {
                key: task["result"].get(key)
                for key in (
                    "next_chapter",
                    "repair_round",
                    "waiting_structure",
                    "waiting_artifact",
                    "production_complete",
                )
            }
            marker = {"task_id": task_id, "status": task["status"], **progress}
            if not self.record["observations"] or self.record["observations"][-1]["progress"] != marker:
                self.record["observations"].append({"at": time(), "progress": marker})
                self.save()
                print(json.dumps(marker), flush=True)
            if task["status"] not in ACTIVE:
                self.capture(task)
                return task
            self.save()
            sleep(2)

    def capture(self, task):
        branch = task["branch_id"]
        directory = self.folder / task["id"] / ("snapshot-" + uuid4().hex)
        directory.mkdir(parents=True)
        before = self.get(f"/branches/{branch}/context")["branch"]["story_revision"]
        chapters = [self.get(f"/chapters/{row['id']}") for row in self.get(f"/branches/{branch}/chapters")]
        graph = self.get(f"/branches/{branch}/story-graph")
        scenes = {
            row["id"]: self.get(f"/branches/{branch}/chapters/{row['id']}/scene-times") for row in chapters
        }
        histories = {row["id"]: self.get(f"/chapters/{row['id']}/history") for row in chapters}
        artifacts = {
            row["id"]: self.get("/artifacts", params={"branch_id": branch, "chapter_id": row["id"]})
            for row in chapters
        }
        facts = self.get(f"/branches/{branch}/facts")
        transcript = self.get(f"/tasks/{task['id']}/transcript")
        completion = self.get(f"/branches/{branch}/completion")
        exported = self.request("GET", f"/branches/{branch}/export", params={"format": "md"}).text
        (directory / "manuscript.md").write_text(exported, encoding="utf-8", newline="\n")
        after = self.get(f"/branches/{branch}/context")["branch"]["story_revision"]
        budget = self.get("/config")["budget"]
        report = {
            "task": task,
            "chapters": chapters,
            "graph": graph,
            "scene_times": scenes,
            "revision_histories": histories,
            "chapter_artifacts": artifacts,
            "facts": facts,
            "transcript": transcript,
            "artifact_coverage": "每章API最近50项产物，运行步骤最近20项；不宣称包含完整数据库历史。正式整书交付仍需一致性备份及evidence_export。",
            "completion": completion,
            "budget": budget,
            "capture_consistent": before == after,
            "captured_story_revision": after,
            "model_completion": task["status"] == "production_complete",
            "segment_completion": task["status"] == "segment_complete",
            "completion_scope": task["payload"].get("completion_scope", "whole_book"),
            "literary_quality": "unverified",
            "benefit": "unverified",
            "scope": self.record["scope"],
            "interventions": deepcopy(self.record["interventions"]),
        }
        if self.data_dir:
            report["usage"] = read_usage(self.data_dir, self.lineage(task))
        write_json(directory / "evidence.json", report)
        self.record["budget_after"] = budget
        capture = {
            "status": task["status"],
            "capture_consistent": before == after,
            "chapter_count": len(chapters),
            "completion": completion,
            "evidence": str(directory / "evidence.json"),
        }
        self.record.setdefault("captures", {})[task["id"]] = capture
        self.record.setdefault("capture_history", []).append({"task_id": task["id"], "at": time(), **capture})
        self.save()

    def lineage(self, task):
        result, seen, current = [], set(), task
        while current:
            if current["id"] in seen or current["branch_id"] != task["branch_id"]:
                raise ValueError("Invalid workflow continuation lineage")
            seen.add(current["id"])
            result.append(current["id"])
            parent = current["result"].get("continued_from")
            current = self.get(f"/tasks/{parent}") if parent else None
        return result

    def run(self):
        config = self.get("/config")
        account = next((row for row in config["accounts"] if row["id"] == self.spec.account), None)
        if not account or not account["configured"] or not account["priced"]:
            raise ValueError("Evaluation account must be configured and priced")
        if self.record.get("selected_account", account) != account:
            raise ValueError("Selected model configuration changed; observation remains available")
        self.record["selected_account"] = account
        self.record.setdefault("budget_before", config["budget"])
        self.save()
        novel = self.create_novel()
        for mode in self.spec.modes:
            task = self.observe(self.dispatch(mode, novel))
            expected_terminal = "segment_complete" if self.spec.stage == "ten" else "production_complete"
            if task["status"] != expected_terminal:
                self.record["stopped"] = {
                    "task_id": task["id"],
                    "status": task["status"],
                    "error": task.get("error", ""),
                    "policy": "停止后续模式，不自动重试、恢复或采纳；等待明确处理",
                }
                self.save()
                return
        self.record["finished"] = time()
        # Model completion is distinct from stability, literary quality and benefit.
        self.save()


def structure_output_limit(value):
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Structure output limit must be an integer from 64 to 16384"
        ) from None
    if not 64 <= limit <= 16384:
        raise argparse.ArgumentTypeError("Structure output limit must be an integer from 64 to 16384")
    return limit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="显式授权启动本次付费工作流；只读观察不需此参数"
    )
    parser.add_argument("--stage", choices=("ten", "books"), default="ten")
    parser.add_argument("--modes", default=None, help="assisted,automatic；ten默认assisted，books默认两模式")
    parser.add_argument("--account", default="deepseek")
    parser.add_argument("--min-chars", type=int, default=2500)
    parser.add_argument("--max-chars", type=int, default=3500)
    parser.add_argument("--run-limit", type=float, default=10)
    parser.add_argument(
        "--character-field-repair",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="仅新运行显式开启AI新增人物字段最多两轮内容修订；旧运行缺省关闭，续跑继承冻结开关",
    )
    parser.add_argument(
        "--plot-contract-check",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="独立解析本章情节并逐项对照正文；增加模型调用，旧运行缺省关闭且不追溯开启",
    )
    parser.add_argument(
        "--structure-max-output-tokens",
        type=structure_output_limit,
        default=None,
        help=(
            "首轮结构维护输出上限，64..16384整数；新运行默认8192，续跑省略时继承冻结值；"
            "已知截断有限修正最高16384，仍受原预算保护"
        ),
    )
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--observe-task", help="只观察既有任务，不启动、恢复、重试或采纳")
    parser.add_argument("--intervention-note", help="人工处理说明；仅写入本地评测记录，不触发修改")
    args = parser.parse_args()
    if not args.execute and not args.observe_task:
        parser.error("启动需显式--execute；既有任务只读观察用--observe-task")
    settings = Settings.load()
    root = (settings.data_dir / "acceptance/longform-quality").resolve()
    folder = args.resume.resolve() if args.resume else root / uuid4().hex
    if root not in folder.parents or not 1 <= args.port <= 65535:
        raise ValueError("Use a private acceptance run directory and a valid loopback port")
    if args.resume and (folder / "run.json").is_file():
        spec = RunSpec(
            **{
                **json.loads((folder / "run.json").read_text("utf-8"))["configuration"],
                "modes": tuple(
                    json.loads((folder / "run.json").read_text("utf-8"))["configuration"]["modes"]
                ),
            }
        )
    else:
        modes = (
            args.modes.split(",")
            if args.modes
            else (["assisted"] if args.stage == "ten" else ["assisted", "automatic"])
        )
        spec = RunSpec(args.stage, args.account, tuple(modes), args.min_chars, args.max_chars, args.run_limit)
    story = json.loads((ROOT / "fixtures/original-story.json").read_text("utf-8"))
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", trust_env=False, timeout=30) as client:
        session = client.get("/api/session").raise_for_status().json()
        client.headers["X-Ainovel-CSRF"] = session["csrf"]
        runner = LongformEvaluation(
            client,
            folder,
            spec,
            story,
            settings.data_dir,
            character_field_repair=args.character_field_repair,
            plot_contract_check=args.plot_contract_check,
            structure_max_output_tokens=args.structure_max_output_tokens,
        )
        runner.record.setdefault(
            "source_commit",
            subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
        )
        runner.record.setdefault("harness_sha256", hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        if args.intervention_note:
            runner.record["interventions"].append({"at": time(), "note": args.intervention_note})
        runner.save()
        print(
            json.dumps({"record": str(runner.path), "stage": spec.stage, "chapters": spec.chapters}),
            flush=True,
        )
        try:
            runner.observe(args.observe_task) if args.observe_task else runner.run()
        finally:
            runner.record["last_observed"] = time()
            runner.save()


if __name__ == "__main__":
    main()
