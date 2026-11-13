# 结构提取场景顺序：准确诊断与有限修正

日期：2026-11-13。来源：真实开篇验证的结构提取连续三轮未通过。实际正文、原响应、调用与费用、精确请求重建、来源单元及源码快照均冻结在仓库外的 `source-extraction-diagnostic/`，不将真实稿件写入测试或文档。

## 实际问题与边界

原文编号和UTF-16来源位置均能精确回源，三轮请求、语义提示词及缓存指纹均与持久记录匹配；本次没有wire投影失真或缓存误复用。真正失败的是场景 `story_order`：一组同时覆盖多个实际事件顺序，另外两组把场景排列号当成实际事件顺序。两轮反馈只有重复的通用规则，最终却显示“引用无法回到原文”，不利于定位与合理修正。

本次保持已有门禁：非空 `story_order` 必须等于所覆盖有效实际事件的唯一顺序集合。不会自动清空顺序、选取某个事件、猜测新顺序、重写模型输出或修改原稿。多个实际事件有相同顺序时仍按原政策判断；显式 `null` 和未知说明继续表示未知，没有扩大为默认通过。

## 实现

- `extraction_scenes.scene_issues` 给出具体 `scenes[index].story_order`、当前值、覆盖source_id、关联实际event_id/order及实际顺序集合。没有唯一依据时，要求模型根据原文使用null与未知说明或合理拆分；不生成虚假的“正确值”。大来源/事件列表采用受限摘要，完整原输入仍在模型请求中。
- 结构提取仅在启用场景范围时增加独立 `场景证据契约版本=scene-event-order/1`，说明实际事件顺序与场景／章节编号的区别。保留原伏笔契约版本和Worker全局提示词版本。
- 只为结构提取动作保留最后一轮具体校验问题。最终有限修正失败仍返回原 `DomainError.code=evidence` 和原HTTP状态，错误内容准确显示结构／场景问题。最终摘要最多展示6项、每项800字符，其余保留在原任务修正记录中；不复制完整原文。没有校验问题或其它错误类别时照常抛出原错误。
- 沿用Worker初次校验加两轮修正，未更改全局grounded、费用机制或旧任务。已经正确复原的原文引用在后续修正中仍须按原chapter/revision/UTF-16位置精确匹配，不重新分配编号。

## 定向验证

使用缩减合成原文，包括一字、二字、换行、表情及扩展汉字。新增测试覆盖：同组多事件顺序、同顺序多个事件、错序、没有有效顺序或仅计划事件、输出保持不变、有效场景维护、修正过的引用在后续轮次按精确位置继续有效、三轮调用上限、失败不stage、不改原稿，以及错误码／状态兼容和最终摘要大小。

实际命令：

```text
.venv/Scripts/python.exe -m pytest -q backend/tests/test_extraction_scene_diagnostics.py backend/tests/test_extraction_scenes.py backend/tests/test_story_extraction.py backend/tests/test_story_extraction_promises.py backend/tests/test_story_extraction_append.py
```

结果：**64 passed，11.92秒，退出0**。Ruff check、Ruff format --check、UTF-8 LF及差异空白检查通过。既有Starlette/httpx弃用提示1条。真实失败输入和旧费用不因离线通过而改变；专项阶段没有调用模型、恢复实际任务、修改生产数据、部署或构建。源码与记录由主任务统一提交，现场恢复另行判定。

这次验证证明诊断与确定性保护有效，不表示新的真实模型输出已修好，也不把章节文学质量、连续十章或整书验收判为通过。实际重做必须由主任务按保留的检查点和预算明确执行。

源码`794eb8c`已提交、推送，[远端CI通过](https://github.com/matthewdailytools/ainovel/actions/runs/43438630177)。此状态不表示已恢复原真实任务；日常实例更新和新执行另行记录。
