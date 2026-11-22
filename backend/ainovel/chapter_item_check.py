"""Read-only checks over a saved chapter and explicitly selected sources."""

import json
from time import time
from typing import Literal

from pydantic import Field, StrictInt, field_validator

from .db import DomainError, dumps
from .models import Input
from .writing_context import ChapterBasis, SettingBasis, chapter_setting

KIND = "chapter_item_check"
ARTIFACT = "chapter_item_check"
ACTION = "review.chapter.item"
VERSION = "chapter-item-check/1"


class CheckInput(Input):
    base_revision: StrictInt = Field(ge=0)
    account: str = Field(min_length=1, max_length=100)
    client_request_id: str = Field(min_length=8, max_length=128)
    previous_base_revision: StrictInt | None = Field(default=None, ge=0)
    previous_chapter_id: str | None = Field(default=None, min_length=1, max_length=128)


class CreateCheck(CheckInput):
    condition: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=120)
    include_previous: bool = False
    setting_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_setting: SettingBasis | None = None

    @field_validator("condition")
    @classmethod
    def clean_condition(cls, value):
        if not value.strip():
            raise ValueError("请填写要核对的问题或条件。")
        return value.strip()


class Recheck(CheckInput):
    previous_result_id: str = Field(min_length=1, max_length=128)


class Quote(Input):
    source: Literal["chapter", "previous"]
    quote: str = Field(min_length=1, max_length=800)


class Judgment(Input):
    result: Literal["pass", "fail", "unknown"]
    reason: str = Field(min_length=1, max_length=1200)
    evidence: list[Quote] = Field(max_length=12)


class CheckEvidence(Quote):
    start: int
    end: int


class Sources(Input):
    chapter: ChapterBasis
    previous: ChapterBasis | None
    setting: SettingBasis | None


class CheckResult(Input):
    id: str
    created: float
    base_revision: int
    result: Literal["pass", "fail", "unknown"]
    reason: str
    evidence: list[CheckEvidence]
    sources: Sources


class CheckItem(Input):
    id: str
    title: str
    condition: str
    include_previous: bool
    previous_chapter_id: str | None
    setting_id: str | None
    status: Literal["current", "stale"]
    changed_sources: list[Literal["chapter", "previous", "setting"]]
    latest: CheckResult
    history: list[CheckResult]


class AdjacentChapter(ChapterBasis):
    id: str


class ChapterChecks(Input):
    chapter_revision: int
    items: list[CheckItem]
    previous: AdjacentChapter | None


def _chapter(tx, chapter_id):
    chapter = tx.execute(
        "SELECT c.*,b.novel_id FROM chapters c JOIN branches b ON b.id=c.branch_id "
        "WHERE c.id=? AND c.archived=0",
        (chapter_id,),
    ).fetchone()
    if not chapter:
        raise DomainError("not_found", "章节不存在或已归档。", 404)
    return chapter


def _previous(tx, chapter):
    return tx.execute(
        "SELECT * FROM chapters WHERE branch_id=? AND archived=0 AND position<? "
        "ORDER BY position DESC LIMIT 1",
        (chapter["branch_id"], chapter["position"]),
    ).fetchone()


def _source(chapter):
    if chapter is None:
        return None
    return {
        "chapter_id": chapter["id"],
        "title": chapter["title"],
        "position": chapter["position"],
        "revision": chapter["revision"],
    }


def _sources(tx, chapter, spec):
    prior = _previous(tx, chapter) if spec["include_previous"] else None
    if prior and prior["id"] != spec["previous_chapter_id"]:
        prior = None
    setting = chapter_setting(tx, chapter, spec["setting_id"]) if spec["setting_id"] else None
    return {"chapter": _source(chapter), "previous": _source(prior), "setting": setting}, prior


def _source_identity(source):
    return (source["chapter_id"], source["revision"]) if source else None


def _setting_basis(setting):
    if not setting or not setting["source"]:
        return setting
    evidence = {key: value for key, value in setting["source"].items() if key not in {"title", "position"}}
    return {**setting, "source": evidence}


def _changed_sources(current, frozen):
    return [
        key
        for key, identify in (
            ("chapter", _source_identity),
            ("previous", _source_identity),
            ("setting", _setting_basis),
        )
        if identify(current[key]) != identify(frozen[key])
    ]


def _reports(tx, chapter_id, check_id=None, excluding=None):
    rows = tx.execute(
        "SELECT id,created,base_revision,payload,basis FROM artifacts "
        "WHERE chapter_id=? AND kind=? AND id IS NOT ? "
        "AND (? IS NULL OR json_extract(payload,'$.check_id')=?) "
        "ORDER BY created DESC,rowid DESC",
        (chapter_id, ARTIFACT, excluding, check_id, check_id),
    )
    for row in rows:
        payload = json.loads(row["payload"])
        yield (
            payload["check_id"],
            payload["spec"],
            {
                "id": row["id"],
                "created": row["created"],
                "base_revision": row["base_revision"],
                "result": payload["result"],
                "reason": payload["reason"],
                "evidence": payload["evidence"],
                "sources": json.loads(row["basis"])["sources"],
            },
        )


def list_checks(domain, chapter_id):
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        chapter = _chapter(tx, chapter_id)
        items = {}
        for check_id, spec, report in _reports(tx, chapter_id):
            if item := items.get(check_id):
                item["history"].append(report)
                continue
            current, _ = _sources(tx, chapter, spec)
            changed = _changed_sources(current, report["sources"])
            items[check_id] = {
                "id": check_id,
                "title": spec["title"],
                "condition": spec["condition"],
                "include_previous": spec["include_previous"],
                "previous_chapter_id": spec["previous_chapter_id"],
                "setting_id": spec["setting_id"],
                "status": "stale" if changed else "current",
                "changed_sources": changed,
                "latest": report,
                "history": [],
            }
        previous = _previous(tx, chapter)
        return {
            "chapter_revision": chapter["revision"],
            "items": list(items.values()),
            "previous": {**_source(previous), "id": previous["id"]} if previous else None,
        }


def _latest(tx, request, excluding=None):
    report = next(_reports(tx, request["chapter_id"], request["check_id"], excluding), None)
    if not report:
        raise DomainError("check_not_found", "校验项不在当前章节。", 404)
    if report[2]["id"] != request["previous_result_id"]:
        raise DomainError("check_changed", "该校验项已有更新结果，请刷新后再复检。", 409)
    return report[1]


def freeze(tx, request):
    chapter = _chapter(tx, request["chapter_id"])
    if chapter["branch_id"] != request["branch_id"] or chapter["revision"] != request["base_revision"]:
        raise DomainError("revision_conflict", "当前正文已改变，请重新读取保存稿再核对。", 409)
    if request.get("check_id"):
        spec = _latest(tx, request)
        running = tx.execute(
            "SELECT 1 FROM tasks WHERE kind=? AND branch_id=? AND status IN ('queued','running') "
            "AND json_extract(payload,'$.check_id')=? LIMIT 1",
            (KIND, request["branch_id"], request["check_id"]),
        ).fetchone()
        if running:
            raise DomainError("check_running", "该校验项已有复检任务，请等待结果或取消后重试。", 409)
    else:
        previous = _previous(tx, chapter) if request["include_previous"] else None
        if not chapter["content"].strip():
            raise DomainError("empty_check", "请先写下并保存本章正文。", 409)
        if request["include_previous"] and (not previous or not previous["content"].strip()):
            raise DomainError("empty_check", "找不到可供核对的相邻前章保存稿。", 409)
        spec = {
            "title": (request["title"] or "").strip() or request["condition"][:120],
            "condition": request["condition"],
            "include_previous": request["include_previous"],
            "previous_chapter_id": previous["id"] if previous else None,
            "setting_id": request["setting_id"],
        }
    sources, _ = _sources(tx, chapter, spec)
    prior = sources["previous"] or {}
    if (
        request["previous_base_revision"] is not None
        and request["previous_base_revision"] != prior.get("revision")
    ) or (
        request["previous_chapter_id"] is not None
        and request["previous_chapter_id"] != prior.get("chapter_id")
    ):
        raise DomainError("check_changed", "相邻前章依据已改变，请刷新后再核对。", 409)
    if not request.get("check_id") and spec["setting_id"]:
        setting = sources["setting"]
        if not setting or not setting["source_current"]:
            raise DomainError("check_changed", "所选设定已失效，请核对当前设定及原文。", 409)
        expected = request["expected_setting"]
        if expected is not None and _setting_basis(expected) != _setting_basis(setting):
            raise DomainError("check_changed", "所选设定已改变，请刷新后再核对。", 409)
    return {"version": VERSION, "spec": spec, "sources": sources}


def _validate(tx, task, excluding=None):
    snapshot = task["result"]["chapter_item_check_snapshot"]
    request = task["payload"]
    if task["kind"] != KIND or snapshot["version"] != VERSION:
        raise DomainError("check_version", "无法确认本次校验依据的版本。", 409)
    chapter = _chapter(tx, request["chapter_id"])
    if request.get("check_id"):
        _latest(tx, request, excluding)
    current, previous = _sources(tx, chapter, snapshot["spec"])
    if _changed_sources(current, snapshot["sources"]):
        raise DomainError("check_changed", "本次核对的正文或设定在运行期间改变，请刷新后重新核对。", 409)
    return snapshot, chapter, previous


def validate(worker):
    with worker.db.connect() as tx:
        tx.execute("BEGIN")
        return _validate(tx, worker.task)


def _locate(report, texts):
    issues, locations = [], []
    for item in report["evidence"]:
        quote = item["quote"]
        scope = item["source"]
        text = texts.get(scope, "")
        offset = text.find(quote)
        if not quote.strip() or offset < 0 or text.find(quote, offset + 1) >= 0:
            issues.append(f"{scope} 引文须连续且唯一定位于本次选定的保存原文。")
            continue
        start = len(text[:offset].encode("utf-16-le")) // 2
        locations.append({**item, "start": start, "end": start + len(quote.encode("utf-16-le")) // 2})
    if not report["reason"].strip():
        issues.append("请写明判断理由或还需核对的事项。")
    if report["result"] != "unknown" and {item["source"] for item in locations} != texts.keys():
        issues.append("通过或不符须提供本次全部选定章节的原文依据；依据不足填unknown。")
    return issues, locations


async def run(worker):
    snapshot, chapter, previous = validate(worker)
    spec, sources = snapshot["spec"], snapshot["sources"]
    request = worker.task["payload"]
    texts = {"chapter": chapter["content"]}
    if previous:
        texts["previous"] = previous["content"]
    missing = []
    if not chapter["content"].strip():
        missing.append("当前章已无可核对的保存正文")
    if spec["include_previous"] and (not previous or not previous["content"].strip()):
        missing.append("原前章当前没有有效的相邻保存稿")
    if spec["setting_id"] and (not sources["setting"] or not sources["setting"]["source_current"]):
        missing.append("所选设定或原文依据已失效")
    if missing:
        report = {"result": "unknown", "reason": "、".join(missing) + "。", "evidence": []}
    else:
        report = await worker.grounded(
            {
                "action": ACTION,
                "condition": spec["condition"],
                "sources": {
                    **sources,
                    **{key: {**sources[key], "text": value} for key, value in texts.items()},
                },
                "要求": (
                    "只核对明确选中的一项，以本次提供的保存正文和采用设定为依据。"
                    "设定是规则依据；有意留白或计划不能当作已发生。"
                    "通过或不符要给出每个选定章节各自连续且唯一可定位的逐字引文。"
                    "不能用引文是否出现直接代替语义核对；有疑义填unknown。"
                    "reason简述实际证据和判断，不改稿、不追加其他校验。"
                ),
            },
            Judgment,
            {},
            lambda value: _locate(value, texts)[0],
            1800,
        )
        report["evidence"] = _locate(report, texts)[1]

    def publish(tx, result_id):
        _validate(tx, worker.task, excluding=result_id)
        completed = {**worker.task["result"], "artifact_id": result_id}
        tx.execute(
            "UPDATE tasks SET result=?,status='succeeded',error='',updated=? WHERE id=? AND attempt=?",
            (dumps(completed), time(), worker.task["id"], worker.attempt),
        )
        worker.db.event(
            tx, worker.task["id"], "checkpoint", {"status": "succeeded", "artifact_id": result_id}
        )

    worker.domain.stage(
        request,
        ARTIFACT,
        {
            "version": VERSION,
            "check_id": request.get("check_id") or worker.task["id"],
            "previous_result_id": request.get("previous_result_id"),
            "spec": spec,
            **report,
        },
        {"sources": sources},
        worker.task["id"],
        worker.attempt,
        on_stage=publish,
    )


def register_routes(app, domain, provider_ready):
    def submit(chapter_id, data, check_id=None):
        chapter = domain.chapter(chapter_id)
        branch = domain.branch(chapter["branch_id"])
        payload = {
            **data.model_dump(),
            "chapter_id": chapter_id,
            "branch_id": branch["id"],
            "novel_id": branch["novel_id"],
            "check_id": check_id,
        }
        if previous := domain.find_request(KIND, payload):
            return previous
        provider_ready(data.account)
        return domain.queue(KIND, payload)

    @app.get("/api/chapters/{chapter_id}/checks", response_model=ChapterChecks)
    def checks(chapter_id: str):
        return list_checks(domain, chapter_id)

    @app.post("/api/chapters/{chapter_id}/checks", status_code=202)
    def create_check(chapter_id: str, data: CreateCheck):
        return submit(chapter_id, data)

    @app.post("/api/chapters/{chapter_id}/checks/{check_id}/recheck", status_code=202)
    def recheck(chapter_id: str, check_id: str, data: Recheck):
        return submit(chapter_id, data, check_id)
