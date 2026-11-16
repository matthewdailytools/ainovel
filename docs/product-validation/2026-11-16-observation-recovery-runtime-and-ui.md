# 来源观察恢复与任务输入恢复：e3 日常发布及只读 UI

日期：2026-11-16。源码 `e3ab19e0250b85fe1f72a3d78b935898eb5e61b3`；日常 `127.0.0.1:8766`，主 UI `index-CbKTBuhK.js`。本轮完成正常 V6 发布、两次真实隔离恢复，以及两个独立无头只读入口验证；没有新增模型调用、正文保存或候选采纳。

本文只提升这些具体工程与入口证据。两套新 12 章、真实来源重做、Source2、整书、文学及总投入收益均未因此通过；旧半自动 18／全自动 15 章候选仍未采用。[旧来源引用首停](2026-11-16-observation-citation-stop-and-recovery.md)、[A07 工程与首次失败](2026-11-16-task-input-reference-recovery.md)及既有 742 条历史完整保留。

## 精确版本与发布

- 本地 R7 同一组 427 输入与 7 个 tested dist 保持：2618 后端、745 前端、168 全项目无头、48 Node，以及 API、类型、格式、构建检查通过。不是将不同轮次的计数相加。
- 精确最终提交的正式 REST CI run `43858544263`／job `133269006983` 为 `completed/success`，与源码完整 SHA 一致；没有借用父提交 e1 的 CI。
- 从 Git 对象导出的不可变发布包为 655 个源码／文档文件加 14 个完整 dist，共 669 项；7 个 tested dist 是完整发布的子集，旧 JS 保留。
- 六阶段分开执行。旧 Worker 自然退出；正式 start／verify 使用既有 `restart_gate.py` 包装，没有绕过原生身份／已知例外／未知拒绝守卫。新 API PID 53280、Worker PID 62216 的身份、启动链、8766 listener、实际 UI 与完整 OpenAPI均核对通过。
- 正常发布本轮没有新增失败；旧长路径导出、历史守卫或 UI 工具失败仍留在各自记录。Main、8765、用户修改、index、配置／环境及原件保持，未取消用户任务。

## 备份、双恢复与旧现场

新正式备份 `e80be6dbabe346e0ab813284f3ce999c`，30,976,513 bytes。真实隔离恢复后使用新二进制再次备份并完成第二次真实恢复；两次完整 API、场景与 transcript 读回通过，没有 schema 迁移。隔离生命周期禁用、无 Worker／模型配置，不能将此读回称为日常进程启动。

V8 新鲜快照稳定：主库 40 张非 sqlite 业务表加 `sqlite_sequence` 共 41 表，另 2 张 usage 表；25 份资料原件、272 条章节记录（含空章）、242 任务完整保留。保留旧 36 章及 18／15 候选、半自动第 6 章／自动第 5 章、Source1 未验状态、U02 操作员 r1 与旧 r0、U10 稿件版本及未采纳候选。

536b 已知停点仍为来源语义 `/2`、旧观察执行模板隐式 `/1`：observe 槽位 pending／started 且无 response；完整 15 个归属事件、3 份已返回原始 step、3 笔 settled 调用及 metadata 保留，CNY0.152952 不改。槽位状态不是费用未知。原 e5 的 continued_to、原来源、完整 12 章规划及旧 nonce 查回 536b 均保持；新自动书的原 nonce 仍未 POST、plots 为空。没有把缓存重编号或入口可读当作来源 ready。

外部 master48 由独立 sidecar 在各阶段前后核 SHA，原 47 条及其它字段保持；它不属于 V8 内部。DeepSeek 已结 CNY40.041098、未知 0、余额 CNY39.958902；OpenAI 两账户合计已结 USD17.740313 加历史未知预留 USD1.683170，预算仍分别 CNY80／USD20，不清零。

## 观察恢复入口：仅预览与未知查询

实际 GET 536b 新恢复预览成功：同来源语义 `/2`、旧执行 `/1` 与拟新执行 `/2` 分列，原话、完整 12 章计划及剩余额度 CNY11.580792 保持。默认确认关闭、缺少理由不能发起；没有 POST 新子任务，没有付费或改变旧三帧。

另一个浏览器内合成的“未收到 POST 结果”状态，只用从未 POST 的专用 nonce 做 GET，返回 task:null 并保留原标识。它验证工作台未知状态只查询的交互，**不是**真实供应商未知调用，也没有据此减少历史未知费用。原 e5 查询链仍回到原 536b。

桌面与 320px 实际文字 2 倍、键盘路径完成，Root 已查看桌面及窄屏截图。普通计时器保持；非 GET 尝试 0、发送 0，fatal／pageerror 0，三个自有 context 与 browser 均关闭。目标 API 响应原始字节前后相同，模型／采纳／child 为 0。

## A07 最近任务与原引用：仅本地表单恢复

使用已有 real-epub 测试作品及其最新普通补写任务，不创建替代数据。实际写作页预览唯一原 reference_id，核对当前摘录原文、来源与备注，以及“没有历史资料内容快照”的提示；确认默认关闭。取消后当前表单／资料选择不变；再次 GET 后明确确认，只在自有浏览器恢复原 instruction、fixed、writing_profile 及唯一原引用复选框，尚未生成或采纳正文。

桌面及 320px 实际文字 2 倍与键盘核对完成，Root 已查看预览桌面和恢复后窄屏图。原空正文 r0、原任务／候选／回执／资料及配置等目标 API 的原始字节前后保持；两个自有 context 与 browser 关闭，fatal／pageerror 0，模型／采纳／child 为 0。

正常阅读位置计时器产生 **1 次**精确 owned reading-state PUT 尝试，守卫已 abort、实际发送 0，不能说完全没有写请求尝试或阅读保存成功。没有暂停页面时钟或伪造响应。本例原任务没有选区，不冒称日常正向 UTF-16 选区恢复；工程隔离测试单独留证。当前导入书目／单元目录及 file_hash 可核对，不是重新验证磁盘 EPUB 或重演旧模型上下文。

## 私有付费 runner 的首次准备失败

后续计划独立运行原 R4 的 prepare，绑定 e3 后，在 `guards.session → next_common.start_registry_precheck` 读取 `sys.modules[__name__]` 时发生 `KeyError: '_new_reduced12_runtime_guard'`。原因是私有动态 loader 未登记模块，尚在 API 写操作／新 baseline 之前；run-e3 目录不存在，新增 POST、任务、模型、费用均为 0。

这一项记为 **stability failed**，归属仓库外执行设施；不是已发布产品、模型质量或原稿失败。原 R4／V6 封存不改，另派生 r5 修复；本文截止修复尚待验，不能预先标 passed。两书实际没有继续，Source2 实际没有运行。

## 证据索引

全部原始证据在仓库外 `C:/Users/matth/.ainovel/acceptance/conditional-book-policy-20261115/`；本目录不存真实稿件、原始模型输出或完整提示词。

| 相对私有根目录的文件 | SHA-256／用途 |
| --- | --- |
| `runtime-scene41-next/20261116-afterad-observe-retry-v6/b-e3ab19e.json` | `2b1bcaa1ffc5517e3c07fa729bcf7563500cb6223c0d9619d78b7ff779ec4d19`，精确源码／CI／7tested／完整 release |
| 同目录 `r-e3ab19e/rollout-result.json` | `b52a6cbac084049dead6bf2ecd2845c38083056f5917dc96461e47d0d4c12b04`，正式发布及真实身份 |
| 同目录 `fresh-v8-e3ab19e.json` | `2a35729d51940b28203a917432b640f8ef11c2b4fc56d22d7758a4b1dfe03084`，新鲜旧现场 |
| 同目录 `r-e3ab19e/actual-restore.json` | `674caab34c76f4826f786317c55466ad7cbc8b8e9728a7e7e1d615b3ce3b00fa`，双真实恢复及完整 API |
| 同目录 `root-master48-supplement-e3ab19e.json` | `11d540c2c127a0fcced7833b87920661c4aeaa513c5f9b51896fc051a093602f`，独立 master sidecar；master SHA `bc9463a296f6a94812a9f665f866da55ce9de3fe42c9f9ef1b3cfc9fd4ef96f6` |
| `ci-e3ab19e-43858544263/final-run.json`／`final-jobs.json` | 正式 REST 精确 run／job 终态，与 binding 中摘要对应 |
| `observation-retry-readonly-ui-prepared-20261116/run-e3ab19e/final-summary.json` | `30b9a4b98258ce5a32b9160df078bd17fc4f18d499186b87164e5c7cb469382b`，另有 audit／前后字节清单／截图 |
| `a07-task-input-readonly-ui-prepared-20261116/run-e3ab19e/final-summary.json` | `41934428228d5155fdf0ab92861027f0ad4dbe9515fd8e2791d813b7d09f0a6a`，另有 audit／表单结果／前后字节清单／截图 |
| `reduced12-observation-retry-prepared-20261116/execution-plan-root-e3ab19e.json` | `163a725b9d6adfd54a75f76a9ce67d902c150bea6a2fbce1a2f7c489c31fdee2`，原 R4 实际绑定计划保持 |
| `e3ab19e-pre-release-review/book-r4-prepare-first-failure.json` | `19be4b7010eb628e7cd7d9158495c2dff0279f47d5145749839251df9c54c09f`，prepare loader 首次失败、未新增 case 现场，修复另包 |
| `observation-recovery-runtime-docs-20261116/before742/` 及 `before-proof.json` | 原 742 条和全部相关文档备份；本轮增量与检查 proof 同目录保留 |

## 本次文档验证

本轮链接／敏感信息／diff、历史生成幂等、原 742 条记录及原始字节前缀、12 模块业务与质量状态保持检查通过；追加 6 条后共 748 条。9 项历史后端与 1 项独立 file:// 无头检查通过，实际 320px 字体 2 倍、筛选、键盘与表格滚动留证。显式 Root PYTHONPATH、无 webServer 配置，结果与前后摘要在上述仓库外文档目录；未启动 8877／日常服务或模型，未重跑全项目套件，未暂存／提交／推送。既有依赖弃用及终端颜色 warning 原日志保留。
