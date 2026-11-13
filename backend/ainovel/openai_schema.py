"""Conservative wire adaptation; domain schemas and paid semantic caches stay intact."""

from copy import deepcopy

from .db import DomainError

VERSION = "openai-strict-tagged/2"
_ANNOTATIONS = {"title", "description"}
_KEYWORDS = _ANNOTATIONS | {
    "type",
    "$ref",
    "$defs",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "anyOf",
    "oneOf",
    "discriminator",
    "default",
    "enum",
    "const",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
}
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"}


def strict_schema(value):
    """Only disjoint, required string tags justify oneOf -> anyOf equivalence."""

    def reject(path, reason):
        raise DomainError(
            "schema_unsupported",
            f"本机OpenAI适配器不支持此结构契约（{path[:180]}：{reason}）；未发送或预留费用。",
        )

    if not isinstance(value, dict) or value.get("type") != "object" or {"oneOf", "anyOf"} & value.keys():
        reject("$", "根必须为object，不能是联合")
    root = deepcopy(value)

    def resolve(node, path):
        seen = set()
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or (ref != "#" and not ref.startswith("#/$defs/")) or ref in seen:
                reject(path, "引用须可解析为本地$defs，不能是远程或循环别名")
            if set(node) - (_ANNOTATIONS | {"$ref"}):
                reject(path, "$ref不能混合未解析的约束")
            seen.add(ref)
            target = root
            if ref != "#":
                for token in ref[2:].split("/"):
                    # JSON Pointer escapes, not URL or external-document resolution.
                    if "~" in token.replace("~0", "").replace("~1", ""):
                        reject(path, "引用包含无效JSON Pointer转义")
                    token = token.replace("~1", "/").replace("~0", "~")
                    if not isinstance(target, dict) or token not in target:
                        reject(path, "本地引用不存在")
                    target = target[token]
            node = target
        if not isinstance(node, dict):
            reject(path, "引用目标不是schema对象")
        return node

    def prove_union(node, path):
        discriminator = node.get("discriminator")
        if not isinstance(discriminator, dict) or set(discriminator) - {"propertyName", "mapping"}:
            reject(path, "oneOf缺少可核验discriminator")
        tag = discriminator.get("propertyName")
        branches = node["oneOf"]
        if not isinstance(tag, str) or not tag or not isinstance(branches, list) or len(branches) < 2:
            reject(path, "oneOf须有标签及至少两个分支")
        tags = {}
        for index, branch in enumerate(branches):
            target = resolve(branch, f"{path}.oneOf[{index}]")
            required = target.get("required", [])
            if target.get("type") != "object" or not isinstance(required, list) or tag not in required:
                reject(path, "分支须为对象且显式要求标签字段")
            properties = target.get("properties", {})
            tagged = resolve(properties.get(tag), path) if isinstance(properties, dict) else {}
            choices = tagged.get("enum")
            if "const" in tagged:
                constant = tagged["const"]
                if choices is not None and (not isinstance(choices, list) or constant not in choices):
                    reject(path, "标签const与enum不一致")
                choices = [constant]
            if (
                tagged.get("type") != "string"
                or not isinstance(choices, list)
                or not choices
                or any(not isinstance(choice, str) for choice in choices)
            ):
                reject(path, "标签须为非空的string const或enum集合")
            for choice in choices:
                if choice in tags:
                    reject(path, "标签集合重叠，不能将oneOf放宽")
                tags[choice] = target
        mapping = discriminator.get("mapping")
        if mapping is not None:
            if not isinstance(mapping, dict) or set(mapping) != set(tags):
                reject(path, "discriminator映射与标签不一致")
            for tag, ref in mapping.items():
                if resolve({"$ref": ref}, path) is not tags[tag]:
                    reject(path, "discriminator映射与分支不一致")

    def visit(node, path):
        if not isinstance(node, dict):
            reject(path, "schema节点须为对象")
        unsupported = set(node) - _KEYWORDS
        if unsupported:
            reject(path, "不支持关键字" + ",".join(sorted(unsupported)))
        if "$ref" in node:
            resolve(node, path)
        if "oneOf" in node:
            if "anyOf" in node:
                reject(path, "不能混合两个联合")
            prove_union(node, path)
        elif "discriminator" in node:
            reject(path, "discriminator缺少受支持的tagged oneOf")
        kind = node.get("type")
        if isinstance(kind, list):
            if (
                len(kind) != 2
                or any(not isinstance(item, str) for item in kind)
                or "null" not in kind
                or len(set(kind)) != 2
                or not set(kind).issubset(_TYPES)
            ):
                reject(path, "type列表目前仅支持明确的nullable类型")
            effective_kind = next(item for item in kind if item != "null")
        else:
            if kind is not None and (not isinstance(kind, str) or kind not in _TYPES):
                reject(path, "不支持的type")
            effective_kind = kind
        if not any(key in node for key in ("type", "$ref", "anyOf", "oneOf")):
            reject(path, "缺少明确type或引用")
        if "format" in node and (not isinstance(node["format"], str) or node["format"] not in _FORMATS):
            reject(path, "不支持的format")
        result = {
            key: deepcopy(item) for key, item in node.items() if key not in {"default", "discriminator"}
        }
        for key in ("$defs", "properties"):
            if key in node:
                if not isinstance(node[key], dict):
                    reject(path, f"{key}须为名称映射")
                result[key] = {name: visit(item, f"{path}.{key}.{name}") for name, item in node[key].items()}
        if effective_kind == "object":
            if node.get("additionalProperties", False) is not False:
                reject(path, "仅支持明确字段，不能丢弃额外属性约束")
            result["required"] = list(result.get("properties", {}))
            result["additionalProperties"] = False
        elif "properties" in node or "additionalProperties" in node or "required" in node:
            reject(path, "对象字段须明确object类型")
        if effective_kind == "array" and "items" not in node:
            reject(path, "数组须有items契约")
        if "items" in node:
            result["items"] = visit(node["items"], path + ".items")
        for key in ("oneOf", "anyOf"):
            if key in node:
                if not isinstance(node[key], list) or not node[key]:
                    reject(path, f"{key}须为非空分支列表")
                converted = "anyOf" if key == "oneOf" else key
                result.pop(key, None)
                result[converted] = [
                    visit(item, f"{path}.{key}[{index}]") for index, item in enumerate(node[key])
                ]
        return result

    return visit(root, "$")
