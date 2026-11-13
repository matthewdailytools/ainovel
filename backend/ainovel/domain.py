import hashlib
import json
from time import time

from .db import DomainError, dumps, uid
from .genre_catalog import assemble_profile
from .models import Fact


def same_task_payload(left, right):
    # Added in 0.2: omitted and null both mean an older caller supplied no seen basis.
    # A supplied basis object (including its explicit null reader ID) remains distinct.
    def normalized(value):
        return {k: v for k, v in value.items() if k != "genre_profile_basis" or v is not None}

    return normalized(left) == normalized(right)


def utf16_slice(text, start, end):
    encoded = text.encode("utf-16-le")
    if start < 0 or end < start or end * 2 > len(encoded):
        raise DomainError("selection", "选区超出正文范围。")
    try:
        return (
            encoded[: start * 2].decode("utf-16-le"),
            encoded[start * 2 : end * 2].decode("utf-16-le"),
            encoded[end * 2 :].decode("utf-16-le"),
        )
    except UnicodeDecodeError as error:
        raise DomainError("selection", "选区不能拆开表情或扩展汉字。") from error


def char_count(text):
    return sum(not c.isspace() for c in text)


class Domain:
    def __init__(self, db):
        self.db = db

    def create_novel(self, title, brief=""):
        novel_id, branch_id = uid(), uid()
        with self.db.connect(True) as db:
            db.execute("INSERT INTO novels VALUES(?,?,?,?)", (novel_id, title, brief, time()))
            db.execute(
                "INSERT INTO branches(id,novel_id,name,created) VALUES(?,?,?,?)",
                (branch_id, novel_id, "作者工作稿", time()),
            )
        return {"id": novel_id, "branch_id": branch_id}

    def branch(self, branch_id, novel_id=None):
        branch = self.db.one("SELECT * FROM branches WHERE id=?", (branch_id,))
        if novel_id and branch["novel_id"] != novel_id:
            raise DomainError("ownership", "工作版本不属于目标作品。", 403)
        return branch

    def chapter(self, chapter_id, branch_id=None):
        chapter = self.db.one("SELECT * FROM chapters WHERE id=? AND archived=0", (chapter_id,))
        if branch_id and chapter["branch_id"] != branch_id:
            raise DomainError("ownership", "章节不属于目标工作版本。", 403)
        return chapter

    def create_chapter(self, branch_id, title, position=None, expected_story_revision=None):
        self.branch(branch_id)
        chapter_id = uid()
        with self.db.connect(True) as db:
            if (
                expected_story_revision is not None
                and db.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
                != expected_story_revision
            ):
                raise DomainError("story_changed", "新建计划章节前故事已改变，停止推进。", 409)
            next_position = db.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM chapters WHERE branch_id=?", (branch_id,)
            ).fetchone()[0]
            position = position or next_position
            if db.execute(
                "SELECT id FROM chapters WHERE branch_id=? AND position=? AND archived=0",
                (branch_id, position),
            ).fetchone():
                raise DomainError("structure_changed", "计划章节位置已被占用，请刷新目录。", 409)
            db.execute(
                "INSERT INTO chapters(id,branch_id,position,title) VALUES(?,?,?,?)",
                (chapter_id, branch_id, position, title),
            )
            db.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?,?)", (uid(), chapter_id, 0, "", "新建空稿", time())
            )
            db.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
            story_revision = db.execute(
                "SELECT story_revision FROM branches WHERE id=?", (branch_id,)
            ).fetchone()[0]
        return {**self.chapter(chapter_id), "story_revision": story_revision}

    @staticmethod
    def revise_tx(db, chapter, content, cause):
        revision = chapter["revision"] + 1
        db.execute(
            "UPDATE chapters SET revision=?,content=?,stale=0 WHERE id=?", (revision, content, chapter["id"])
        )
        db.execute(
            "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
            (uid(), chapter["id"], revision, content, cause, time()),
        )
        db.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (chapter["branch_id"],))
        db.execute(
            "UPDATE facts SET stale=1 WHERE chapter_id IN (SELECT id FROM chapters WHERE branch_id=? AND position>=?)",
            (chapter["branch_id"], chapter["position"]),
        )
        db.execute(
            "UPDATE chapters SET stale=1 WHERE branch_id=? AND position>? AND content!=''",
            (chapter["branch_id"], chapter["position"]),
        )
        return revision

    def save(self, chapter_id, base_revision, content, cause="作者编辑"):
        with self.db.connect(True) as db:
            row = db.execute("SELECT * FROM chapters WHERE id=? AND archived=0", (chapter_id,)).fetchone()
            if not row:
                raise DomainError("not_found", "章节不存在。", 404)
            if row["revision"] != base_revision:
                raise DomainError("revision_conflict", "正文已有新版本，请比较后重新保存。", 409)
            if row["content"] != content:
                self.revise_tx(db, dict(row), content, cause)
        return self.chapter(chapter_id)

    def restore(self, chapter_id, base_revision, revision_id):
        revision = self.db.one(
            "SELECT * FROM revisions WHERE id=? AND chapter_id=?", (revision_id, chapter_id)
        )
        return self.save(chapter_id, base_revision, revision["content"], "恢复历史为新版本")

    def stage(self, request, kind, payload, basis, task_id=None, attempt=None):
        artifact_id = uid()
        with self.db.connect(True) as db:
            if attempt:
                self.db.check_attempt(db, task_id, attempt)
            db.execute(
                "INSERT INTO artifacts(id,novel_id,branch_id,chapter_id,kind,base_revision,payload,basis,task_id,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    request["novel_id"],
                    request["branch_id"],
                    request.get("chapter_id"),
                    kind,
                    request.get("base_revision"),
                    dumps(payload),
                    dumps(basis),
                    task_id,
                    time(),
                ),
            )
        return self.artifact(artifact_id)

    def artifact(self, artifact_id):
        artifact = self.db.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        for key in ("payload", "basis"):
            artifact[key] = json.loads(artifact[key])
        artifact["candidate_revision"] = artifact["payload"].get("_candidate_revision", 0)
        return artifact

    @staticmethod
    def add_fact_tx(db, branch_id, chapter_id, revision, fact, text):
        fact = Fact.model_validate(fact)
        start = text.find(fact.evidence)
        if start < 0:
            raise DomainError("evidence", "事实依据不在指定正文版本中，未写入故事记录。")
        start16 = len(text[:start].encode("utf-16-le")) // 2
        end16 = start16 + len(fact.evidence.encode("utf-16-le")) // 2
        db.execute(
            "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (
                uid(),
                branch_id,
                chapter_id,
                revision,
                fact.subject,
                fact.kind,
                fact.statement,
                fact.truth,
                fact.story_time,
                fact.evidence,
                start16,
                end16,
            ),
        )

    def accept(
        self,
        artifact_id,
        key,
        require_review=False,
        attempt=None,
        candidate_revision=None,
        workflow_result=None,
    ):
        with self.db.connect(True) as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not row:
                raise DomainError("not_found", "候选不存在。", 404)
            artifact = dict(row)
            artifact["payload"], artifact["basis"] = json.loads(row["payload"]), json.loads(row["basis"])
            prior = db.execute("SELECT * FROM receipts WHERE key=?", (key,)).fetchone()
            if prior:
                if prior["artifact_id"] != artifact_id:
                    raise DomainError("idempotency", "采纳标识已用于另一个候选。", 409)
                return json.loads(prior["result"])
            previous = db.execute(
                "SELECT result FROM receipts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            if previous:
                return json.loads(previous[0])
            current = db.execute("SELECT status FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if current[0] != "candidate":
                raise DomainError("candidate_state", "该产物已被拒绝或采纳。", 409)
            if attempt:
                self.db.check_attempt(db, artifact["task_id"], attempt)
            payload = artifact["payload"]
            if candidate_revision is not None and candidate_revision != payload.get("_candidate_revision", 0):
                raise DomainError("candidate_conflict", "候选已有新修订，请重新比较后采纳；正文未改动。", 409)
            if "story_revision" in artifact["basis"]:
                story_revision = db.execute(
                    "SELECT story_revision FROM branches WHERE id=?", (artifact["branch_id"],)
                ).fetchone()[0]
                if story_revision != artifact["basis"]["story_revision"]:
                    raise DomainError("story_changed", "生成所依据的故事版本已改变，需重新审阅生成。", 409)
            if require_review and not payload.get("review_passed"):
                raise DomainError("review_required", "候选尚未通过本轮检查，不能自动采纳。", 409)
            result = {"artifact_id": artifact_id, "chapter_id": artifact["chapter_id"]}
            if artifact["kind"] in ("text_patch", "chapter_draft"):
                chapter = dict(
                    db.execute("SELECT * FROM chapters WHERE id=?", (artifact["chapter_id"],)).fetchone()
                )
                if chapter["revision"] != artifact["base_revision"] or chapter["archived"]:
                    raise DomainError("revision_conflict", "生成后正文已改变，请重新对照；原稿未改动。", 409)
                if artifact["kind"] == "text_patch":
                    before, selected, after = utf16_slice(
                        chapter["content"], payload["start"], payload["end"]
                    )
                    if selected != payload["original"]:
                        raise DomainError("revision_conflict", "选区原文不匹配。", 409)
                    content = before + payload["text"] + after
                else:
                    content = payload["text"]
                revision = self.revise_tx(db, chapter, content, "采纳候选")
                memory = payload.get("memory", {})
                for fact in memory.get("facts", []):
                    self.add_fact_tx(db, artifact["branch_id"], chapter["id"], revision, fact, content)
                result["revision"] = revision
            elif artifact["kind"] == "memory":
                chapter = dict(
                    db.execute("SELECT * FROM chapters WHERE id=?", (artifact["chapter_id"],)).fetchone()
                )
                if chapter["revision"] != artifact["base_revision"]:
                    raise DomainError("revision_conflict", "事实提取依据已过期。", 409)
                db.execute(
                    "UPDATE facts SET stale=1 WHERE chapter_id=? AND source_revision=?",
                    (chapter["id"], chapter["revision"]),
                )
                for fact in payload.get("facts", []):
                    self.add_fact_tx(
                        db,
                        artifact["branch_id"],
                        chapter["id"],
                        chapter["revision"],
                        fact,
                        chapter["content"],
                    )
                db.execute(
                    "UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (artifact["branch_id"],)
                )
            elif artifact["kind"] in ("world", "characters", "outline", "intent", "reader"):
                db.execute(
                    "UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (artifact["branch_id"],)
                )
            elif artifact["kind"] == "review" and artifact["chapter_id"]:
                chapter = db.execute(
                    "SELECT * FROM chapters WHERE id=?", (artifact["chapter_id"],)
                ).fetchone()
                if chapter["revision"] != artifact["base_revision"]:
                    raise DomainError("revision_conflict", "审稿依据已过期。", 409)
                if any(f["severity"] == "blocker" for f in payload.get("findings", [])) or any(
                    c["result"] != "pass" for c in payload.get("checks", [])
                ):
                    raise DomainError(
                        "review_required", "报告存在未解决阻断或未通过条件，不能解除待复检状态。", 409
                    )
                db.execute("UPDATE chapters SET stale=0 WHERE id=?", (artifact["chapter_id"],))
                db.execute(
                    "UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (artifact["branch_id"],)
                )
            db.execute("UPDATE artifacts SET status='accepted' WHERE id=?", (artifact_id,))
            db.execute("INSERT INTO receipts VALUES(?,?,?)", (key, artifact_id, dumps(result)))
            if artifact["task_id"]:
                self.db.event(db, artifact["task_id"], "accepted", result)
            if workflow_result is not None:
                if not (require_review and attempt and artifact["kind"] == "chapter_draft"):
                    raise DomainError("workflow_progress", "自动进度必须与已验证的执行尝试一起保存。", 409)
                progress = {
                    **workflow_result,
                    "latest_artifact": artifact_id,
                    "next_chapter": chapter["position"] + 1,
                    "expected_story_revision": db.execute(
                        "SELECT story_revision FROM branches WHERE id=?", (artifact["branch_id"],)
                    ).fetchone()[0],
                }
                self.db.check_attempt(db, artifact["task_id"], attempt)
                db.execute(
                    "UPDATE tasks SET result=?,updated=? WHERE id=? AND attempt=?",
                    (dumps(progress), time(), artifact["task_id"], attempt),
                )
                self.db.event(
                    db,
                    artifact["task_id"],
                    "checkpoint",
                    {"status": "running", "atomic_adoption": True, **progress},
                )
                workflow_result.update(progress)
            return result

    def genre_profile(self, branch_id, definitions=None):
        branch = self.branch(branch_id)
        if definitions is None:
            rows = self.db.all(
                "SELECT id,kind,payload FROM artifacts WHERE branch_id=? AND kind='reader' "
                "AND chapter_id IS NULL AND status='accepted' ORDER BY created DESC,id DESC LIMIT 1",
                (branch_id,),
            )
            definitions = [{**r, "payload": json.loads(r["payload"])} for r in rows]
        reader = next((row for row in definitions if row["kind"] == "reader"), None)
        return {
            **assemble_profile(reader["payload"] if reader else None),
            "source_artifact_id": reader["id"] if reader else None,
            "source_story_revision": branch["story_revision"],
        }

    def check_genre_snapshot(self, branch_id, snapshot):
        if not snapshot or snapshot.get("legacy_unconfigured"):
            return
        readers = self.db.all(
            "SELECT id FROM artifacts WHERE branch_id=? AND kind='reader' "
            "AND chapter_id IS NULL AND status='accepted' ORDER BY created DESC,id DESC LIMIT 1",
            (branch_id,),
        )
        if (readers[0]["id"] if readers else None) != snapshot.get("source_artifact_id"):
            raise DomainError(
                "genre_profile_changed",
                "本任务的频道题材设定已改变，原快照保留；请按新配置创建独立批次，不混用两套设定。",
                409,
            )

    @staticmethod
    def check_genre_basis(profile, basis):
        if basis is not None and (
            basis["source_artifact_id"] != profile["source_artifact_id"]
            or basis["catalog_version"] != profile["catalog_version"]
        ):
            raise DomainError(
                "genre_profile_changed", "频道题材配置已在其他窗口改变，请刷新并确认后再生成。", 409
            )

    def context(self, branch_id, chapter_id=None):
        branch = self.branch(branch_id)
        chapter = self.chapter(chapter_id, branch_id) if chapter_id else None
        position = chapter["position"] if chapter else 1_000_000
        facts = self.db.all(
            """SELECT f.*,c.position,c.title FROM facts f JOIN chapters c ON c.id=f.chapter_id
            WHERE f.branch_id=? AND c.position<? AND c.archived=0 AND c.stale=0 AND f.stale=0 ORDER BY c.position DESC LIMIT 100""",
            (branch_id, position),
        )
        previous = self.db.all(
            "SELECT id,title,position,revision,content,stale FROM chapters WHERE branch_id=? AND position<? AND archived=0 AND stale=0 ORDER BY position DESC LIMIT 2",
            (branch_id, position),
        )
        definitions = self.db.all(
            """SELECT id,kind,payload FROM (SELECT id,kind,payload,created,
            ROW_NUMBER() OVER(PARTITION BY kind ORDER BY created DESC,id DESC) AS rank FROM artifacts
            WHERE branch_id=? AND chapter_id IS NULL AND status='accepted'
            AND kind IN ('intent','reader','world','characters','outline')) WHERE rank=1 ORDER BY created DESC""",
            (branch_id,),
        )
        definitions = [{**row, "payload": json.loads(row["payload"])} for row in definitions]
        return {
            "novel": self.db.one("SELECT * FROM novels WHERE id=?", (branch["novel_id"],)),
            "branch": branch,
            "chapter": chapter,
            "facts": facts,
            "recent": list(reversed(previous)),
            "definitions": definitions,
            "genre_profile": self.genre_profile(branch_id, definitions),
            "coverage": "每类最新采用设定、目标章节之前最近100条有效事实及最近两章；更早原文需主动查询，未知故事时间保留未知。",
        }

    def find_request(self, kind, payload):
        key = payload.get("client_request_id")
        if not key:
            return None
        rows = self.db.all("SELECT task_id FROM task_requests WHERE key=?", (key,))
        if rows:
            task = self.task(rows[0]["task_id"])
            if task["kind"] != kind or not same_task_payload(task["payload"], payload):
                raise DomainError("idempotency", "该提交标识已用于不同输入，未创建新任务。", 409)
            return task

    def queue(self, kind, payload, branch_id=None):
        previous = self.find_request(kind, payload)
        if previous:
            return previous
        branch_id = branch_id or payload["branch_id"]
        self.branch(branch_id, payload["novel_id"])
        if payload.get("chapter_id"):
            chapter = self.chapter(payload["chapter_id"], branch_id)
            if chapter["revision"] != payload.get("base_revision"):
                raise DomainError("revision_conflict", "请先保存当前正文并重新选择目标版本。", 409)
        task_id = uid()
        profile = self.genre_profile(branch_id)
        initial = {"genre_profile_snapshot": profile}
        if kind == "workflow":
            initial["expected_story_revision"] = profile["source_story_revision"]
        with self.db.connect(True) as db:
            key = payload.get("client_request_id")
            if key:
                prior = db.execute("SELECT task_id FROM task_requests WHERE key=?", (key,)).fetchone()
                if prior:
                    previous = self.task(prior[0])
                    if previous["kind"] != kind or not same_task_payload(previous["payload"], payload):
                        raise DomainError("idempotency", "提交标识已用于不同输入。", 409)
                    return previous
            revision = db.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()[
                0
            ]
            reader = db.execute(
                "SELECT id FROM artifacts WHERE branch_id=? AND kind='reader' "
                "AND chapter_id IS NULL AND status='accepted' ORDER BY created DESC,id DESC LIMIT 1",
                (branch_id,),
            ).fetchone()
            if (reader[0] if reader else None) != profile["source_artifact_id"]:
                raise DomainError("genre_profile_changed", "频道题材配置在提交时改变，请刷新后重试。", 409)
            if kind == "module":
                self.check_genre_basis(profile, payload.get("genre_profile_basis"))
            if revision != profile["source_story_revision"]:
                raise DomainError("story_changed", "创作配置在提交时改变，请刷新后重试。", 409)
            db.execute(
                "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    kind,
                    payload["novel_id"],
                    branch_id,
                    "queued",
                    dumps(payload),
                    dumps(initial),
                    time(),
                    time(),
                ),
            )
            self.db.event(db, task_id, "queued", {"kind": kind})
            self.db.event(
                db,
                task_id,
                "genre_profile_frozen",
                {
                    "catalog_version": profile["catalog_version"],
                    "source_artifact_id": profile["source_artifact_id"],
                    "story_revision": revision,
                },
            )
            if key:
                db.execute("INSERT INTO task_requests VALUES(?,?)", (key, task_id))
        return self.task(task_id)

    def continue_workflow(self, current, payload, request):
        previous = self.find_request("workflow", payload)
        if previous:
            if (
                previous["result"].get("continued_from") != current["id"]
                or previous["result"].get("continuation_request") != request
            ):
                raise DomainError("idempotency", "该续跑标识已用于其他检查点或决定。", 409)
            return previous
        self.check_genre_snapshot(current["branch_id"], current["result"].get("genre_profile_snapshot"))
        if current["kind"] != "workflow" or current["status"] not in (
            "paused",
            "blocked_runtime",
            "blocked_creative",
            "cancelled",
        ):
            raise DomainError("state", "请先停止当前执行，再选择其他账户续跑。", 409)
        if current["status"] in ("blocked_runtime", "cancelled") and not request["acknowledge_unknown"]:
            raise DomainError("unknown_result", "请核对原账户的费用预留，再明确继续。", 409)
        if current["result"].get("waiting_artifact"):
            raise DomainError("review_required", "请先处理待审候选并恢复到章节检查点，再换账户续跑。", 409)
        if payload["account"] == current["payload"]["account"] or not request["reason"].strip():
            raise DomainError("continuation", "换账户续跑需要另一个账户和明确原因。", 409)
        new_id = uid()
        progress = json.loads(dumps(current["result"]))
        progress.update(continued_from=current["id"], continuation_request=request)
        with self.db.connect(True) as db:
            prior = db.execute(
                "SELECT t.* FROM task_requests r JOIN tasks t ON t.id=r.task_id WHERE r.key=?",
                (payload["client_request_id"],),
            ).fetchone()
            if prior:
                saved = json.loads(prior["result"])
                if (
                    prior["kind"] != "workflow"
                    or not same_task_payload(json.loads(prior["payload"]), payload)
                    or saved.get("continued_from") != current["id"]
                    or saved.get("continuation_request") != request
                ):
                    raise DomainError("idempotency", "该提交标识已用于不同的续跑决定。", 409)
                new_id = prior["id"]
            else:
                latest = db.execute(
                    "SELECT status,updated FROM tasks WHERE id=?", (current["id"],)
                ).fetchone()
                if latest["status"] != current["status"] or latest["updated"] != current["updated"]:
                    raise DomainError("task_changed", "原任务状态已改变，请刷新后再决定。", 409)
                story_revision = db.execute(
                    "SELECT story_revision FROM branches WHERE id=?", (current["branch_id"],)
                ).fetchone()[0]
                if progress.get("expected_story_revision", story_revision) != story_revision:
                    raise DomainError("story_changed", "前文已变化，请先在原任务核对影响并恢复检查点。", 409)
                progress["expected_story_revision"] = story_revision
                now = time()
                db.execute(
                    "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        new_id,
                        "workflow",
                        current["novel_id"],
                        current["branch_id"],
                        "queued",
                        dumps(payload),
                        dumps(progress),
                        now,
                        now,
                    ),
                )
                db.execute("INSERT INTO task_requests VALUES(?,?)", (payload["client_request_id"], new_id))
                db.execute(
                    "UPDATE tasks SET status='continued',result=?,updated=? WHERE id=?",
                    (dumps({**current["result"], "continued_to": new_id}), now, current["id"]),
                )
                details = {"previous_task": current["id"], "next_task": new_id, **request}
                self.db.event(db, current["id"], "continued_with_account", details)
                self.db.event(db, new_id, "continued_from", details)
        return self.task(new_id)

    def task(self, task_id):
        task = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        for field in ("payload", "result"):
            task[field] = json.loads(task[field])
        return task

    def completion(self, branch_id):
        branch = self.branch(branch_id)
        chapters = self.db.all(
            "SELECT id,title,position,revision,content,stale FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position",
            (branch_id,),
        )
        checks = self.db.all(
            "SELECT * FROM assessments WHERE branch_id=? AND story_revision=? ORDER BY created",
            (branch_id, branch["story_revision"]),
        )
        latest = {row["category"]: row for row in checks}
        workflow = self.db.all(
            "SELECT payload,result,status FROM tasks WHERE branch_id=? AND kind='workflow' ORDER BY created DESC LIMIT 1",
            (branch_id,),
        )
        production = json.loads(workflow[0]["result"]) if workflow else {}
        spec = json.loads(workflow[0]["payload"]) if workflow else {}
        missing = [chapter["position"] for chapter in chapters if not chapter["content"] or chapter["stale"]]
        expected = spec.get("chapters", len(chapters))
        length_failures = [
            c["position"]
            for c in chapters
            if spec and not spec["min_chars"] <= char_count(c["content"]) <= spec["max_chars"]
        ]
        model_report = production.get("completion", {})
        complete = (
            bool(chapters)
            and len(chapters) == expected
            and not missing
            and not length_failures
            and model_report.get("closed", False)
            and not any(f["severity"] == "blocker" for f in model_report.get("findings", []))
            and workflow[0]["status"] == "production_complete"
            and production.get("expected_story_revision") == branch["story_revision"]
        )
        passed = complete and all(
            latest.get(category, {}).get("result") == "passed"
            for category in ("engineering", "creative", "benefit")
        )
        return {
            "branch": branch,
            "chapter_count": len(chapters),
            "expected": expected,
            "chars": sum(char_count(c["content"]) for c in chapters),
            "missing_or_stale": missing,
            "length_failures": length_failures,
            "production_complete": complete,
            "accepted": passed,
            "assessments": list(latest.values()),
            "model_report": production.get("completion"),
            "note": "模型报告与作者验收分别记录；章数完成不等于全书通过。",
        }

    def export(self, branch_id, format="md"):
        branch = self.branch(branch_id)
        novel = self.db.one("SELECT * FROM novels WHERE id=?", (branch["novel_id"],))
        chapters = self.db.all(
            "SELECT * FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position", (branch_id,)
        )
        prefix = "# " if format == "md" else ""
        sections = [
            prefix + novel["title"],
            f"工作版本：{branch['name']}；故事修订：{branch['story_revision']}",
        ]
        for chapter in chapters:
            sections.append(("## " if format == "md" else "") + chapter["title"])
            sections.append(chapter["content"])
        return "\n\n".join(sections) + "\n"


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
