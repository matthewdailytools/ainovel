"""Opt-in paired evaluations through the running API; never adopts generated prose."""

import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
from time import sleep, time
from uuid import uuid4

import httpx

from .settings import ROOT, Settings


CASES = [
    (
        "开篇",
        "旧钟提前响了三声。守钟人没有校准它，先把昨夜的值班表翻到缺页处。",
        "沈砚清晨核对潮尺，发现昨夜记录缺一行。他保留原表，决定找值夜人核实。",
        ["不确认缺行原因", "不销毁原表"],
    ),
    (
        "交涉",
        "库房只肯借半捆绳，要求留下归还时刻。来人写了字据，又问余下半捆去了哪里。",
        "沈砚向货商换铜环，对方临时加价，双方未成交。他离开后找熟人借旧尺。",
        ["交易失败", "无战斗或肢体冲突"],
    ),
    (
        "调查",
        "杯底积着细沙，窗台却干净。她把两处分别记下，没有立刻指认昨夜开窗的人。",
        "宁禾检查闸门外露磨损，因责任争议拿不到维修记录。她只保留测量结果，另找见证者。",
        ["不修闸门", "拿不到维修记录", "不认定责任人"],
    ),
    (
        "打斗",
        "对手压住窄桥中线，他便让出半步，借桥柱挡住长杆。右腕发麻后，他不再抢攻，只等退路空出。",
        "普通护堤工沈砚被一名持短棍的看守阻拦，利用工具车拉开距离，右臂擦伤，最终退回街口。",
        ["没有超自然能力", "右臂擦伤保留", "不击败看守"],
    ),
    (
        "心理",
        "她把已经写好的名字划去，又怕划痕让人认出，索性换了张纸，只留下时间和地点。",
        "宁禾担心证词连累同伴，反复整理要问的问题，最后决定只公开测量值，暂不写同伴姓名。",
        ["不公开同伴姓名", "不凭空得知他人想法"],
    ),
    (
        "环境",
        "雨从棚布破口落进秤盘。伙计挪了三次砝码，才发现货物没变，是盘底积了水。",
        "沈砚在潮湿旧库房检查旧尺，斜光和渗水影响读数。他换到门口复测，仍不能确定误差来源。",
        ["误差来源未知", "环境细节影响具体操作"],
    ),
    (
        "日常关系",
        "妹妹把热汤推给他，却把欠账本压在碗下。他喝完才翻开账本，她也不催，只把灯拨亮。",
        "沈砚向老友借旧尺，老友记挂过去拖欠的工钱，边收拾饭桌边提出归还日期。沈砚答应，二人关系仍有芥蒂。",
        ["旧尺借出", "明确归还约定", "关系未完全和解"],
    ),
]


def write_json(path, value):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows readers/virus scanners can briefly deny replacement of an open file.
    # Retry only the already-written local journal, never a network/model request.
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            sleep(0.05)


def blind_pack(folder, results):
    """Keep the decoding key separate from anonymous author-facing texts."""
    reviews = folder / "blind-review"
    reviews.mkdir(parents=True, exist_ok=True)
    randomizer = random.Random(hashlib.sha256(folder.name.encode()).hexdigest())
    answers = []
    for index, row in enumerate(results, 1):
        pair = [("baseline", row["baseline"]), ("reference", row["reference"])]
        randomizer.shuffle(pair)
        text = (
            f"# 样例{index:02}：{row['case']}\n\n情节：{row['plot']}\n\n固定条件：{'；'.join(row['fixed'])}\n"
        )
        for label, (origin, artifact) in zip(("A", "B"), pair, strict=True):
            text += f"\n## 稿件{label}\n\n{artifact['payload']['text']}\n"
            answers.append({"sample": index, "label": label, "origin": origin, "artifact_id": artifact["id"]})
        text += "\n## 作者评价\n\n- 固定条件及因果：\n- 表达、情绪、声音、阅读体验：\n- 更愿采用哪稿及具体段落依据：\n- 额外核查与返修投入：\n"
        (reviews / f"样例{index:02}.md").write_text(text, encoding="utf-8")
    write_json(folder / "answer-key.json", answers)
    (reviews / "README.md").write_text(
        "# 匿名对照审阅\n\nA/B顺序分别随机化。未完成的配对不进入盲审。模型检查仅辅助；文学质量与实际收益等待作者评价。准备、查找、提示调整、等待、核查、返修及保存总投入尚待记录，不由调用耗时推断。解码索引在本目录之外。\n",
        encoding="utf-8",
    )


class Evaluation:
    def __init__(self, client, folder, account, count):
        self.client, self.folder, self.account = client, folder, account
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "run.json"
        self.record = (
            json.loads(self.path.read_text("utf-8"))
            if self.path.exists()
            else {
                "account": account,
                "count": count,
                "started": time(),
                "operations": {},
                "tasks": {},
                "results": [],
                "human_quality": "unverified",
                "scope": "七类首次配对，不代表整书验收",
            }
        )
        fixture_hash = hashlib.sha256(json.dumps(CASES, ensure_ascii=False).encode()).hexdigest()
        if self.record.get("fixture_sha256", fixture_hash) != fixture_hash:
            raise ValueError("Evaluation fixture changed; resume would not be comparable")
        self.record["fixture_sha256"] = fixture_hash
        if self.record["account"] != account or self.record["count"] != count:
            raise ValueError("Resume configuration differs")

    def configure_cases(self, names=None, reference_first=False):
        selected = names or [case[0] for case in CASES[: self.record["count"]]]
        if (
            len(selected) != self.record["count"]
            or len(set(selected)) != len(selected)
            or any(name not in {case[0] for case in CASES} for name in selected)
        ):
            raise ValueError("Select distinct known cases matching count")
        settings = {"case_names": selected, "reference_first": reference_first}
        for key, value in settings.items():
            if key in self.record and self.record[key] != value:
                raise ValueError("Resume case selection or order changed")
        if (
            self.record["tasks"]
            and "case_names" not in self.record
            and (names is not None or reference_first)
        ):
            raise ValueError("Cannot change an already-started legacy evaluation order")
        self.record.update(settings)
        self.save()

    def save(self):
        write_json(self.path, self.record)

    def request(self, method, url, **kwargs):
        response = self.client.request(method, "/api" + url, **kwargs)
        response.raise_for_status()
        return response.json()

    def once(self, name, method, url, **kwargs):
        operations = self.record["operations"]
        if name in operations:
            if operations[name]["status"] != "done":
                raise RuntimeError(f"Operation outcome unknown: {name}; inspect before resuming")
            return operations[name]["result"]
        operations[name] = {"status": "dispatching", "started": time()}
        self.save()
        result = self.request(method, url, **kwargs)
        operations[name].update(status="done", result=result, finished=time())
        self.save()
        return result

    def task(self, name, url, payload):
        tasks = self.record["tasks"]
        if name not in tasks:
            tasks[name] = {"request_key": uuid4().hex, "started": time()}
            self.save()
        entry = tasks[name]
        if "id" not in entry:
            # A repeated task submission uses the same server-side idempotency key.
            row = self.request("POST", url, json={**payload, "client_request_id": entry["request_key"]})
            entry["id"] = row["id"]
            self.save()
            print(json.dumps({"step": name, "task": row["id"]}), flush=True)
        while True:
            row = self.request("GET", f"/tasks/{entry['id']}")
            entry.update(status=row["status"], error=row.get("error"), observed=time())
            self.save()
            if row["status"] not in ("queued", "running"):
                break
            sleep(2)
        if row["status"] != "succeeded":
            raise RuntimeError(f"Task {entry['id']} stopped with {row['status']}; no automatic paid retry")
        artifact = self.request("GET", f"/artifacts/{row['result']['artifact_id']}")
        write_json(self.folder / f"{name}.json", {"task": row, "artifact": artifact})
        return artifact

    def run(self):
        config = self.request("GET", "/config")
        selected = next(row for row in config["accounts"] if row["id"] == self.account)
        if not selected["configured"] or not selected["priced"]:
            raise ValueError("Selected evaluation account is not configured and priced")
        if self.record.get("selected_configuration", selected) != selected:
            raise ValueError("Model configuration changed; start a distinct evaluation run")
        self.record["selected_configuration"] = selected
        self.record.setdefault("budget_before", config["budget"])
        self.record.setdefault("configuration", config["accounts"])
        selected_names = self.record.get("case_names", [case[0] for case in CASES[: self.record["count"]]])
        for index, (kind, source, plot, fixed) in enumerate(CASES):
            if kind not in selected_names:
                continue
            prefix = f"case-{index + 1}"
            if any(row["case"] == kind for row in self.record["results"]):
                continue
            novel = self.once(
                prefix + "-novel",
                "POST",
                "/novels",
                json={
                    "title": f"质量验证 · {kind} · {self.folder.name[-6:]}",
                    "brief": "普通护堤工与调查者的原创短场景；不延续旧验收正文。",
                },
            )
            chapter = self.once(
                prefix + "-chapter", "POST", f"/branches/{novel['branch_id']}/chapters", json={"title": kind}
            )
            shared_plot = (
                plot + "只写本场景，约500—800字，第三人称限定视角、现代白话、动作有因果，不写后续章节。"
            )
            common = {"account": self.account, "budget_limit": 5}

            def baseline_task():
                return self.task(
                    prefix + "-baseline",
                    "/modules",
                    {
                        **common,
                        "action": "writing.expand_plot",
                        "novel_id": novel["id"],
                        "branch_id": novel["branch_id"],
                        "chapter_id": chapter["id"],
                        "base_revision": 0,
                        "instruction": shared_plot,
                        "fixed": fixed,
                    },
                )

            baseline = None if self.record.get("reference_first") else baseline_task()
            book = self.once(
                prefix + "-book",
                "POST",
                "/library/import",
                files={"file": (kind + "自拟参考.txt", source.encode(), "text/plain")},
            )
            unit = self.request("GET", f"/library/books/{book['id']}/units")[0]
            reference = self.once(
                prefix + "-session",
                "POST",
                "/reference-sessions",
                json={
                    "unit_id": unit["id"],
                    "start": 0,
                    "end": len(source.encode("utf-16-le")) // 2,
                    "quote": source,
                    "goal": f"学习{kind}场景中可迁移的叙事机制，明确观察与解释的区别",
                    "branch_id": novel["branch_id"],
                    "chapter_id": chapter["id"],
                },
            )
            base_url = f"/reference-sessions/{reference['id']}"
            analysis = self.task(
                prefix + "-analyze",
                base_url + "/actions",
                {**common, "base_revision": reference["revision"], "action": "analyze"},
            )
            cards = analysis["payload"]["cards"]
            if not cards:
                raise RuntimeError(
                    "No evidence-grounded cards; retain failed case, do not manufacture reference rules"
                )
            values = {
                key: reference["payload"][key]
                for key in (
                    "goal",
                    "cards",
                    "selected_ids",
                    "plot",
                    "fixed",
                    "application_plan",
                    "branch_id",
                    "chapter_id",
                )
            }
            values.update(
                cards=cards, selected_ids=[card["id"] for card in cards], plot=shared_plot, fixed=fixed
            )
            edited = self.once(
                prefix + "-rules", "PATCH", base_url, json={**values, "base_revision": reference["revision"]}
            )
            plan = self.task(
                prefix + "-plan",
                base_url + "/actions",
                {**common, "base_revision": edited["revision"], "action": "plan"},
            )
            if plan["payload"]["conflicts"]:
                raise RuntimeError("Application conflicts require review; no automatic override")
            values["application_plan"] = plan["payload"]["text"]
            confirmed = self.once(
                prefix + "-application",
                "PATCH",
                base_url,
                json={**values, "base_revision": edited["revision"]},
            )
            candidate = self.task(
                prefix + "-reference",
                base_url + "/actions",
                {**common, "base_revision": confirmed["revision"], "action": "draft"},
            )
            if self.record.get("reference_first"):
                baseline = baseline_task()
            saved = self.request("GET", f"/chapters/{chapter['id']}")
            if saved["revision"] != 0 or saved["content"]:
                raise RuntimeError("Evaluation target changed; stop rather than overwrite")
            self.record["results"].append(
                {
                    "case": kind,
                    "plot": shared_plot,
                    "fixed": fixed,
                    "baseline": baseline,
                    "reference": candidate,
                    "selection_policy": "评测运行选择全部有依据规则；不代表作者认可",
                    "finished": time(),
                }
            )
            self.record["budget_after"] = self.request("GET", "/config")["budget"]
            self.save()
            blind_pack(self.folder, self.record["results"])
        self.record["finished"] = time()
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--count", type=int, choices=range(1, 8), default=7)
    parser.add_argument("--account", default="deepseek")
    parser.add_argument("--cases", help="Comma-separated exact case names; overrides count")
    parser.add_argument("--reference-first", action="store_true")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    settings = Settings.load()
    root = (settings.data_dir / "acceptance" / "reference-quality").resolve()
    folder = args.resume.resolve() if args.resume else root / uuid4().hex
    if root not in folder.parents:
        raise ValueError("Evaluation outputs must stay inside the private acceptance directory")
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", trust_env=False, timeout=30) as client:
        session = client.get("/api/session").raise_for_status().json()
        client.headers["X-Ainovel-CSRF"] = session["csrf"]
        names = args.cases.split(",") if args.cases else None
        runner = Evaluation(client, folder, args.account, len(names) if names else args.count)
        runner.configure_cases(names, args.reference_first)
        runner.record.setdefault(
            "source_commit",
            subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
        )
        runner.record.setdefault("harness_sha256", hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        runner.save()
        print(json.dumps({"record": str(runner.path)}), flush=True)
        try:
            runner.run()
        finally:
            runner.record["last_observed"] = time()
            try:
                runner.record["budget_after"] = runner.request("GET", "/config")["budget"]
            except httpx.HTTPError as error:
                runner.record["budget_observation_error"] = type(error).__name__
            finally:
                runner.save()


if __name__ == "__main__":
    main()
