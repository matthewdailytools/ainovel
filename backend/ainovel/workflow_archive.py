"""Independent archives of accepted inspections and frozen completion packages."""

from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter, ValidationError, model_validator

from .book_completion import (
    CLASSIFIED_VERSION,
    KIND as COMPLETION,
    WORLD_CATEGORY,
    CompletionRequest,
    basis_fingerprint,
    material_chunks,
)
from .db import DomainError
from .domain import utf16_slice
from .models import Input
from .project_portability import Identifier
from .setting_use_portability import validate_version as validate_setting_use_version
from .story_graph import Source
from .workflow_inspection import (
    AuthorReview,
    CheckId,
    KIND as INSPECTION,
    LengthTargets,
    SOFT_DEVIATION_POLICY,
    Scope,
    _item,
    _sha,
    inspection_setting_use_matches,
    inspection_setting_uses,
    validate_soft_deviation_review,
)
from .workflow_privacy import has_reference_selection

MATERIAL_FILES = frozenset({"正文.md", "设定册.md", "简介.md", "试读.md"})
CHECKS = ("manuscript", "ending", "characters", "promises", "inspection", "delivery")
FORMAT = "ainovel-manuscript-sync/1"
WORKFLOW_OBJECTS = {"workflow-inspection": INSPECTION, "workflow-completion": COMPLETION}
WORKFLOW_PREFIXES = tuple(f"{kind}:" for kind in WORKFLOW_OBJECTS)
SCHEMA = """
CREATE TABLE IF NOT EXISTS manuscript_workflow_history(
 branch_id TEXT NOT NULL REFERENCES branches(id),kind TEXT NOT NULL,id TEXT NOT NULL,
 payload TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(branch_id,kind,id));
CREATE TABLE IF NOT EXISTS manuscript_workflow_cloud_sources(
 branch_id TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,device_id TEXT NOT NULL,
 change_id TEXT NOT NULL,sequence INTEGER NOT NULL,
 PRIMARY KEY(branch_id,kind,id),FOREIGN KEY(branch_id,kind,id)
 REFERENCES manuscript_workflow_history(branch_id,kind,id));
"""


class ChapterBinding(Input):
    id: Identifier
    revision: int = Field(ge=0, strict=True)
    revision_id: Identifier
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ChapterManifest(Input):
    id: Identifier
    title: str = Field(min_length=1, max_length=200)
    position: int = Field(ge=1, strict=True)
    revision: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class InspectionChapter(Input):
    id: Identifier
    position: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    chars: int = Field(ge=0, strict=True)
    stale: bool = Field(strict=True)


class ArchivedAuthorReview(AuthorReview):
    candidate_result: Literal["pass", "fail", "unknown"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class StateConflictSide(Input):
    record_id: Identifier
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=4000)
    event_id: Identifier
    sources: list[Source] = Field(min_length=1, max_length=30)


class StateConflict(Input):
    character_id: Identifier
    category: Literal["knowledge", "capability", "resource", "injury", "other"]
    key: str = Field(min_length=1, max_length=100)
    audience: Literal["author", "character", "reader"]
    story_order: int | None
    event_id: Identifier | None
    sides: list[StateConflictSide] = Field(min_length=2, max_length=200_000)


STATE_CONFLICTS = TypeAdapter(Annotated[list[StateConflict], Field(min_length=1, max_length=200_000)])


class InspectionItem(Input):
    check_id: CheckId
    title: str = Field(min_length=1, max_length=200)
    status: Literal["green", "yellow", "red", "pending", "not_applicable", "outdated"]
    summary: str = Field(max_length=4000)
    basis: dict[str, JsonValue]
    can_mark_not_applicable: bool = Field(strict=True)
    basis_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    author_review: ArchivedAuthorReview | None = Field(default=None, exclude_if=lambda value: value is None)


class InspectionReport(Input):
    version: Literal["m6-inspection/1"]
    scope: Scope
    scope_id: Identifier | None
    chapters: list[InspectionChapter] = Field(max_length=5000)
    length_target: dict[str, JsonValue] | None
    items: list[InspectionItem] = Field(min_length=10, max_length=10)
    basis_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    length_targets: LengthTargets | None = None


class ArchivedInspection(Input):
    id: Identifier
    created: float = Field(ge=0, allow_inf_nan=False)
    chapter_bindings: list[ChapterBinding] = Field(max_length=5000)
    report: InspectionReport


def has_frozen_reference_selection(entry):
    return isinstance(entry, ArchivedInspection) and has_reference_selection(
        [item.model_dump() for item in entry.report.items if item.check_id == "08"]
    )


class PromiseManifest(Input):
    id: Identifier
    revision: int = Field(ge=0, strict=True)


class DeliverySetting(Input):
    id: Identifier
    category_id: Identifier
    category_title: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=4000)


class DeliveryManifest(Input):
    format: Literal["book-completion/1", "book-completion/2"]
    branch_id: Identifier
    title: str = Field(min_length=1, max_length=200)
    story_revision: int = Field(ge=0, strict=True)
    chapters: list[ChapterManifest] = Field(min_length=1, max_length=5000)
    project_id: Identifier | None
    setting_id: Identifier | None
    outline_id: Identifier | None
    inspection_id: Identifier
    length_target: dict[str, JsonValue] | None
    promises: list[PromiseManifest] = Field(max_length=5000)
    settings_catalogue: list[DeliverySetting] | None = Field(
        default=None, min_length=1, max_length=240, exclude_if=lambda value: value is None
    )
    ai_preparation_id: Identifier | None = Field(default=None, exclude_if=lambda value: value is None)
    files: dict[str, str] = Field(min_length=4, max_length=4)
    spoiler_scope: Literal["opening", "whole"]
    sample_chapter_ids: list[Identifier] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_settings_catalogue(self):
        if self.format != CLASSIFIED_VERSION:
            if "settings_catalogue" in self.model_fields_set:
                raise ValueError("世界设定版交付清单不能附带分类设定册。")
            return self
        entries = self.settings_catalogue
        if (
            not self.setting_id
            or not entries
            or all(entry.category_id == WORLD_CATEGORY for entry in entries)
            or len({entry.id for entry in entries}) != len(entries)
            or any(not entry.detail.strip() for entry in entries)
        ):
            raise ValueError("分类设定册须绑定正式设定修订并包含有效的不同设定条目。")
        return self


class CompletionCheck(Input):
    id: Literal["manuscript", "ending", "characters", "promises", "inspection", "delivery"]
    title: str = Field(min_length=1, max_length=200)
    status: Literal["green", "red", "pending"]
    detail: str


class CompletionProof(Input):
    id: Identifier
    created: float = Field(ge=0, allow_inf_nan=False)
    chapter_bindings: list[ChapterBinding] = Field(min_length=1, max_length=5000)
    basis_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    checks: list[CompletionCheck] = Field(min_length=6, max_length=6)
    manifest: DeliveryManifest
    review: CompletionRequest


class ArchivedCompletion(CompletionProof):
    materials: dict[str, str] = Field(min_length=4, max_length=4)


COMPLETION_WIRE = TypeAdapter(ArchivedCompletion | CompletionProof)


class WorkflowHistory(Input):
    inspections: list[ArchivedInspection] = Field(default_factory=list, max_length=1000)
    completions: list[ArchivedCompletion] = Field(default_factory=list, max_length=1000)


class WorkflowObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["workflow-inspection", "workflow-completion"]
    branch_id: Identifier
    # A string preserves the frozen order of nested inspection basis keys across transport.
    entry_json: str = Field(min_length=2, max_length=1024 * 1024)


def workflow_object(branch_id, entry):
    return {
        "format": FORMAT,
        "kind": "workflow-inspection" if isinstance(entry, ArchivedInspection) else "workflow-completion",
        "branch_id": branch_id,
        "entry_json": entry.model_dump_json(exclude={"materials"}),
    }


def parse_workflow_object(branch_id, object_id, payload, tombstone):
    if tombstone:
        raise DomainError("sync_workflow", "已归档的体检或交付历史不能删除。", 409)
    try:
        value = WorkflowObject.model_validate(payload)
        entry = (
            ArchivedInspection.model_validate_json(value.entry_json)
            if value.kind == "workflow-inspection"
            else COMPLETION_WIRE.validate_json(value.entry_json)
        )
    except ValidationError as error:
        raise DomainError("sync_workflow", "云端体检或交付历史格式无效。", 409) from error
    if value.branch_id != branch_id or object_id != f"{value.kind}:{entry.id}":
        raise DomainError("sync_workflow", "体检或交付历史归属不匹配。", 409)
    return entry


def same_workflow_entry(left, right):
    if isinstance(left, CompletionProof) and isinstance(right, CompletionProof):
        return left.model_dump(exclude={"materials"}) == right.model_dump(exclude={"materials"})
    return left == right


def validate_workflow_object(
    entry, branch_id, revision, inspection, *, setting_use=None, settings=None, hydrate=False
):
    def require_revision(identifier):
        source = revision(identifier)
        if not source:
            raise DomainError("sync_workflow_dependency", f"历史记录尚缺原章稿修订 {identifier}。", 409)
        return source

    if isinstance(entry, ArchivedInspection):

        def require_setting_source(lookup, family, identifier):
            source = lookup(identifier) if lookup else None
            if not source:
                raise DomainError("sync_workflow_dependency", f"历史记录尚缺{family} {identifier}。", 409)
            return source

        _validate_inspection(
            entry,
            branch_id,
            require_revision,
            lambda identifier: require_setting_source(setting_use, "设定使用原修订", identifier),
            lambda identifier: require_setting_source(settings, "正式设定修订", identifier),
        )
        return entry
    report = inspection(entry.manifest.inspection_id)
    if not isinstance(report, ArchivedInspection):
        raise DomainError("sync_workflow_dependency", "完本交付尚缺原全书体检单。", 409)
    if report.id == entry.id:
        _bad("稿件包报告的来源编号重复。")
    return _validate_completion(entry, branch_id, report, require_revision, hydrate=hydrate)


def _digest(text):
    return sha256(text.encode()).hexdigest()


def checked_materials(manifest, materials):
    if set(materials) != MATERIAL_FILES or manifest["files"] != {
        name: _digest(text) for name, text in materials.items()
    }:
        raise DomainError("completion_integrity", "交付材料名称或 SHA-256 校验失败。", 409)


def _bad(message):
    raise DomainError("manuscript_workflow", message, 409)


def _chapters(manifest, bindings, revision_for, retain_content):
    if len(manifest) != len(bindings) or len({item.id for item in bindings}) != len(bindings):
        _bad("稿件包报告的正文清单和来源修订不一致。")
    result = {}
    for chapter, binding in zip(manifest, bindings, strict=True):
        revision = revision_for(binding.revision_id)
        if (
            (chapter.id, chapter.revision, chapter.sha256) != (binding.id, binding.revision, binding.sha256)
            or not revision
            or revision.id != binding.revision_id
            or (revision.chapter_id, revision.revision, revision.sha256)
            != (binding.id, binding.revision, binding.sha256)
            or _digest(revision.content) != binding.sha256
        ):
            _bad(f"稿件包缺少报告所引用的准确正文修订 {binding.revision_id}。")
        result[chapter.id] = {
            "title": chapter.title,
            "id": chapter.id,
            "revision": revision.revision,
            "revision_id": revision.id,
        }
        if chapter.id in retain_content:
            result[chapter.id]["content"] = revision.content
    return result


def _sources(sources, chapters, *, label="作者核对"):
    for source in sources:
        revision = chapters.get(source.chapter_id)
        if not revision or source.revision != revision["revision"]:
            _bad(f"稿件包{label}引用了报告范围之外的正文修订。")
        try:
            quote = utf16_slice(revision["content"], source.start, source.end)[1]
        except DomainError as error:
            raise DomainError("manuscript_workflow", f"稿件包报告的{label}原文范围无效。", 409) from error
        if quote != source.quote or not quote.strip():
            _bad(f"稿件包报告的{label}引文与原稿不一致。")


def _state_conflict_sources(item):
    if "state_conflicts" not in item.basis:
        return []
    if (
        item.check_id != "03"
        or item.status != "red"
        or (item.author_review and item.author_review.status != "red")
    ):
        _bad("有确定的人物状态冲突时，第 03 项须保留红项。")
    try:
        conflicts = STATE_CONFLICTS.validate_python(item.basis["state_conflicts"], strict=True)
    except ValidationError:
        _bad("稿件包中第 03 项的人物状态冲突格式无效。")
    records = item.basis.get("records")
    if not isinstance(records, list):
        _bad("稿件包中第 03 项缺少冲突所对应的人物状态记录。")
    by_id = {row["id"]: row for row in records if isinstance(row, dict) and isinstance(row.get("id"), str)}
    original = []
    for conflict in conflicts:
        if (
            (conflict.story_order is None) == (conflict.event_id is None)
            or len({side.record_id for side in conflict.sides}) != len(conflict.sides)
            or len({side.value for side in conflict.sides}) < 2
            or (conflict.event_id and any(side.event_id != conflict.event_id for side in conflict.sides))
        ):
            _bad("稿件包中第 03 项缺少同一时点下互相矛盾的记录。")
        for side in conflict.sides:
            frozen = by_id.get(side.record_id)
            expected = {
                **conflict.model_dump(include={"character_id", "category", "key", "audience"}),
                **side.model_dump(include={"revision", "title", "value", "event_id"}),
                "kind": "state",
                "truth": "actual",
            }
            if (
                not frozen
                or any(frozen.get(key) != value for key, value in expected.items())
                or frozen.get("invalid_reasons") != []
                or not isinstance(frozen.get("sources"), list)
                or any(source.model_dump() not in frozen.get("sources", []) for source in side.sources)
            ):
                _bad("稿件包中第 03 项的冲突与冻结人物状态记录不一致。")
            original.extend(side.sources)
    return original


def _setting_use_sources(item, plan_basis, manifest, branch_id, revision_for, setting_use, settings):
    try:
        sources = inspection_setting_uses(item.basis)
    except ValueError as error:
        raise DomainError("manuscript_workflow", "报告的设定正文使用依据无效。", 409) from error
    plans = plan_basis.get("plans")
    scoped_outline = item.basis.get("author_setting_use_outline_id")
    if not sources or not isinstance(plans, list) or not isinstance(scoped_outline, str):
        _bad("报告的设定正文使用依据缺少对应的冻结大纲及章纲计划。")
    attachments = {
        (row.get("chapter_id"), key)
        for row in plans
        if isinstance(row, dict)
        and isinstance(row.get("chapter_id"), str)
        and isinstance(row.get("setting_ids"), list)
        for key in row["setting_ids"]
        if isinstance(key, str)
    }
    used = set()
    settings_versions = {}
    for source in sources:
        evidence = source.evidence
        chapter = manifest.get(evidence.chapter_id)
        key = (evidence.chapter_id, source.item_id)
        if (
            key in used
            or key not in attachments
            or source.outline_version_id != scoped_outline
            or not chapter
            or chapter.stale
            or chapter.sha256 != source.chapter_sha256
        ):
            _bad("报告的设定正文使用依据不在对应章稿清单内。")
        used.add(key)
        version = setting_use(source.version_id) if setting_use else None
        if not version:
            _bad("稿件包缺少体检所引用的原设定使用证明版本。")
        try:
            validate_setting_use_version(branch_id, version)
        except DomainError as error:
            raise DomainError("manuscript_workflow", "报告的设定使用证明版本校验失败。", 409) from error
        if source.settings_version_id not in settings_versions:
            settings_versions[source.settings_version_id] = (
                settings(source.settings_version_id) if settings else None
            )
        adopted_settings = settings_versions[source.settings_version_id]
        if not inspection_setting_use_matches(source, version, adopted_settings):
            _bad("报告的设定使用依据与正式证明或原设定条目不一致。")
        original = revision_for(evidence.revision_id)
        if (
            not original
            or (original.id, original.chapter_id, original.revision, original.sha256)
            != (evidence.revision_id, evidence.chapter_id, source.revision, source.chapter_sha256)
            or _digest(original.content) != source.chapter_sha256
        ):
            _bad("报告缺少确切的设定正文使用原稿修订。")
        try:
            quote = utf16_slice(original.content, evidence.start, evidence.end)[1]
        except DomainError as error:
            raise DomainError("manuscript_workflow", "报告的设定正文使用选区不合法。", 409) from error
        if not quote.strip() or len(quote) > 4000 or _digest(quote) != evidence.quote_sha256:
            _bad("报告的设定正文使用选区与原稿不一致。")


def validate_workflow_history(history, branch_id, revisions, *, setting_use=None, settings=None):
    if not history:
        return
    reports = {entry.id: entry for entry in history.inspections}
    all_ids = [*reports, *(entry.id for entry in history.completions)]
    if len(all_ids) != len(set(all_ids)) or len(reports) != len(history.inspections):
        _bad("稿件包报告的来源编号重复。")
    for report in history.inspections:
        _validate_inspection(report, branch_id, revisions.get, setting_use, settings)
    for completion in history.completions:
        _validate_completion(
            completion, branch_id, reports.get(completion.manifest.inspection_id), revisions.get
        )


def _validate_inspection(archived, branch_id, revision_for, setting_use, settings):
    report = archived.report
    if (
        (report.scope == "book") != (report.scope_id is None)
        or (report.scope == "chapter" and [row.id for row in report.chapters] != [report.scope_id])
        or (report.scope == "volume" and not report.chapters)
    ):
        _bad("稿件包的体检范围和章稿清单不一致。")
    conflicts = [source for item in report.items for source in _state_conflict_sources(item)]
    retain = {
        source.chapter_id
        for item in report.items
        if item.author_review
        for source in item.author_review.evidence
    }
    retain.update(source.chapter_id for source in conflicts)
    chapters = _chapters(report.chapters, archived.chapter_bindings, revision_for, retain)
    _sources(conflicts, chapters, label="第 03 项冲突")
    manifest = [chapter.model_dump() for chapter in report.chapters]
    chapters_by_id = {chapter.id: chapter for chapter in report.chapters}
    if [item.check_id for item in report.items] != [f"{number:02d}" for number in range(1, 11)]:
        _bad("稿件包体检单缺少十项检查或编号顺序错误。")
    for item in report.items:
        if "author_setting_uses" in item.basis or "author_setting_use_outline_id" in item.basis:
            if item.check_id != "08":
                _bad("设定正文使用依据须属于第 08 项。")
            _setting_use_sources(
                item, report.items[1].basis, chapters_by_id, branch_id, revision_for, setting_use, settings
            )
        if report.scope != "book" and "outline" in item.basis and not isinstance(item.basis["outline"], dict):
            _bad("稿件包分范围体检的章纲依据格式无效。")
        expected = _item(item.check_id, item.summary, item.basis, manifest=manifest, scope=report.scope)
        review = item.author_review
        if (
            expected["basis_sha256"] != item.basis_sha256
            or (item.can_mark_not_applicable and item.check_id not in {"05", "07"})
            or (item.status == "not_applicable" and not item.can_mark_not_applicable)
            or (review and review.check_id != item.check_id)
            or (review and review.status == "red" and item.status != "red")
            or (
                item.status in {"green", "not_applicable"}
                and (item.check_id in {"01", "02", "08", "10"} or item.status == "not_applicable")
                and (not review or review.status != item.status)
            )
        ):
            _bad("稿件包体检单的检查依据或作者裁定不一致。")
        if review:
            _sources(review.evidence, chapters)
        soft_policy = item.basis.get("soft_deviation_policy")
        if soft_policy is not None and (item.check_id != "05" or soft_policy != SOFT_DEVIATION_POLICY):
            _bad("稿件包体检单的软设定裁定口径无效。")
        if not (soft_policy and item.status == "green"):
            if review and review.soft_deviation_report_ids:
                _bad("稿件包体检单的软设定裁定与原检查项不一致。")
            continue
        if not review or review.status != "green":
            _bad("稿件包体检单缺少软设定偏离的作者通过裁定。")
        try:
            validate_soft_deviation_review(item.basis, review)
        except DomainError as error:
            _bad(f"稿件包体检单的软设定裁定无效。{error.message}")
    if report.basis_sha256 != _sha(
        [report.scope, report.scope_id, [item.basis_sha256 for item in report.items]]
    ):
        _bad("稿件包体检单的复核依据校验失败。")


def _validate_completion(record, branch_id, report, revision_for, *, hydrate=False):
    manifest = record.manifest
    if manifest.branch_id != branch_id or record.basis_sha256 != record.review.basis_sha256:
        _bad("稿件包完本记录归属或原作者复核依据不一致。")
    if tuple(check.id for check in record.checks) != CHECKS or any(
        check.status != "green" for check in record.checks
    ):
        _bad("稿件包的正式完本缺少原六项通过记录。")
    if (
        not report
        or report.report.scope != "book"
        or report.chapter_bindings != record.chapter_bindings
        or any(item.status not in {"green", "not_applicable"} for item in report.report.items)
    ):
        _bad("稿件包的完本记录缺少与原章稿对应的通过体检单。")
    declared = [chapter.model_dump(exclude={"chars", "stale"}) for chapter in report.report.chapters]
    stale_outline = report.report.items[4].basis.get("outline_stale")
    # The native delivery fingerprint encodes SQLite's integer stale flags.
    stale_chapters = [int(chapter.stale) for chapter in report.report.chapters]
    if (
        declared != [chapter.model_dump() for chapter in manifest.chapters]
        or not isinstance(stale_outline, list)
        or any(not isinstance(reason, str) for reason in stale_outline)
        or any(stale_chapters)
        or basis_fingerprint(
            manifest.model_dump(
                exclude={"ai_preparation_id", "files", "spoiler_scope", "sample_chapter_ids"}
            ),
            stale_chapters,
            stale_outline,
            [item.status for item in report.report.items],
        )
        != record.basis_sha256
    ):
        _bad("稿件包的完本清单与原体检单或冻结完本依据不一致。")
    review = record.review
    if review.author_decisions is not None and not review.author_decisions.cleared():
        _bad("历史完本的作者裁定尚未完成。")
    closures = (review.ending, review.characters, review.promises)
    evidence = [source for closure in closures for source in closure.sources]
    chapters = _chapters(
        manifest.chapters, record.chapter_bindings, revision_for, {source.chapter_id for source in evidence}
    )
    _sources(evidence, chapters)
    if (
        not review.read_note.strip()
        or not all(row.note.strip() for row in closures)
        or not review.materials_reviewed
        or manifest.spoiler_scope != review.spoiler_scope
        or manifest.sample_chapter_ids != review.sample_chapter_ids
        or manifest.ai_preparation_id != review.preparation_id
    ):
        _bad("稿件包完本记录缺少一致的作者通读、收束及材料核对。")
    if isinstance(record, ArchivedCompletion):
        checked_materials(manifest.model_dump(), record.materials)
        hydrate = False
    rule_basis = report.report.items[4].basis
    rules = rule_basis.get("rules")
    if not isinstance(rules, list) or any(
        not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("title", "detail"))
        for item in rules
    ):
        _bad("稿件包交付的设定册缺少原体检设定依据。")
    if manifest.format == CLASSIFIED_VERSION:
        world_entries = [
            (entry.id, entry.title, entry.detail)
            for entry in manifest.settings_catalogue
            if entry.category_id == WORLD_CATEGORY
        ]
        if manifest.setting_id != rule_basis.get("confirmation_id") or world_entries != [
            (rule.get("id"), rule["title"], rule["detail"]) for rule in rules
        ]:
            _bad("稿件包分类设定册与原体检设定修订不一致。")

    def read_content(chapter):
        return chapter["content"] if "content" in chapter else revision_for(chapter["revision_id"]).content

    rendered = material_chunks(manifest.model_dump(), list(chapters.values()), rules, review, read_content)
    if rendered is None or set(rendered) != set(manifest.files):
        _bad("稿件包的交付材料与冻结稿件、设定或作者输入不一致。")
    materials = {}
    for name, chunks in rendered.items():
        digest = sha256()
        if hydrate:
            parts = list(chunks)
            materials[name] = "".join(parts)
            chunks = parts
        for chunk in chunks:
            digest.update(chunk.encode("utf-8"))
        if digest.hexdigest() != manifest.files[name]:
            _bad("稿件包的交付材料与冻结稿件、设定或作者输入不一致。")
    return ArchivedCompletion(**record.model_dump(), materials=materials) if hydrate else record


def _bindings(tx, branch_id, chapters):
    references = []
    for chapter in chapters:
        row = tx.execute(
            "SELECT r.id,r.content FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
            "WHERE c.branch_id=? AND r.chapter_id=? AND r.revision=?",
            (branch_id, chapter["id"], chapter["revision"]),
        ).fetchone()
        if not row or _digest(row["content"]) != chapter["sha256"]:
            _bad("本机缺少历史报告原章稿，无法完整导出。")
        references.append(
            {**{key: chapter[key] for key in ("id", "revision", "sha256")}, "revision_id": row["id"]}
        )
    return references


def current_sources_match(tx, branch_id, chapters, bindings):
    return len(chapters) == len(bindings) and _bindings(tx, branch_id, chapters) == bindings


def inspection_revalidation(report, preview):
    reviews = []
    for item in report["items"]:
        if previous := item.get("author_review"):
            # The source candidate is historical. A local author can reauthorize the
            # same notes as a native manual review with today's matching basis.
            reviews.append(
                {
                    key: value
                    for key, value in previous.items()
                    if key not in {"source_candidate_id", "candidate_result", "author_overrides_candidate"}
                }
            )
    return {**{key: preview[key] for key in ("scope", "scope_id", "basis_sha256")}, "author_reviews": reviews}


def read_archived_workflow(tx, branch_id, kind, identifier=None):
    return tx.execute(
        "SELECT h.id,h.payload,h.created,s.device_id AS source_device_id "
        "FROM manuscript_workflow_history h LEFT JOIN manuscript_workflow_cloud_sources s "
        "ON h.branch_id=s.branch_id AND h.kind=s.kind AND h.id=s.id WHERE h.branch_id=? AND h.kind=? "
        + ("AND h.id=? " if identifier else "")
        + "ORDER BY h.created DESC,h.id",
        (branch_id, kind, identifier) if identifier else (branch_id, kind),
    )


def archived_workflow(tx, branch_id, kind, identifier):
    return next(iter(read_archived_workflow(tx, branch_id, kind, identifier)), None)


def archive_origin(row):
    if device := row["source_device_id"]:
        return {"origin": "cloud", "source_device_id": device}
    return {"origin": "manuscript_package"}


def list_archived_workflow(tx, branch_id, kind, limit, *, scope=None, scope_id=None):
    args = (branch_id, kind)
    scoped = ""
    if scope:
        scoped = " AND json_extract(h.payload,'$.report.scope')=? AND json_extract(h.payload,'$.report.scope_id') IS ?"
        args += (scope, scope_id)
    return tx.execute(
        "SELECT h.id,h.created,json_extract(h.payload,'$.report.scope') AS scope,"
        "json_extract(h.payload,'$.report.scope_id') AS scope_id,s.device_id AS source_device_id "
        "FROM manuscript_workflow_history h LEFT JOIN manuscript_workflow_cloud_sources s "
        "ON h.branch_id=s.branch_id AND h.kind=s.kind AND h.id=s.id WHERE h.branch_id=? AND h.kind=?"
        + scoped
        + " ORDER BY h.created DESC,h.id LIMIT ?",
        (*args, limit),
    )


def _freeze_native(tx, branch_id, row):
    value = json.loads(row["payload"])
    common = {"id": row["id"], "created": row["created"]}
    if row["kind"] == INSPECTION:
        return ArchivedInspection(
            **common, report=value, chapter_bindings=_bindings(tx, branch_id, value["chapters"])
        )
    return ArchivedCompletion(
        **common,
        **value,
        review=json.loads(row["basis"])["review"],
        chapter_bindings=_bindings(tx, branch_id, value["manifest"]["chapters"]),
    )


def _native_row(tx, branch_id, kind, identifier):
    return tx.execute(
        "SELECT id,kind,payload,basis,created FROM artifacts "
        "WHERE branch_id=? AND status='accepted' AND kind=? AND id=?",
        (branch_id, kind, identifier),
    ).fetchone()


def read_workflow_entry(tx, branch_id, kind, identifier):
    if row := _native_row(tx, branch_id, kind, identifier):
        return _freeze_native(tx, branch_id, row)
    if row := archived_workflow(tx, branch_id, kind, identifier):
        model = ArchivedInspection if kind == INSPECTION else ArchivedCompletion
        return model.model_validate_json(row["payload"])
    return None


def inspection_retains_reference_selection(tx, branch_id, identifier):
    if row := _native_row(tx, branch_id, INSPECTION, identifier):
        return has_reference_selection(json.loads(row["payload"]).get("items"))
    if row := archived_workflow(tx, branch_id, INSPECTION, identifier):
        return has_reference_selection(json.loads(row["payload"]).get("report", {}).get("items"))
    return False


def workflow_ids(tx, branch_id):
    return [
        (sync_kind, kind, row["id"])
        for sync_kind, kind in WORKFLOW_OBJECTS.items()
        for row in tx.execute(
            "SELECT id,created FROM artifacts WHERE branch_id=? AND status='accepted' AND kind=? UNION "
            "SELECT id,created FROM manuscript_workflow_history WHERE branch_id=? AND kind=? "
            "ORDER BY created,id",
            (branch_id, kind, branch_id, kind),
        )
    ]


def observe_workflow_object(tx, branch_id, receipt, entry):
    kind = WORKFLOW_OBJECTS[receipt.object_id.partition(":")[0]]
    other_kind = COMPLETION if kind == INSPECTION else INSPECTION
    if _native_row(tx, branch_id, other_kind, entry.id) or archived_workflow(
        tx, branch_id, other_kind, entry.id
    ):
        raise DomainError("sync_workflow", "体检和交付记录共用了同一来源编号。", 409)
    if existing := read_workflow_entry(tx, branch_id, kind, entry.id):
        if existing != entry:
            raise DomainError("sync_workflow", "同一体检或交付记录编号不能改写。", 409)
        if _native_row(tx, branch_id, kind, entry.id):
            return
    else:
        tx.execute(
            "INSERT INTO manuscript_workflow_history VALUES(?,?,?,?,?)",
            (branch_id, kind, entry.id, entry.model_dump_json(), entry.created),
        )
    tx.execute(
        "INSERT OR IGNORE INTO manuscript_workflow_cloud_sources VALUES(?,?,?,?,?,?)",
        (branch_id, kind, entry.id, receipt.device_id, receipt.change_id, receipt.sequence),
    )


def export_workflow_history(tx, branch_id):
    reports = {
        row["id"]: ArchivedInspection.model_validate_json(row["payload"])
        for row in read_archived_workflow(tx, branch_id, INSPECTION)
    }
    completions = {
        row["id"]: ArchivedCompletion.model_validate_json(row["payload"])
        for row in read_archived_workflow(tx, branch_id, COMPLETION)
    }
    for row in tx.execute(
        "SELECT id,kind,payload,basis,created FROM artifacts WHERE branch_id=? AND status='accepted' "
        "AND kind IN (?,?) ORDER BY created,id",
        (branch_id, INSPECTION, COMPLETION),
    ):
        value = _freeze_native(tx, branch_id, row)
        if row["kind"] == INSPECTION:
            reports[value.id] = value
        else:
            completions[value.id] = value
    if reports or completions:
        return WorkflowHistory(inspections=list(reports.values()), completions=list(completions.values()))
    return None


def import_workflow_history(tx, branch_id, history):
    tx.executemany(
        "INSERT INTO manuscript_workflow_history VALUES(?,?,?,?,?)",
        [
            (branch_id, kind, item.id, item.model_dump_json(), item.created)
            for kind, entries in ((INSPECTION, history.inspections), (COMPLETION, history.completions))
            for item in entries
        ],
    )
