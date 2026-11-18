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
        by_id = {row.id: row for row in self.versions}
        if len(by_id) != len(self.versions):
            raise ValueError("立项历史的修订编号重复。")
        if self.adopted_revision_id and self.adopted_revision_id not in by_id:
            raise ValueError("稿件包缺少采用的立项修订。")
        proven = set()
        for revision in self.versions:
            path = set()
            cursor = revision.id
            while cursor and cursor not in proven:
                if cursor in path or cursor not in by_id:
                    raise ValueError("立项父修订缺失或形成循环。")
                path.add(cursor)
                cursor = by_id[cursor].parent_id
            proven.update(path)
        return self
