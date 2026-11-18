"""Prepare reviewable M7 material from a frozen set of saved chapters."""

import json
from typing import Literal

from pydantic import Field

from .book_completion import _basis
from .db import DomainError
from .evidence import completion_sources
from .models import Input, ModuleInput
from .story_graph import Source

KIND = "completion_preparation_candidate"
KEY = "completion_preparation"


class PrepareCompletion(Input):
    basis_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    spoiler_scope: Literal["opening", "whole"] = "opening"
    sample_chapters: int = Field(default=1, ge=1, le=20, strict=True)
    account: str
    client_request_id: str = Field(min_length=8, max_length=128)
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class DraftFinding(Input):
    note: str = Field(max_length=4000)
    source_ids: list[str] = Field(max_length=12)


class ClosureDraft(Input):
    ending: DraftFinding
    characters: DraftFinding
    promises: DraftFinding


class Introduction(Input):
    intro: str = Field(min_length=1, max_length=4000)


def freeze_tx(tx, branch_id, request):
    basis, _, chapters, _, _, _, _ = _basis(tx, branch_id)
    if basis != request["basis_sha256"]:
        raise DomainError("completion_changed", "完本依据已变化，请先核对最新版本。", 409)
    count = request["sample_chapters"]
    if len(chapters) < count or any(not row["content"].strip() for row in chapters[:count]):
        raise DomainError("completion_sample", "试读须包含已有正文的连续开篇章节。", 409)
    return {**request, "sample_chapter_ids": [row["id"] for row in chapters[:count]]}


def _saved_basis(worker, frozen):
    with worker.db.connect() as tx:
        tx.execute("BEGIN")
        basis = _basis(tx, worker.task["branch_id"])
        if basis[0] != frozen["basis_sha256"]:
            raise DomainError("completion_changed", "完本依据已修改，旧建议须重新生成。", 409)
        return basis


def _closing_units(chapters, report, byte_limit):
    whole = bool(chapters) and all(row["content"].strip() for row in chapters)
    if sum(len(row["content"].encode()) for row in chapters) <= byte_limit:
        units, _ = completion_sources(chapters)
        return units, "全部现有正文" if whole else "仅有正文的章节"

    # For larger drafts use exact quotations already frozen by the current inspection.
    indexed = {row["id"]: row for row in chapters}
    units = []
    seen = set()
    remaining = byte_limit
    if report and report["current"]:
        for item in report["items"]:
            for raw in (item.get("author_review") or {}).get("evidence", []):
                source = Source.model_validate(raw)
                address = source.chapter_id, source.revision, source.start, source.end
                size = len(source.quote.encode())
                if address in seen or size > remaining or source.chapter_id not in indexed:
                    continue
                seen.add(address)
                remaining -= size
                units.append(
                    {
                        "source_id": f"M7F{len(units) + 1:04}",
                        "text": source.quote,
                        "chapter_id": source.chapter_id,
                        "revision": source.revision,
                        "start": source.start,
                        "end": source.end,
                    }
                )
    return units, "当前体检的可核对选区"


async def run(worker, request, frozen):
    _, manifest, chapters, _, _, report, _ = _saved_basis(worker, frozen)
    account = worker.settings.providers[request["account"]]
    byte_limit = account.max_input_bytes // 3
    units, coverage = _closing_units(chapters, report, byte_limit)
    closures = {key: {"note": "", "sources": []} for key in ("ending", "characters", "promises")}
    if units:
        by_id = {unit["source_id"]: unit for unit in units}
        contract = ClosureDraft.model_json_schema()
        contract["$defs"]["DraftFinding"]["properties"]["source_ids"]["items"]["enum"] = list(by_id)
        value = await worker.call(
            {
                "动作": "准备完本收束核对建议",
                "建议范围": coverage,
                "正文依据": [{key: row[key] for key in ("source_id", "text")} for row in units],
                "要求": "分别为终局、关键人物关系、承诺提出有来源的核对建议。仅引用给定的来源编号。"
                "在说明中区分已兑现、留白和待核。无法定位正文时留空。只能准备建议，不得给出作者决定或签收。",
            },
            context={},
            schema=ClosureDraft,
            contract=contract,
            max_output=4096,
        )
        for key, finding in ClosureDraft.model_validate(value).model_dump().items():
            selected = [by_id.get(identifier) for identifier in dict.fromkeys(finding["source_ids"])]
            if None in selected:
                raise DomainError("completion_evidence", "完本建议引用了未知来源，请重新生成。", 409)
            if selected and finding["note"].strip():
                closures[key] = {
                    "note": finding["note"],
                    "sources": [
                        {
                            "quote": unit["text"],
                            **{field: unit[field] for field in ("chapter_id", "revision", "start", "end")},
                        }
                        for unit in selected
                    ],
                }

    _saved_basis(worker, frozen)
    whole = frozen["spoiler_scope"] == "whole"
    if whole:
        introduction_text = [row["text"] for row in units]
        introduction_coverage = coverage
    else:
        sample = chapters[: len(frozen["sample_chapter_ids"])]
        opening = "\n\n".join(row["content"] for row in sample).encode()
        introduction_text = [opening[:byte_limit].decode(errors="ignore")]
        introduction_coverage = (
            "所选连续开篇" if len(opening) <= byte_limit else "所选开篇的前段，超过输入范围的部分未发送"
        )
    if not any(text.strip() for text in introduction_text):
        raise DomainError(
            "completion_evidence", "当前范围缺少可送审的正文依据，请选择仅开篇或补充体检选区。", 409
        )
    introduction = await worker.call(
        {
            "动作": "起草完本交付简介",
            "作品": manifest["title"],
            "剧透范围": "全书" if whole else "仅开篇试读范围",
            "依据覆盖": introduction_coverage,
            "可用正文": introduction_text,
            "要求": "写一份简洁的交付简介。只根据提供的正文范围写作，勿添加材料外情节。",
        },
        context={},
        schema=Introduction,
        max_output=2048,
    )
    _saved_basis(worker, frozen)
    artifact = worker.domain.stage(
        request,
        KIND,
        {
            "closures": closures,
            "intro": Introduction.model_validate(introduction).intro,
            "coverage": coverage,
            "intro_coverage": introduction_coverage,
            "spoiler_scope": frozen["spoiler_scope"],
            "sample_chapter_ids": frozen["sample_chapter_ids"],
        },
        {"completion_basis_sha256": frozen["basis_sha256"]},
        worker.task["id"],
        worker.attempt,
    )
    worker.checkpoint({**worker.task["result"], "artifact_id": artifact["id"]}, "succeeded")


def register_routes(app, domain, provider_ready):
    path = "/api/branches/{branch_id}/book-completion/preparation"

    @app.post(path, status_code=202)
    def prepare(branch_id: str, data: PrepareCompletion):
        branch = domain.branch(branch_id)
        provider_ready(data.account)
        request = ModuleInput(
            action="outline.plan",
            novel_id=branch["novel_id"],
            branch_id=branch_id,
            instruction="准备完本核对建议与交付简介。",
            account=data.account,
            client_request_id=data.client_request_id,
            budget_limit=data.budget_limit,
        ).model_dump(exclude_none=True)
        request[KEY] = data.model_dump(include={"basis_sha256", "spoiler_scope", "sample_chapters"})
        return domain.queue("module", request)

    @app.get(path)
    def latest_preparation(branch_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            basis = _basis(tx, branch_id)[0]
            row = tx.execute(
                "SELECT id,status,error,json_extract(result,'$.artifact_id') AS artifact_id FROM tasks "
                "WHERE branch_id=? AND kind='module' AND json_type(payload,'$.completion_preparation')='object' "
                "ORDER BY rowid DESC LIMIT 1",
                (branch_id,),
            ).fetchone()
            if not row:
                return {"task": None}
            result = dict(row)
            artifact = tx.execute(
                "SELECT payload,basis FROM artifacts WHERE id=? AND kind=?", (row["artifact_id"], KIND)
            ).fetchone()
            if artifact:
                result["candidate"] = {
                    **json.loads(artifact["payload"]),
                    "current": json.loads(artifact["basis"])["completion_basis_sha256"] == basis,
                }
            return {"task": result}
