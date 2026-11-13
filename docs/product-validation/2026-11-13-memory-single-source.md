# 事实提取的单来源证据诊断

日期：2026-11-13。范围：memory 动作的来源契约说明、校验反馈及回归。未调整小说正文、事实数量、来源选择或自动修复次数。

## 真实失败与精确只读诊断

新十章阶段验证已保存前6章，在第7章未采用候选的事实提取阶段停止。该候选2695字符、7905 UTF-8字节，可引用原文共有46个单元，编号 `MEM0001`—`MEM0046`。

模型首条事实使用单个编号，能够正常回填；其余事实把多个合法编号写入同一个字符串，例如 `MEM0003,MEM0005,MEM0008`。该字段不是一个完整来源编号，也不是一段连续逐字原文。首次引用修复只去掉空格，后轮仍保留组合，因此旧精确映射正确拒绝。末轮另有多余根字段 `type`，触发一次格式修复；修复格式后，多编号问题仍在。

原任务、步骤、事件、失败原输出、当前候选及 MEM 来源已冻结在仓库外的该运行私有 `readonly-memory-evidence-audit` 目录。没有覆盖或删除这些失败记录。

四个实际请求的 prompt SHA、semantic SHA、缓存指纹及146755／151213／150969／161608字节大小均精确匹配持久记录；四份无损 wire 展开全部等于原 frame，来源单元不变。四个指纹不同，均有派发与完成步骤，不是缓存误用或正文压缩丢失。

实际上下文仍含36条旧 facts、最近两章和长篇图状态；`longform` 字段单独序列化约105969字节。此大小不是输入 Token 或额外费用数，各字段有重复，不能直接相加。该动作的证据只能来自本次正文。此次组合成员均属于当前 MEM 表，没有发现跨章错引或 wire 被误解的直接证据，不能把上下文体积当成本次根因。

## 最小修复

- memory 指令增加独立契约版本 `memory-single-source/1`，步骤派发元数据记录该版本；动作名称和全局提示版本不变。
- 明确 `evidence` 只返回一个合法 `source_id`；保留现有的连续逐字原文兼容。不接受编号列表、编号范围、概括、改写或其他章节引文。
- 多编号、非法编号及非逐字引文分别给出 `facts[i].evidence` 的精确反馈，附合法单编号示例及本批集合界限，不回显完整正文或不可信引文。
- 复合陈述须由模型回到原文核实支持范围或合理拆分；程序不自动取第一个编号、不拼接非连续引文、不删事实，不用弱证据让整条复合陈述通过。
- 最多6条事实、最多两轮引用修正、格式修复边界、已完成调用缓存及费用保护保持原机制。

本轮没有做上下文投影或裁剪。是否需要仅保留当前正文与版本依据的最小 memory 上下文，应另做有对照的验证，不能凭推测省略资料。

## 验证

确定性替身经过真实 Worker、持久步骤、grounded 修复和 memory 模块候选落地；数据位于 pytest 临时目录，不调用真实模型。

| 场景 | 结果 |
| --- | --- |
| 多编号→精确反馈→模型选单一正确来源 | 正常暂存候选；模型明确选择第二个编号，不是后端盲取第一个 |
| 再次执行已完成的相同提取／模块落地 | 已完成步骤复用，不重复模型请求 |
| 持续逗号列表、带空格列表、编号范围 | 三次有限校验后停止；没有拼接、删事实、正文修改或事实采纳 |
| 错误编号、概括改写、前文引文 | 继续拒绝，反馈有字段位置与合法集合，不回显引文 |
| 合法连续逐字原文 | 原有兼容行为保留 |
| 超过6条事实 | 既有结构契约继续拒绝，未放宽上限 |
| 诊断安全 | 不复制正文、提示词或不可信引文；不修改不合格证据值 |
| 半自动／全自动及作者、题材契约 | 相关完成范围和冻结约定回归保持通过 |

执行两组定向回归：

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/test_memory_evidence.py backend/tests/test_structure_repair.py backend/tests/test_evidence.py backend/tests/test_candidate_context.py -q
.venv/Scripts/python.exe -m pytest backend/tests/test_workflow_scope.py backend/tests/test_author_contract.py::test_both_workflows_keep_independent_contract_through_completion backend/tests/test_genre_integration.py::test_workflows_inherit_profile_through_plan_writing_review_repair_memory_and_completion -q
.venv/Scripts/python.exe -m ruff check backend/ainovel/worker.py backend/ainovel/memory_evidence.py backend/tests/test_memory_evidence.py
.venv/Scripts/python.exe -m ruff format --check backend/ainovel/worker.py backend/ainovel/memory_evidence.py backend/tests/test_memory_evidence.py
```

结果：37项＋23项，合计60项定向回归通过；Ruff、格式、针对性差异与文档链接检查通过。保留已有 Starlette／HTTPX 弃用提示。此次没有重跑完整后端或无头浏览器。

源码：[memory_evidence.py](../../backend/ainovel/memory_evidence.py)、[worker.py](../../backend/ainovel/worker.py)。测试：[test_memory_evidence.py](../../backend/tests/test_memory_evidence.py)。

## 真实恢复与质量边界

本次未修改实际任务、稿件、配置、账本或服务，没有恢复、重试或发起真实模型调用。旧失败产物保留；真实恢复须由主任务明确记录后执行。更精确的提示与离线回归不保证实际模型下次必然修正；来源可定位也不证明陈述本身准确或文学质量通过。
