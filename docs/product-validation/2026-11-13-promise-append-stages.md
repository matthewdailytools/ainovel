# 伏笔生命周期的显式阶段追加

日期：2026-11-13。范围：既有结构提取、伏笔生命周期和自动维护。未新增导航或续章功能，也不代表创作质量验收通过。

## 已观察到的缺口

提取动作只给模型正式记录索引，没有发送伏笔的完整旧阶段和旧引文。此前更新 `stages` 只有整字段替换语义，而自动维护只接受完整保留旧阶段前缀的推进。模型若仅输出本批回收阶段，便会替换旧史；若试图重建旧史，又无法可靠恢复没有发送的来源。要求模型猜历史无法解决这项操作契约缺口。

## 明确追加操作

`ProposedRecord` 增加严格布尔字段 `append_stages`，缺省为 `false`。

- `false` 或旧请求未填写：保持原有 `update_fields` 整字段替换语义及审核政策。
- `true`：必须指定当前正式 `promise` 的 `target_id`、`target_revision`，并在 `update_fields` 中选择 `stages`。`record.stages` 只包含本批新增且有本批原文证据的阶段；程序以冻结旧记录的完整 `old.stages + new.stages` 合并。
- 该追加操作仅接受 `planted`／`reinforced`／`resolved`；不借追加模式生成计划或自动决定保留、放弃。已有的明确作者决定仍沿用既有编辑和审核流程。
- 旧阶段、旧来源原句及其章节修订保持原值，并保留 `previous_record` 与正式记录历史。阶段新增仍使用逐项证据前置校验，不借用主来源自动补齐。
- 正式索引只新增伏笔当前状态和阶段数，不发送完整旧阶段及旧引用历史。
- 合并后超过100阶段时，在 grounded 校验中明确反馈并有限停止，不截断旧史。主来源合并仍沿用原有30条保护，超过时明确停止。
- 主 `sources` 的语义保持：未列入 `update_fields` 时追加去重证据，明确列入时仍是替换提案；移除旧证据的提案不能借阶段追加绕过原有自动采用检查。

提取动作的独立 `阶段证据契约版本` 更新为 `promise-stage-grounding/2`。Worker 全局提示版本、采纳事务、幂等回执、费用规则及正文保护未修改。`safe_automatic` 继续使用原有“旧阶段完整前缀＋证据保留”的判断，无需放宽。

## 验证

测试使用实际 Worker、grounded 校验、持久步骤、自动维护与采纳事务，模型由确定性替身提供。数据只存 pytest 隔离临时目录，无真实模型调用或生产数据库操作。

| 场景 | 结果 |
| --- | --- |
| 同一伏笔从埋设到强化再到回收 | 正式身份不变，修订0→1→2；旧阶段前缀、旧来源及历史记录完整保留；正文不改 |
| 模型仅获得必要索引 | 可读当前状态与阶段数，未发送旧阶段及旧原文也能正确推进 |
| 新记录／错误类型／未选择 stages | 明确拒绝追加，有限修复停止，没有暂存产物或历史变更 |
| 新阶段引用旧批次原文 | 反馈具体阶段和合法本批来源编号，不接受把旧来源伪装成本批依据 |
| 已有100阶段再追加 | 明确报告101超过100，不删除旧阶段，不暂存结果 |
| 追加计划、保留或放弃 | 操作边界拒绝，不通过自动阶段追加替作者决定 |
| 旧引文被隔离故障样本伪造 | 最终领域逐字校验仍拒绝，采纳事务回滚，旧记录与历史未被重写 |
| 旧来源章节已换修订 | 仍报告来源修订冲突，不能通过新增有效来源掩盖失效旧来源 |
| 未填写 append_stages 的旧提案 | 仍是整阶段字段替换；先前需人工处理的历史改写不会变成自动追加 |
| 明确替换主 sources | 保持原替换语义及人工审核边界，阶段追加不会扩大自动权限 |

执行：

```powershell
.venv/Scripts/python.exe -m ruff check backend/ainovel/story_extraction.py backend/tests/test_story_extraction_append.py backend/tests/test_story_extraction_promises.py
.venv/Scripts/python.exe -m ruff format --check backend/ainovel/story_extraction.py backend/tests/test_story_extraction_append.py backend/tests/test_story_extraction_promises.py
.venv/Scripts/python.exe -m pytest backend/tests/test_story_extraction_append.py backend/tests/test_story_extraction_promises.py backend/tests/test_story_extraction.py backend/tests/test_extraction_scenes.py backend/tests/test_workflow_structure.py -q
```

结果：63项通过；Ruff、格式及针对性差异检查通过。保留已有 Starlette／HTTPX 弃用提示，不影响本轮结果。

源码：[story_extraction.py](../../backend/ainovel/story_extraction.py)。专用测试：[test_story_extraction_append.py](../../backend/tests/test_story_extraction_append.py)。前置阶段证据修复记录：[promise-stage-grounding](2026-11-13-promise-stage-grounding.md)。

## 剩余限制

是否真的埋设、强化或回收仍需根据段落语义审阅；机械来源有效不等于推理正确。真实运行恢复与实际模型产出质量尚未在本轮执行。模型不能依靠简要索引改写旧历史；需要修正历史或超过限制时，应保留现场交由作者处理。
