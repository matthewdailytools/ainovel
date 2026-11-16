"""Anonymous deterministic routing for new automatic plan /3; no semantic-quality claim."""

from copy import deepcopy
import json

from ainovel.db import dumps
from ainovel.prompt_encoding import expand_prompt
from ainovel import workflow_plan_recipient_scope as scope
from ainovel import workflow_plan_scope_classification as classification
from helpers.plan_execution_fixture import plan_execution_output
from test_workflow_plan_preparation import PreparationModel


def preservation_output(instruction, *, verdict="pass"):
    def checks(key):
        return [
            {
                "source_id": unit["source_id"],
                "result": verdict,
                "classification_result": verdict,
                "evidence_ids": [unit["source_id"]],
                "reason": "匿名工程替身逐句保持，不代表真实模型语义正确。",
            }
            for unit in instruction[key]
        ]

    return {
        "input_sha256": instruction["input_sha256"],
        "original_checks": checks("全部原始连续单元"),
        "effective_checks": checks("全部有效连续单元"),
        "item_checks": [
            {
                "item_id": item["item_id"],
                "result": verdict,
                "evidence_ids": [instruction["全部有效连续单元"][0]["source_id"]],
                "reason": "匿名逐P工程路由，非真实保持认证。",
            }
            for item in instruction["全部本轮P"]
        ],
        "fixed_checks": [
            {
                "source_id": unit["source_id"],
                "result": verdict,
                "evidence_ids": [unit["source_id"]],
                "reason": "保留原固定项，不提前要求未来兑现。",
            }
            for unit in instruction["全部固定项"]
        ],
        "constraints_check": {"result": verdict, "reason": "原规划及采用约定的工程替身。"},
        "summary": "仅验证接线、完整覆盖和停止保护，不是质量通过。",
    }


class AutoScopeClassificationModel(PreparationModel):
    def __init__(self, *, final_verdict="pass"):
        super().__init__()
        self.final_verdict = final_verdict

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = expand_prompt(json.loads(prompt.split("\n只返回")[0]))
        instruction = frame["任务"]
        action = instruction.get("动作")
        if action == classification.ACTION:
            output = preservation_output(instruction, verdict=self.final_verdict)
        elif action in {scope.ACTION, "写前解释AI工作章纲"}:
            output = plan_execution_output(instruction)
            if (
                action == "写前解释AI工作章纲"
                and instruction.get("接收范围核对执行版本") == scope.NEW_VERSION
            ):
                for item in output["items"]:
                    item.update(
                        scope_binding_status="supported", scope_binding_reason="匿名工程替身同源绑定。"
                    )
        else:
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        self.calls += 1
        self.frames.append(deepcopy(frame))
        return {"text": dumps(output), "usage": {"input": 1, "output": 1}, "call_id": f"auto3-{self.calls}"}
