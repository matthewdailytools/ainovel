"""Compact extraction source choices; full evidence is restored from frozen text."""

from copy import deepcopy

from pydantic import Field

from .db import DomainError
from .story_graph import Source, Strict

VERSION = "extraction-source-ids/1"
KEY = "extraction_source_output_version"
INSTRUCTION = (
    '每个record.sources和promise.stages[i].sources项只填{"quote":"合法source_id"}。'
    "不重复输出chapter_id、revision、start或end；服务只从本次冻结原文恢复这些字段和逐字引文。"
    "全部所选原文及编号仍可读，没有裁剪来源范围。保留有依据的记录、阶段、场景和未知边界，"
    "不为精简删除事实、来源或真实问题，不缩短原文或改造情节。"
)


class SourceChoice(Strict):
    quote: str = Field(min_length=1, max_length=4000)


def frozen_version(task):
    result = task.get("result", {})
    if KEY not in result:
        return None
    if result[KEY] != VERSION:
        raise DomainError("extraction_source_version", "整理来源输出的冻结版本无法确认，未派发。", 409)
    return VERSION


class SourceIdSchema:
    """Small model boundary, with the unchanged domain model as final validator.

    Full legacy source objects remain readable (including previous repair rounds).
    Their computed fields are still checked/restored by the existing evidence
    validator. Only an explicit source choice can receive server-owned positions.
    """

    def __init__(self, schema, units):
        self.schema = schema
        self.index = {unit["source_id"]: unit for unit in units}
        self.__name__ = "SourceId" + schema.__name__

    def model_json_schema(self):
        result = deepcopy(self.schema.model_json_schema())
        result["$defs"]["Source"] = SourceChoice.model_json_schema()
        return result

    def model_validate(self, value):
        value = deepcopy(value)
        if isinstance(value, dict) and isinstance(value.get("records"), list):
            for proposal in value["records"]:
                record = proposal.get("record") if isinstance(proposal, dict) else None
                if not isinstance(record, dict):
                    continue
                self.restore(record)
                if record.get("kind") == "promise" and isinstance(record.get("stages"), list):
                    for stage in record["stages"]:
                        if isinstance(stage, dict):
                            self.restore(stage)
        return self.schema.model_validate(value)

    def restore(self, owner):
        if not isinstance(owner.get("sources"), list):
            return
        sources = []
        for source in owner["sources"]:
            if isinstance(source, dict) and set(source) == {"quote"}:
                choice = SourceChoice.model_validate(source)
                unit = self.index.get(choice.quote)
                # Unresolvable choices reach the normal evidence diagnostics;
                # they can never acquire a guessed source or become adoptable.
                sources.append(
                    {
                        **{k: unit[k] for k in ("chapter_id", "revision", "start", "end")},
                        "quote": unit["text"],
                    }
                    if unit
                    else {"chapter_id": "", "revision": 0, "start": 0, "end": 1, "quote": choice.quote}
                )
            else:
                sources.append(Source.model_validate(source).model_dump())
        owner["sources"] = sources
