"""Formal M4 history used by both synchronization and independent manuscript backups."""

from pydantic import Field, model_validator

from .models import Input
from .outline_workspace import OutlineCard, OutlinePlan
from .project_portability import Identifier, check_version_graph
from .settings_portability import DeviceName
from .story_graph import Promise


class OutlineVersion(Input):
    id: Identifier
    parent_id: Identifier | None
    project_version_id: Identifier | None
    settings_version_id: Identifier | None
    plan: OutlinePlan
    reference_cards: list[OutlineCard] = Field(default_factory=list, max_length=1000)
    promise_sources: dict[Identifier, Promise] = Field(default_factory=dict, max_length=250)


class OutlineHistory(Input):
    adopted_revision_id: Identifier | None
    versions: list[OutlineVersion] = Field(min_length=1, max_length=5000)
    source_devices: dict[Identifier, DeviceName] = Field(
        default_factory=dict, max_length=5000, exclude_if=lambda value: not value
    )
    required_story_records: list[Identifier] = Field(
        default_factory=list, max_length=250, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def check_links(self):
        by_id = check_version_graph(self.adopted_revision_id, self.versions, "大纲")
        if not self.source_devices.keys() <= by_id.keys():
            raise ValueError("大纲来源设备关联了包中不存在的修订。")
        selected = by_id.get(self.adopted_revision_id)
        references = {window.promise_id for window in selected.plan.promises} if selected else set()
        required = set(self.required_story_records)
        if (
            len(required) != len(self.required_story_records)
            or not required <= references
            or (selected and not references - selected.promise_sources.keys() <= required)
        ):
            raise ValueError("大纲等待的故事记录须对应采用版的伏笔窗口，且须列出缺少计划种子的依赖。")
        return self
