# 批次干预类型过滤与安全内部诊断

日期：2026-11-13。范围：Worker 恢复稳定性。真实故障在第4章结构维护完成、即将首次生成第5章时出现，未进入第5章模型请求。本记录不表示小说质量或整书验收通过。

## 故障与最小修复

作者通过世界设定保存后，使用 `acknowledge_changes` 恢复待维护批次。API 正常记录 `recheck_structure_after_author_changes`，其中只有动作、章节、修订和时间，不含 `artifact_id`。Worker 却将所有 `interventions` 当成拒绝候选读取 `artifact_id`，因此触发 `KeyError`。

修复只调整读取旧正文纠错材料的筛选：

- 旧历史没有 `action` 时保留兼容；新动作只有 `regenerate_pending` 进入该路径。
- 结构重做、作者变更复核、账户等其他干预先跳过，再读取产物，不删除或修改这些历史。
- 没有产物标识的记录跳过，不伪造标识；明确给出但不存在的候选仍正常报告错误，不吞异常。
- 产物必须同时为 `chapter_draft`、已拒绝、属于当前目标章，才作为旧审稿纠错材料。结构产物即使带标识，也不冒充正文。

原有版本保护、有限修订、任务暂停／取消、费用及采纳机制未修改，提示词全局版本未变。

## 安全内部诊断

原生产日志只记录 `KeyError` 类型，无法直接定位行号。现在未知内部异常会额外写入 `internal_error` 事件和本机日志，内容严格限制为：

- 任务标识、执行尝试标识、异常类型。
- 最多8个项目源码帧：`backend/` 或 `scripts/` 下真实文件的相对路径、函数名和行号。

不记录异常消息、局部变量、源代码行、正文、提示词或密钥；不使用输出完整异常的 `logger.exception`。持久事件沿用现有任务尝试条件，不修改无关任务状态。环境及第三方依赖路径不进入这些帧。

## 回归证据

采用确定性模型替身和 pytest 临时数据，实际经过 API、Worker、grounded、结构维护和采纳流程，没有真实模型调用或生产数据操作。

| 场景 | 结果 |
| --- | --- |
| 首章采用后暂停、世界设定实际 API 保存采用、确认变更恢复 | 待维护结构完成后，第二章首次生成成功；首章原文不变，干预历史保留，阶段完成保持正确语义 |
| 无 action 的旧拒绝正文历史 | 仍能提供原有审稿纠错材料，旧失败稿不成为已发生事实 |
| 新 `regenerate_pending` 正文历史 | 仍能提供同章已拒绝正文的审查材料 |
| 带结构产物标识的不同干预 | 已知结构动作在查询前跳过；旧无动作或误归正文动作的结构产物也因 kind 不符而排除 |
| 明确引用不存在的候选 | 停止并保留 `not_found`，不静默忽略或继续生成 |
| 深栈未知异常消息含假密钥和原稿 | 仅持久化8个安全源码帧及允许元数据；任务、事件和日志均不含异常消息原文 |

命令：

```powershell
.venv/Scripts/python.exe -m ruff check backend/ainovel/worker.py backend/tests/test_workflow_interventions.py
.venv/Scripts/python.exe -m ruff format --check backend/ainovel/worker.py backend/tests/test_workflow_interventions.py
.venv/Scripts/python.exe -m pytest backend/tests/test_workflow_interventions.py backend/tests/test_api_worker.py::test_redo_uses_rejected_review_without_treating_old_text_as_story backend/tests/test_workflow_structure.py backend/tests/test_workflow_scope.py -q
```

结果：38项通过，Ruff、格式及针对性差异检查通过。测试保留既有 Starlette／HTTPX 弃用提示，不影响结果。

源码：[worker.py](../../backend/ainovel/worker.py)。专用测试：[test_workflow_interventions.py](../../backend/tests/test_workflow_interventions.py)。

## 现场边界

本次没有改写实际稿件、干预记录或费用账本，没有调用真实模型、恢复任务或重启服务。实际任务恢复仍由主任务在更新源码与运行版本后执行；离线通过不冒充现场恢复成功。完整真实诊断快照与只读复现栈留在该运行的仓库外私有 `readonly-runtime-audit` 目录。
