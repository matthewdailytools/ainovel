"""Separate an AI plan's stated action from its still-open execution slots.

This is a before-prose interpretation only. It never relaxes an executable
ScopedAuthorPlot, supplies a missing participant or clears invalid evidence.
"""

from .db import DomainError

KEY = "workflow_plan_open_details_version"
VERSION = "workflow-plan-open-details/1"


def enabled(worker):
    return validate_task(worker.task)


def validate_task(task):
    marker = task["result"].get(KEY)
    if marker is None:
        return False
    if (
        marker != VERSION
        or task["kind"] != "workflow"
        or task["payload"].get("mode") != "automatic"
        or task["payload"].get("plot_contract_check") is not True
        or task["result"].get("workflow_plan_preparation_version") != "workflow-plan-preparation/1"
        or task["result"].get("plot_contract_version") != "author-plot-audit/2"
    ):
        raise DomainError("plan_open_details_version", "工作章纲开放细节政策无法确认，未升级旧任务。", 409)
    return True


GUIDANCE = (
    "原话动作明确与执行双方是否齐全是两件事。只在原AI章纲确实未指定的supplier、recipient、transfer_object"
    "列入open_fields，对应字段留空，open_reason说明尚未确定的槽并引用该项原话；不能仅因格式错误就声称原话开放。"
    "已指定的角色、关系或‘另一人’即使没有姓名也须保持，不能换对象。原动作可标explicit，但有开放槽时服务器"
    "只形成ambiguous待细化事项，绝不视为可写正文。可依据原粗章纲明确的上下文解析已指定角色和回指，"
    "但不能把其他事项的人物猜成本项对象，不能从未来正文或期望方向反推；缺字段或缺姓名本身不等于可选槽。"
    "解释阶段只解析原源，不自行选择尚未指定的接收人。"
    "例如‘甲提及备用灯尚未试亮，乙记下缺口’须同时保留提及和乙记下；若原话没有指定提及对象，"
    "recipient留空并列开放槽，不能因此删除传递事项或把未试亮改为试亮。"
    "明确的已交付、尝试、计划阶段和不同双方／物品、只／仅、否定、未知均保留。不得把去送／打算改成已交付。"
    "scope_condition仅填来源中连续逐字的实际条件／时点；没有则空。不能写‘原话未说明’解释，也不能用分号拼接两处原文。"
    "非交换的剧情未知并不是待补槽，不得为明确执行而提前解谜。开放细节只交下一独立选择阶段，非作者新锁定或事实。"
)
