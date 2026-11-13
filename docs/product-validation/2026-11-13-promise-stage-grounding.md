# 伏笔阶段证据前置校验

日期：2026-11-13。范围：结构反向整理与自动维护的确定性稳定性修复。本记录不代表小说文学质量、作者盲审或整书验收通过。

## 真实触发与原因

连续十章验证在第6章的结构维护阶段停止：新伏笔的主 `record.sources` 有原文，但 `planted` 阶段自身的 `sources` 为空。旧提取校验把主来源与阶段来源合并，只要总集合非空便通过；结果先被暂存，随后领域采纳校验以“埋设、强化和回收须分别关联原文”拒绝，有限引用修复没有机会收到这项错误。

这是提取早检与已有领域规则不一致，属于功能稳定性阻断；不能通过补造引文、把主来源自动塞入阶段，或降低领域采纳条件解决。

## 修复行为

- 在 `run_extraction` 的 grounded 校验内，逐项检查 `planted`／`reinforced`／`resolved` 的 `stages[i].sources`；主 `sources` 非空不能代替本阶段证据。
- 同步前置已有的阶段规则：`retained`／`abandoned` 的理由非空；阶段 `event_id` 若填写，只能关联本批或正式索引中的事件。不额外要求这些事件的发生状态，也不推断新业务条件。
- 错误反馈包含候选下标、`local_id`、阶段下标、阶段状态和出错字段，并提供合法 `source_id` 示例；完整合法来源仍由冻结的原文单元列表提供。引用 ID 非法时也明确指出该阶段的来源位置。
- 不自动补写阶段来源。同段原文确实支持多个阶段时，模型可显式选用同一个来源编号；要求的是各阶段分别有依据，不要求来源编号互不相同。
- 仅更新部分字段且未选择 `stages` 时，正式阶段历史保持原样，避免因不会写入的提案字段误拦截。最终采纳仍执行既有领域校验和原子事务。
- 提取动作携带独立的 `阶段证据契约版本 = promise-stage-grounding/1`，未改 Worker 全局提示版本。原有最多两轮引用修复、已完成调用缓存、费用规则和正文保护保持原行为。

领域 promise 校验位于依赖数据库图的 `validate_record` 内。本次按限定范围只改提取模块，没有抽取或改动领域模块；通过实际领域保存、采纳与自动维护测试，核对早检条件与现行规则一致。

## 验证方法与证据

专用替身测试使用真实 Worker 的 grounded 循环、持久步骤、结构维护与采纳链路，不调用外部模型。所有数据位于 pytest 隔离临时目录。

| 用例 | 预期与已验证行为 |
| --- | --- |
| 首轮阶段缺证据，主来源非空 | 收到精确阶段定位和合法原文编号后修复；合法结果才暂存并正常维护，正文不变 |
| planted／reinforced／resolved | 三种阶段分别执行相同证据要求 |
| 阶段引用编号非法 | 精确指出阶段来源字段，修复后逐字恢复原文位置 |
| 阶段引用人物或未知事件 | 收到事件类型／标识反馈，改为合法事件后正常维护 |
| 始终缺阶段证据 | 三个有限校验轮次后停止；相同修复请求复用完成步骤，只有两个独立替身调用；没有结构产物、正式记录或场景写入，正文不变 |
| 多阶段只缺其中部分证据 | 分别指出缺失阶段，不因主来源或其他阶段非空而放行，也不修改输入 |
| 同段明确支持多个阶段 | 可显式重复引用同一来源，不新增来源唯一性业务限制 |
| 保留／放弃理由及计划阶段 | 与既有领域保存规则一致；计划阶段不被强行要求实际埋设证据 |
| 更新未选择 stages | 保留正式阶段证据，正常采纳，无虚假修复调用 |

命令：

```powershell
.venv/Scripts/python.exe -m ruff check backend/ainovel/story_extraction.py backend/tests/test_story_extraction_promises.py
.venv/Scripts/python.exe -m ruff format --check backend/ainovel/story_extraction.py backend/tests/test_story_extraction_promises.py
.venv/Scripts/python.exe -m pytest backend/tests/test_story_extraction_promises.py backend/tests/test_story_extraction.py backend/tests/test_extraction_scenes.py backend/tests/test_workflow_structure.py -q
```

结果：51项通过。Ruff、格式与针对性差异检查通过。测试环境保留已有的 Starlette／HTTPX 弃用提示，不影响本轮结果。

源码与专用测试：[story_extraction.py](../../backend/ainovel/story_extraction.py)、[test_story_extraction_promises.py](../../backend/tests/test_story_extraction_promises.py)。

## 现场恢复边界

本次没有调用真实模型、读写实际稿件数据库、修改账户配置、调整单价或账本、重启运行服务。原失败候选保留；真实第6章维护恢复须由主任务在更新运行版本后，明确记录原因并重做同一待维护结构候选。该恢复在本文编写时尚未执行，离线替身通过不能冒充现场恢复或创作质量通过。
