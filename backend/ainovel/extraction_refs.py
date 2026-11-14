"""Locate unresolved or mistyped story links without assigning identities."""

from .db import DomainError, dumps

VERSION = "extraction-reference-checks/1"
KEY = "extraction_reference_version"
INSTRUCTION = (
    "participants/from_id/to_id/character_id仅引用character；after/event_id/until_event_id及阶段event_id仅引用event。"
    "depends_on可引用不同类型，所有关联值都用已提供正式id或本批local_id，不用姓名、职务或source_id代替。"
    "同一已确认人物跨章复用标识；同名或同职务不证明同一身份，不把‘是否同一人未知’写进备注后仍合并为确定身份。"
    "匿名参与事实仍须保留：有依据且可区分的新人物可提出独立local_id与本项来源；"
    "无法确定身份或属于群体时，在事件说明和unknowns保留称谓、来源范围及未绑定原因，不能猜测映射到已有个人。"
    "修正只处理指出的关联或字段；保留已有效的事件事实、故事顺序、独立来源及其他记录，"
    "不为通过删掉实际参与事实、改稿、任意合并人物或把已知事件顺序清空。"
)


def frozen_version(task):
    result = task.get("result", {})
    if KEY not in result:
        return None
    if result[KEY] != VERSION:
        raise DomainError("extraction_reference_version", "整理关联核对的冻结版本无法确认，未派发。", 409)
    return VERSION


def fields(record):
    for i, ref in enumerate(record["depends_on"]):
        yield f"depends_on[{i}]", ref, None
    kind = record["kind"]
    if kind == "event":
        for field, expected in (("participants", "character"), ("after", "event")):
            for i, ref in enumerate(record[field]):
                yield f"{field}[{i}]", ref, expected
    elif kind == "state":
        for field, expected in (
            ("character_id", "character"),
            ("event_id", "event"),
            ("until_event_id", "event"),
        ):
            if field != "until_event_id" or record[field]:
                yield field, record[field], expected
    elif kind == "relationship":
        for field, expected in (("from_id", "character"), ("to_id", "character"), ("event_id", "event")):
            if field != "event_id" or record[field]:
                yield field, record[field], expected
    elif kind == "promise":
        for i, stage in enumerate(record["stages"]):
            if stage["event_id"]:
                yield f"stages[{i}].event_id", stage["event_id"], "event"


def label(value):
    # Treat an invalid model ID as data, never as a line of diagnostic text.
    return dumps(value[:120] + ("…" if len(value) > 120 else ""))


def reference_issues(proposal, index, path):
    issues = []
    own = {proposal["local_id"], proposal.get("target_id")} - {None}
    for field, ref, expected in fields(proposal["record"]):
        node = index.get(ref)
        prefix = f"{path}.{field} 值={label(ref)}"
        if ref in own:
            issues.append(prefix + "：记录不能依赖自身；未替换或删除该关联。")
        elif node is None:
            available = [i for i, r in index.items() if expected is None or r["kind"] == expected]
            examples = "、".join(label(i) for i in available[:12]) or "无"
            issues.append(
                prefix + f"：未在本批local_id或已提供正式索引中声明；需要{expected or 'record'}标识。"
                f"当前可用示例（共{len(available)}项）：{examples}。"
                "称谓不能直接当标识，不自动按名字猜测身份；保留参与事实和未知边界。"
            )
        elif expected and node["kind"] != expected:
            issues.append(prefix + f"：实际类型{node['kind']}，需要{expected}；未自动改绑。")
    return issues
