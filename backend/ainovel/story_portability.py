"""Story version and manuscript archive contracts shared with the sync protocol."""

from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from .models import Input
from .project_portability import Identifier
from .settings_portability import DeviceName, SettingsEvidence
from .story_graph import Record

StoryRevisionId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
NativeRevision = Annotated[int, Field(ge=0, strict=True)]
MAX_VERSIONS = 20_000


class StoryEvidence(Input):
    record: list[SettingsEvidence] = Field(default_factory=list, max_length=30)
    stages: list[list[SettingsEvidence]] = Field(default_factory=list, max_length=100)


class StoryVersion(Input):
    id: StoryRevisionId
    parent_id: StoryRevisionId | None
    record_id: Identifier
    archived: bool = Field(strict=True)
    record: Record
    evidence: StoryEvidence
    dependencies: dict[Identifier, StoryRevisionId] = Field(max_length=200)


class NativeStoryEvidence(Input):
    record: list[Identifier] = Field(default_factory=list, max_length=30)
    stages: list[list[Identifier]] = Field(default_factory=list, max_length=100)


class NativeStoryRevision(Input):
    revision: NativeRevision
    version_id: StoryRevisionId
    created: float = Field(ge=0, allow_inf_nan=False)
    dependencies: dict[Identifier, NativeRevision] = Field(max_length=200)
    evidence: NativeStoryEvidence


class PortableStoryArchive(Input):
    adopted_heads: dict[Identifier, StoryRevisionId] = Field(max_length=MAX_VERSIONS)
    versions: list[StoryVersion] = Field(min_length=1, max_length=MAX_VERSIONS)
    source_devices: dict[StoryRevisionId, DeviceName] = Field(default_factory=dict, max_length=MAX_VERSIONS)
    native_history: dict[Identifier, list[NativeStoryRevision]] = Field(max_length=MAX_VERSIONS)

    @model_validator(mode="after")
    def check_history(self):
        versions = {version.id: version for version in self.versions}
        if len(versions) != len(self.versions) or not self.source_devices.keys() <= versions.keys():
            raise ValueError("故事版本编号须唯一，来源设备须关联已有版本。")
        if set(self.adopted_heads) != set(self.native_history):
            raise ValueError("故事采用头与本机历史须逐条对应。")
        if sum(map(len, self.native_history.values())) > MAX_VERSIONS:
            raise ValueError("故事本机历史超出稿件包保存上限。")
        for record_id, history in self.native_history.items():
            if not history or history[-1].version_id != self.adopted_heads[record_id]:
                raise ValueError("故事采用头须与最后一个本机修订相同。")
            if [item.revision for item in history] != list(
                range(history[0].revision, history[0].revision + len(history))
            ):
                raise ValueError("故事本机修订号须连续递增。")
            if any(
                row.version_id not in versions or versions[row.version_id].record_id != record_id
                for row in history
            ):
                raise ValueError("本机故事修订必须属于同一记录。")
        return self
