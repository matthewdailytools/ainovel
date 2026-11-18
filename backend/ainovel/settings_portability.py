"""M3 object schemas shared by cloud synchronization and manuscript packages."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .conception import BUILTINS, ConceptionCategory
from .models import Input
from .project_portability import Identifier, check_version_graph

DeviceName = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class SettingsEvidence(Input):
    chapter_id: Identifier
    revision_id: Identifier
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    quote_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SettingsOrigin(Input):
    origin: Literal["author", "ai"]
    edited: bool = Field(default=False, strict=True)
    artifact_id: Identifier | None = None
    device_id: DeviceName | None = None
    project_version_id: Identifier | None = None
    evidence: SettingsEvidence | None = None


class SettingsVersion(Input):
    id: Identifier
    parent_id: Identifier | None
    project_version_id: Identifier | None
    categories: list[ConceptionCategory] = Field(min_length=len(BUILTINS), max_length=24)
    origins: dict[Identifier, SettingsOrigin] = Field(max_length=240)
    note: str = Field(default="", max_length=4000)
    reconciled_project_items: list[Identifier] = Field(default_factory=list, max_length=240)
    reviewed_setting_findings: list[Identifier] = Field(default_factory=list, max_length=7440)


class SettingsHistory(Input):
    adopted_revision_id: Identifier | None
    versions: list[SettingsVersion] = Field(min_length=1, max_length=5000)
    source_devices: dict[Identifier, DeviceName] = Field(
        default_factory=dict, max_length=5000, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def check_links(self):
        by_id = check_version_graph(self.adopted_revision_id, self.versions, "设定")
        if not self.source_devices.keys() <= by_id.keys():
            raise ValueError("设定来源设备关联了包中不存在的修订。")
        return self
