"""Versioned guidance for an uncertain scene point, without changing its events."""

from .db import DomainError

VERSION = "scene-order-guidance/1"
KEY = "scene_order_guidance_version"
INSTRUCTION = (
    "场景是原文覆盖范围，事件是其中发生的事实；一个场景可能跨多个已知事件时点。"
    "event_refs不是‘列出发生过的事件’的通用类型，它断言整个场景恰好对应同一个精确时点。"
    "覆盖多个不同story_order且无法按原文合理拆分时，应选择order_basis.kind=unknown，"
    "在order_basis.reason解释该范围为什么没有唯一时点，并保留本场景有效的refs。"
    '最小结构形状为{"kind":"unknown","reason":"该范围跨多个已知事件时点，不能当作一个精确点"}；'
    "这个形状仅省略可选refs以示意判别字段，实际结果保留已有有效关联，不复制不存在的编号。"
    "未知的是场景的唯一精确点，不代表其中事件、事实或各自已知的story_order变成未知。"
    "仅在story_time或note写‘未知’而仍选event_refs不能表达这个边界。"
    "不得为了校验把事件story_order改成相同值或null、删事件、删来源或丢弃真实问题；"
    "unknown也不能引用无效、计划、过期或别章事件。"
)
REPAIR_HINT = (
    "请核对order_basis的判别字段：若不能据原文合理拆分成唯一精确时点，"
    "使用order_basis.kind=unknown及order_basis.reason说明真实边界，保留有效refs；"
    "不是仅把story_time或note写未知。场景无唯一点不等于事件本身未知，"
    "保留事件原story_order、事实及来源，不删事件或把顺序改相同来通过；unknown不豁免refs有效性。"
)


def frozen_version(task):
    result = task.get("result", {})
    if KEY not in result:
        return None
    # Pin compatibility rather than importing the module that consumes this hint.
    if result[KEY] != VERSION or result.get("scene_contract_version") != "scene-event-refs/2":
        raise DomainError("scene_guidance_version", "场景时点提示的冻结版本无法确认，未升级旧任务。", 409)
    return VERSION
