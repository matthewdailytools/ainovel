"""Version identities shared by M1 synchronization and portable backups."""

from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from .models import Input
from .project_brief import IdeaText, ProjectBrief

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")]


class ProjectVersion(Input):
    id: Identifier
    parent_id: Identifier | None
    idea: IdeaText
    brief: ProjectBrief


class ProjectHistory(Input):
    adopted_revision_id: Identifier | None
    versions: list[ProjectVersion] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def check_links(self):
        check_version_graph(self.adopted_revision_id, self.versions, "立项")
        return self


def check_version_graph(adopted_revision_id, versions, label):
    by_id = {row.id: row for row in versions}
    if len(by_id) != len(versions):
        raise ValueError(f"{label}历史的修订编号重复。")
    if adopted_revision_id and adopted_revision_id not in by_id:
        raise ValueError(f"稿件包缺少采用的{label}修订。")
    proven = set()
    for revision in versions:
        path = set()
        cursor = revision.id
        while cursor and cursor not in proven:
            if cursor in path or cursor not in by_id:
                raise ValueError(f"{label}父修订缺失或形成循环。")
            path.add(cursor)
            cursor = by_id[cursor].parent_id
        proven.update(path)
    return by_id
