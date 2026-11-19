"""Exact, scoped source material for rebuilding a portable checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .db import DomainError
from .domain import utf16_slice
from .models import Fact, Input
from .outline_portability import OutlineVersion
from .outline_workspace import _chapter_plans
from .project_brief import KIND as PROJECT_KIND
from .project_portability import Identifier, ProjectVersion
from .reference_portability import Revision as Count
from .settings_portability import SettingsEvidence, SettingsVersion
from .story_portability import MAX_VERSIONS, NativeStoryRevision, StoryRevisionId, StoryVersion
from .workflow_inspection import LengthTargets

if TYPE_CHECKING:
    from .portable_checkpoint import PortableCheckpoint


def checkpoint_error(message, status=409):
    return DomainError("snapshot_source", message, status)


class CheckpointStoryHead(Input):
    revision: Count
    version_id: StoryRevisionId


class CheckpointFact(Input):
    subject: str = Field(min_length=1, max_length=200)
    kind: Literal["event", "knowledge", "capability", "resource", "relationship", "promise", "world"]
    statement: str = Field(min_length=1, max_length=2000)
    truth: Literal["fact", "belief", "uncertain", "plan"]
    story_time: str = Field(max_length=200)
    evidence: SettingsEvidence

    @field_validator("subject", "statement", "story_time")
    @classmethod
    def valid_text(cls, value):
        value.encode("utf-8")
        return value


class CheckpointLengthTarget(Input):
    scope: Literal["chapter", "volume", "book"]
    scope_id: Identifier | None
    targets: LengthTargets | None

    @model_validator(mode="after")
    def check_scope(self):
        if (self.scope == "book") != (self.scope_id is None):
            raise ValueError("篇幅目标范围须对应作品、卷或章。")
        return self


class PortableRestoreSources(Input):
    version: Literal["ainovel-portable-restore-sources/1"]
    outline_live: bool = Field(strict=True)
    story_heads: dict[Identifier, CheckpointStoryHead] = Field(max_length=MAX_VERSIONS)
    facts: list[CheckpointFact] = Field(max_length=MAX_VERSIONS)
    length_targets: list[CheckpointLengthTarget] = Field(max_length=5000)
    scene_spans_for_review: Count


@dataclass(frozen=True)
class ResolvedStoryRevision:
    native: NativeStoryRevision
    version: StoryVersion
    payload: dict


@dataclass(frozen=True)
class ResolvedRestoreSources:
    project: ProjectVersion | None
    settings: SettingsVersion | None
    outline: OutlineVersion | None
    stories: dict[str, list[ResolvedStoryRevision]]
    facts: list[dict]


def _by_version(history):
    return {version.id: version for version in history.versions} if history else {}


class RestoreSourceCatalog:
    """Use the package's verified histories, with exact version heads selected by the checkpoint."""

    def __init__(self, package, revisions=None):
        self.branch_id = package.branch.id
        self.projects = _by_version(package.project_history)
        self.settings = _by_version(package.settings_history)
        self.outlines = _by_version(package.outline_history)
        self.references = _by_version(package.reference_history)
        self.stories = _by_version(package.story_history)
        self.story_history = package.story_history.native_history if package.story_history else {}
        self.story_rows = {
            (record_id, row.revision): row
            for record_id, history in self.story_history.items()
            for row in history
        }
        self.native_revisions = {row.id: row for row in package.revisions}
        self.native_revision_numbers = {(row.chapter_id, row.revision): row for row in package.revisions}
        self.revisions = (
            {**self.native_revisions, **{row.id: row for row in package.source_revisions}}
            if revisions is None
            else revisions
        )

    def capture(self, manifest) -> PortableRestoreSources:
        """Project only formal dependencies; raw workspaces and reference selections stay local."""
        from .story_archive import _bind_native

        if any(value for kind, value in manifest["definitions"].items() if kind != PROJECT_KIND):
            raise checkpoint_error("恢复点含此交换格式尚未支持的正式定义，请使用整库备份恢复。", 409)
        chapters = {row["id"]: row for row in manifest["chapters"]}
        heads = {}
        for record in manifest["records"]:
            key = record["id"], record["revision"]
            native = self.story_rows.get(key)
            version = self.stories.get(native.version_id) if native else None
            if not version or version.archived != bool(record["archived"]):
                raise checkpoint_error("恢复点缺少对应的历史故事记录。", 409)
            payload, _ = _bind_native(version, native, self.revisions.get, self.native_revisions.get)
            if payload != record["payload"]:
                raise checkpoint_error("恢复点的历史故事内容与作品档案不一致。", 409)
            heads[key[0]] = CheckpointStoryHead(revision=key[1], version_id=version.id)
        captured_edges = {
            (edge["record_id"], edge["parent_id"]): edge["parent_revision"]
            for edge in manifest["dependencies"]
        }
        history_edges = {
            (record_id, parent_id): revision
            for record_id, head in heads.items()
            for parent_id, revision in self.story_rows[record_id, head.revision].dependencies.items()
        }
        if captured_edges != history_edges:
            raise checkpoint_error("恢复点的故事依赖与历史修订不一致。")
        facts = []
        for row in manifest["facts"]:
            chapter = chapters.get(row["chapter_id"])
            if row["stale"] or not chapter or chapter["revision"] != row["source_revision"]:
                continue
            facts.append(
                CheckpointFact(
                    **{key: row[key] for key in ("subject", "kind", "statement", "truth", "story_time")},
                    evidence=SettingsEvidence(
                        chapter_id=chapter["id"],
                        revision_id=chapter["revision_id"],
                        start=row["evidence_start"],
                        end=row["evidence_end"],
                        quote_sha256=sha256(row["evidence"].encode("utf-8")).hexdigest(),
                    ),
                )
            )
        return PortableRestoreSources(
            version="ainovel-portable-restore-sources/1",
            outline_live=manifest["outline_live"],
            story_heads=heads,
            facts=facts,
            length_targets=[
                CheckpointLengthTarget(**{key: row[key] for key in ("scope", "scope_id", "targets")})
                for row in manifest.get("length_targets", [])
            ],
            scene_spans_for_review=len(manifest["scene_spans"]),
        )

    def resolve(
        self, checkpoint: PortableCheckpoint, sources: PortableRestoreSources
    ) -> ResolvedRestoreSources:
        """Check all chosen dependencies before a restore transaction may write a branch."""
        try:
            return self._resolve(checkpoint, sources)
        except (DomainError, ValidationError, UnicodeError) as error:
            if isinstance(error, DomainError) and error.code == "snapshot_source":
                raise
            raise checkpoint_error("恢复点所需的正式来源或正文证据无法核验。", 409) from error

    def _resolve(self, checkpoint, sources):
        from .outline_sync import validate_version as validate_outline_version, validate_version_sources
        from .settings_sync import validate_version as validate_settings_version
        from .settings_sync import validate_version_sources as validate_settings_sources

        if checkpoint.branch_id != self.branch_id:
            raise checkpoint_error("恢复点不属于此工作版本。", 409)
        chapters = {row.id: row for row in checkpoint.chapters}
        if len(chapters) != len(checkpoint.chapters):
            raise checkpoint_error("恢复点章节编号重复。", 409)
        for row in chapters.values():
            revision = self.native_revisions.get(row.revision_id)
            if not revision or (revision.chapter_id, revision.revision, revision.sha256) != (
                row.id,
                row.revision,
                row.sha256,
            ):
                raise checkpoint_error("恢复点缺少可恢复的本机章稿修订。", 409)
        project = self.projects.get(checkpoint.shared.project_id)
        settings = self.settings.get(checkpoint.shared.settings_id)
        outline = self.outlines.get(checkpoint.shared.outline_id)
        if (
            (checkpoint.shared.project_id and project is None)
            or (checkpoint.shared.settings_id and settings is None)
            or (checkpoint.shared.outline_id and outline is None)
        ):
            raise checkpoint_error("恢复点缺少正式立项、设定或大纲来源。", 409)
        project_id = project.id if project else None
        if settings:
            if settings.project_version_id != project_id:
                raise checkpoint_error("恢复点设定所用的立项版本不一致。", 409)
            validate_settings_version(settings)
            validate_settings_sources(settings, self.projects.get, self.revisions.get)
            for origin in settings.origins.values():
                if origin.evidence:
                    self.native_setting_source(origin.evidence, chapters)
        if outline:
            validate_outline_version(outline)
            validate_version_sources(
                outline,
                self.projects.get,
                self.settings.get,
                chapters.get,
                reference_lookup=self.references.get,
            )
        if sources.outline_live and (
            not outline
            or outline.project_version_id != project_id
            or (
                outline.settings_version_id is not None
                and outline.settings_version_id != checkpoint.shared.settings_id
            )
        ):
            raise checkpoint_error("恢复点的大纲正式采用依据不一致。", 409)
        self._check_scope(checkpoint, chapters, outline, sources.length_targets)
        stories = self._story_prefix(sources, chapters)
        if sources.outline_live and any(
            window.promise_id not in stories
            or stories[window.promise_id][-1].version.archived
            or stories[window.promise_id][-1].version.record.kind != "promise"
            for window in outline.plan.promises
        ):
            raise checkpoint_error("恢复点缺少大纲正式采用时对应的伏笔记录。", 409)
        return ResolvedRestoreSources(
            project=project,
            settings=settings,
            outline=outline,
            stories=stories,
            facts=[self._resolve_fact(row, chapters) for row in sources.facts],
        )

    def native_setting_source(self, evidence, chapters):
        revision = self.revisions.get(evidence.revision_id)
        chapter = chapters.get(evidence.chapter_id)
        if (
            revision
            and chapter
            and revision.chapter_id == chapter.id
            and revision.revision <= chapter.revision
        ):
            local = self.native_revision_numbers.get((chapter.id, revision.revision))
            if local and local.sha256 == revision.sha256:
                return local
        raise checkpoint_error("恢复点的设定引文缺少确切的本机原稿修订。")

    def _check_scope(self, checkpoint, chapters, outline, length_targets):
        active = [row for row in chapters.values() if not row.archived]
        active_ids = [row.id for row in active]
        positions = [row.position for row in active]
        if positions != sorted(set(positions)):
            raise checkpoint_error("恢复点章节位置重复或顺序不一致。", 409)
        plans = outline.plan.volumes if outline else []
        if checkpoint.scope == "book":
            expected = active_ids
        else:
            volume = next((plan for plan in plans if plan.id == checkpoint.scope_id), None)
            expected = [row.chapter_id for unit in volume.units for row in unit.chapters] if volume else []
            if not expected or not set(expected) <= set(active_ids):
                raise checkpoint_error("恢复点缺少原卷的有效章稿。", 409)
        if checkpoint.scoped_chapter_ids != expected:
            raise checkpoint_error("恢复点范围和旧目录或旧卷纲不一致。", 409)
        if outline:
            planned = [row["chapter_id"] for _, _, row in _chapter_plans(outline.plan.model_dump())]
            if len(planned) != len(set(planned)):
                raise checkpoint_error("恢复点的大纲重复引用章节。", 409)
        scopes = {"chapter": chapters.keys(), "volume": {plan.id for plan in plans}, "book": {None}}
        keys = {(row.scope, row.scope_id) for row in length_targets}
        if len(keys) != len(length_targets) or any(
            identifier not in scopes[kind] for kind, identifier in keys
        ):
            raise checkpoint_error("恢复点的篇幅目标重复或缺少对应范围。", 409)

    def _story_prefix(self, sources, chapters):
        from .story_archive import _bind_native, validate_effective_cycles

        histories = {}
        for record_id, head in sources.story_heads.items():
            row = self.story_rows.get((record_id, head.revision))
            if not row or row.version_id != head.version_id:
                raise checkpoint_error("恢复点缺少准确的历史故事修订。", 409)
            histories[record_id] = [
                item for item in self.story_history[record_id] if item.revision <= head.revision
            ]
        available = {
            (record_id, row.revision): row.version_id for record_id, rows in histories.items() for row in rows
        }
        output = {}
        for record_id, rows in histories.items():
            output[record_id] = resolved = []
            for row in rows:
                version = self.stories.get(row.version_id)
                if (
                    not version
                    or version.record_id != record_id
                    or row.dependencies.keys() != version.dependencies.keys()
                    or any(
                        available.get((parent_id, revision)) != version.dependencies[parent_id]
                        for parent_id, revision in row.dependencies.items()
                    )
                ):
                    raise checkpoint_error("恢复点缺少故事记录所需的旧依赖。", 409)
                for revision_id in [
                    *row.evidence.record,
                    *(i for group in row.evidence.stages for i in group),
                ]:
                    revision = self.native_revisions.get(revision_id)
                    chapter = chapters.get(revision.chapter_id) if revision else None
                    if not chapter or revision.revision > chapter.revision:
                        raise checkpoint_error("故事记录的原文超出恢复点保存的章稿范围。", 409)
                payload, _ = _bind_native(version, row, self.revisions.get, self.native_revisions.get)
                resolved.append(ResolvedStoryRevision(native=row, version=version, payload=payload))
        validate_effective_cycles(
            {record_id: head.version_id for record_id, head in sources.story_heads.items()}, self.stories
        )
        return output

    def _resolve_fact(self, fact, chapters):
        evidence = fact.evidence
        chapter = chapters.get(evidence.chapter_id)
        if not chapter or evidence.revision_id != chapter.revision_id:
            raise checkpoint_error("恢复点事实未引用当时保存的章稿。", 409)
        revision = self.native_revisions[evidence.revision_id]
        _, quote, _ = utf16_slice(revision.content, evidence.start, evidence.end)
        if sha256(quote.encode("utf-8")).hexdigest() != evidence.quote_sha256:
            raise checkpoint_error("恢复点事实的原文摘要不符。", 409)
        data = Fact.model_validate({**fact.model_dump(exclude={"evidence"}), "evidence": quote})
        return {
            **data.model_dump(),
            "chapter_id": chapter.id,
            "source_revision": chapter.revision,
            "evidence_start": evidence.start,
            "evidence_end": evidence.end,
            "stale": 0,
        }
