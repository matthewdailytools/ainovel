"""Offline UI routing fixture, never a model quality evaluator."""

TAG = "source-identity-e2e"
SOURCE = TAG + "：沈砚从渡口一位修船匠借得旧尺，约定后日归还。"
BODY = (
    "陈师傅在修船铺里整理工具，沈砚问过要量哪一块船板，便沿堤向渡口走去。"
    "陈师傅没有给他尺，只叮嘱他把借来的东西收好，别沾上桐油。\n\n"
    "渡口西头的老吴是另一位修船匠，正在给小船捻缝。沈砚等他放下凿子，才开口借一把旧尺。"
    "老吴把旧尺递给沈砚，沈砚接住，检查过边角，又用布把刻线擦清，没有把别人的工具当成自己的。\n\n"
    "两人约定后日归还。老吴说自己这两日都在渡口，沈砚应了一声，记在本子上，才把尺放进工具袋。"
    "回铺子的路上，他没有把借尺说成买尺，也没有说已经归还；陈师傅问起时，他把借出者和归还日说清，随后按原来的安排量船板。"
)


def source_identity_output(instruction):
    action = instruction.get("动作", instruction.get("action"))
    source = instruction.get("情节原话", "")
    units = instruction.get("可引用原文", [])
    if action == "审查正文" and "老吴把旧尺递给沈砚" in instruction.get("正文", ""):
        unit = units[-1]
        return {
            "checks": [
                {"condition": c, "result": "pass", "evidence": unit["source_id"]}
                for c in instruction["固定条件"]
            ],
            "findings": [],
            "summary": "隔离借尺用例，只检验工程调用与来源接线。",
        }
    if action == "从正文提取事实候选与章节摘要" and any("老吴把旧尺递给沈砚" in u["text"] for u in units):
        unit = next(u for u in units if "老吴把旧尺递给沈砚" in u["text"])
        return {
            "summary": "沈砚借得旧尺，归还仍是约定。",
            "facts": [
                {
                    "subject": "沈砚",
                    "kind": "resource",
                    "statement": "从渡口老吴处借到旧尺，尚未归还。",
                    "truth": "fact",
                    "story_time": "本章",
                    "evidence": unit["source_id"],
                }
            ],
        }
    if action == "仅从情节原话补全角色称谓限定":
        labels = []
        for eid, name in instruction["原角色名称表"].items():
            label = "渡口一位修船匠" if name == "修船匠" and "渡口一位修船匠" in source else name
            match = next((u for u in units if label in u["text"]), None)
            labels.append(
                {
                    "entity_id": eid,
                    "label": label if match else "",
                    "status": "supported" if match else "unknown",
                    "evidence": match["source_id"] if match else "",
                    "reason": "隔离界面夹具仅保留原话中的称谓，不是质量判定。",
                }
            )
        return {"labels": labels}
    if action == "独立核对原话角色称谓限定":
        return {
            "checks": [
                {
                    "entity_id": row["entity_id"],
                    "result": "pass" if row["status"] == "supported" else "unknown",
                    "evidence": next(u["source_id"] for u in units if row["label"] in u["text"])
                    if row["status"] == "supported"
                    else "",
                    "reason": "隔离原话核对夹具，不读取候选。",
                }
                for row in instruction["限定提案"]
            ]
        }
    if action in {"阶段批次规划", "全书滚动规划"} and TAG in instruction.get("作品简报", ""):
        return {
            "chapters": [{"number": 1, "title": "借尺", "plot": SOURCE, "fixed": []}],
            "ending": "后续再调查，当前只完成借尺。",
        }
    if (
        (action == "按章纲写完整正文" and TAG in instruction.get("本章", {}).get("plot", ""))
        or (action == "writing.expand_plot" and TAG in instruction.get("作者要求", ""))
        or (action == "按确认参考应用方案原创试写" and TAG in instruction.get("作者情节", ""))
    ):
        return BODY
    if action in {"仅依据作者原话整理本场景情节", "写前解释AI工作章纲"} and any(
        TAG in str(instruction.get(k, "")) for k in ("作者原话", "本章工作章纲（AI规划）")
    ):
        return {
            "items": [
                {
                    "author_evidence": units[0]["source_id"],
                    "actor": "沈砚",
                    "action": "借尺",
                    "object": "旧尺",
                    "direction": "修船匠向沈砚借出",
                    "result": "借得并约定归还",
                    "certainty": "explicit",
                    "transfer_object": "旧尺",
                    "supplier": "修船匠",
                    "recipient": "沈砚",
                    "transfer_scope": "completed",
                    "recipient_scope": "unspecified",
                    "scope_condition": "",
                }
            ],
            "note": "工程夹具刻意保留泛称，检验独立身份限定链路。",
        }
    if action == "逐项对照作者情节解释与本次正文" and "老吴把旧尺递给沈砚" in instruction.get("本次候选", ""):
        unit = next(u for u in units if "老吴把旧尺递给沈砚" in u["text"])
        return {
            "checks": [
                {
                    "item_id": "P01",
                    "result": "pass",
                    "observed": "借得旧尺",
                    "evidence": unit["source_id"],
                    "reason": "仅工程夹具，不代表真实判断。",
                    "repair": "",
                }
            ],
            "summary": "夹具不代替独立身份或作者判断。",
        }
    if action == "只从本次正文独立提取物品与信息流向" and "老吴把旧尺递给沈砚" in instruction.get(
        "本次正文", ""
    ):
        roles = instruction["实体别名对照"]
        unit = next(u for u in units if "老吴把旧尺递给沈砚" in u["text"])
        return {
            "flows": [
                {
                    "object_id": "O01",
                    "supplier_id": next(
                        (eid for eid, name in roles.items() if name == "渡口一位修船匠"), "unknown"
                    ),
                    "recipient_id": next(eid for eid, name in roles.items() if name == "沈砚"),
                    "stage": "completed",
                    "certainty": "explicit",
                    "evidence": unit["source_id"],
                    "note": "有地点限定才能映射，泛称不猜测为师傅或老吴。",
                }
            ],
            "note": "独立观察工程夹具。",
        }
    return None
