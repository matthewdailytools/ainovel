"""Give models stable source choices; restore quotations from the frozen source."""

import re
from copy import deepcopy


def source_units(text, prefix, offset=0, group_size=0):
    units = []
    if group_size:
        cursor = 0
        while cursor < len(text):
            end = min(cursor + group_size, len(text))
            if end < len(text):
                boundary = text.rfind("\n", cursor + group_size // 2, end + 1)
                if boundary >= cursor:
                    end = boundary + 1
            quote = text[cursor:end]
            if quote.strip():
                units.append(
                    {
                        "source_id": f"{prefix}{len(units) + 1:04}",
                        "text": quote,
                        "start": offset + len(text[:cursor].encode("utf-16-le")) // 2,
                        "end": offset + len(text[:end].encode("utf-16-le")) // 2,
                    }
                )
            cursor = end
        return units
    for match in re.finditer(r"[^\n]+", text):
        for index in range(match.start(), match.end(), 800):
            end = min(index + 800, match.end())
            quote = text[index:end]
            if not quote.strip():
                continue
            units.append(
                {
                    "source_id": f"{prefix}{len(units) + 1:04}",
                    "text": quote,
                    "start": offset + len(text[:index].encode("utf-16-le")) // 2,
                    "end": offset + len(text[:end].encode("utf-16-le")) // 2,
                }
            )
    return units


def source_schema(schema, units, replacement=None):
    result = deepcopy(schema)
    ids = [unit["source_id"] for unit in units]

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for key, item in node.get("properties", {}).items():
                if key in ("evidence", "quote", "original"):
                    item["enum"] = ids + ([""] if key == "evidence" and not item.get("minLength", 0) else [])
                if key == "replacement" and replacement is not None:
                    if replacement == "delete":
                        item["enum"] = [""]
                    else:
                        item["minLength"], item["maxLength"] = replacement
            for item in node.values():
                visit(item)

    visit(result)
    if replacement is not None:
        result["properties"]["edits"].update(minItems=1, maxItems=1)
    return result


def resolve_field(item, key, sources):
    value = item[key]
    if value in sources:
        item[key] = sources[value]["text"]
        return sources[value]
    return None


def completion_sources(chapters, previous=()):
    """Read each batch in full; aggregate reports with quotations from the same revisions."""
    units = []

    def append(unit, chapter):
        units.append(
            {
                **unit,
                "chapter": chapter["position"],
                "revision": chapter["revision"],
                "chapter_id": chapter["id"],
            }
        )

    for chapter in chapters[-1:] if previous else chapters:
        for unit in source_units(chapter["content"], f"C{chapter['position']:03}F", group_size=600):
            append(unit, chapter)
    reports = deepcopy(list(previous))
    for report in reports:
        for key, field in (("promises", "evidence"), ("findings", "quote")):
            for item in report[key]:
                quote = item[field]
                if not quote:
                    continue
                chapter = next(
                    (
                        c
                        for c in chapters
                        if quote in c["content"] and (key != "findings" or c["position"] == item["chapter"])
                    ),
                    None,
                )
                if not chapter:
                    raise ValueError("Completion report quotation is not in the frozen chapters")
                start = len(chapter["content"][: chapter["content"].find(quote)].encode("utf-16-le")) // 2
                source_id = f"C{chapter['position']:03}R{len(units) + 1:04}"
                append(
                    {
                        "source_id": source_id,
                        "text": quote,
                        "start": start,
                        "end": start + len(quote.encode("utf-16-le")) // 2,
                    },
                    chapter,
                )
                item[field] = source_id
    return units, reports
