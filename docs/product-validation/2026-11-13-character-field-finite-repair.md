# 未采纳人物字段有限修订

日期：2026-11-13。范围：用户已授权的自动化有限修订；后端实现及隔离离线验证。没有操作日常实例、真实数据库、密钥、模型或预算配置；未提交、推送或部署，由主任务统一交付。

本段对应后端阶段，后续工作台、整合验证和日常更新分别记录，不将离线替身结果写成真实模型修订质量通过。

## 已实现的行为

`WorkflowInput.character_field_repair` 是严格布尔值，默认 `false`。旧 payload 缺省与显式 `false` 的 nonce 幂等比较一致；字符串、数字、null 不强转为布尔。旧任务保持原有行为，换账户续跑保留已选择的开关和检查点。CLI RunSpec/config 没有新增默认字段。

开关为 `true` 且采用政策为 `automatic` 时，只修订未采纳、没有作者编辑、来源属于当前或同工作版本 ancestor 自动工作流的新人物 `notes/profile`；人物 `target_id` 必须为空。结构生成时由服务写入内容指纹、生成任务和 attempt，不能仅凭候选修订号为0推断来自AI。没有可信生成标记的旧候选保留人工处理，不追溯盖章。已有正式人物档案、其它 unsafe 结构和需要作者确认的场景替换先停。

独立来源审查的 `unknown/contradicted` 都是有效结果。原候选及报告保持原样，修订模型不改变审查结论。流程为：

> 原候选A0及报告R0 → 新候选A1及独立报告R1 → 必要时A2及独立报告R2 → 仍不支持则等待作者。

每条候选链固定最多两轮，计数不随新候选、Worker attempt 或换账户重置。第一轮失败字段形成冻结白名单，后续只处理白名单内尚未支持的字段；新出现白名单外问题直接停止。正式记录、原稿、身份、别名、来源、依赖及其它字段均原样保留。

修订动作使用独立契约 `character-field-repair/1`，不改变全局提示词版本或独立审查的 `character-field-entailment/2` 语义。模型逐字段返回 `replace/cannot_repair`、旧文本哈希、替换文本、本人物来源编号及理由。空字段、未变化、借用他人来源及常见泛化“未提及/信息不足”替代被拒绝；不能合理按原文修订时明确 `cannot_repair`，不删除字段制造通过。提示同时要求保留原文确实支持的实质信息与未知/猜测边界，不拼造事实。独立复检仍读取本项精确原文。

## 持久化、恢复与费用

- 任务 `result.character_field_repairs[root_artifact_id]` 保存固定上限、初始白名单、来源/约束指纹、当前候选、每轮父子关联、响应、差异、派发attempt及完成步骤元数据。
- 子候选 `basis.character_field_repair` 保存根候选、父候选、轮次、输入哈希和差异；`character_field_origin` 是服务生成来源指纹。
- 派发前持久化轮次；创建新 artifact 与转移 `pending_structure.artifact_id` 使用同一短事务。报告、候选、来源、故事版本、约束、pending、attempt及租约在提交时再次核对。事务中断不留下可误用的孤立候选。
- 子候选独立复检也先持久化派发标记。报告尚未落盘但调用已完整成功时，可复用当前或同链祖先任务中相同契约和输入 token 的完成步骤；格式、完整字段覆盖与本项来源继续验证，报告持久化继续执行原有CAS。
- 完整付费响应包括合法 JSON 围栏形式可恢复。原任务、账户、实际模型、call_id、usage与fingerprint保留；恢复不把原账户费用归到新账户。
- 没有可安全复用的完整结果（包括未知超时、取消或进程窗口中断）时保守停止，不自动重发；已完成但不合格的格式/引用步骤同样保留。两轮语义修订内，沿用已有有限格式/引用校验，不无限重试。
- 继续调用既有 Worker、ModelClient 和 Budget，共享两把 OpenAI Key 的 USD20 与 DeepSeek CNY80 限额，保留历史费用和未知预留。新功能没有独立额度或费用清零路径。预算及上下文预检失败继续保留现场。

暂停后可保存已完整返回的候选，但不会继续派发独立复检；恢复后从该候选继续。作者并发明确采用时，不覆盖作者操作。人工模式不启动自动修订；作者仍可沿既有接口明确采用当前候选，原不支持报告不会被改写为通过。

## 历史候选的界面连接

父稿移交后保持 `status=candidate`，其原 payload/report 不变。后端既有 `maintenance_owner` 保护继续拒绝对失去批次所有权的历史父稿采用；本轮没有新增历史回选/手工修订API，也不解锁旧候选。

界面依据当前非 `continued` 工作流的 `character_field_repairs` 链，把**已经有 `child_artifact_id` 的各轮父稿**标为只读历史，结合 `current_artifact_id`、实际 pending 与候选状态，仅为当前可处理候选提供既有操作。`cannot_repair` 或未知结果停止且没有子稿时，原父稿仍可能是当前待人工处理候选，不能仅因它出现在 `parent_artifact_id` 就禁用。换账户后不要只依据父 artifact 的旧 `task_id` 推断当前归属。

### 工作台整合

已将默认关闭的开关放入整书工作区，只在自动采纳且维护结构时可选；退出该条件会清除勾选。开关纳入表单离开保护、请求签名和幂等键，任务卡显示冻结政策，不受后来表单调整影响。旧任务缺少字段时显示关闭。

结构审阅直接展示每轮说明／档案的修订前后、理由及来源编号，可切换原、上一轮和当前候选。读取任务最近50项不足时，沿创建任务的 `continued_to` 查到当前任务，校验分支、回链及循环；归属无法核对时保留数据并禁用写操作。已产生子稿的父候选只读；当前候选被采用后整链只读；未产生子稿的停止结果保留人工核对入口。所有差异按文本安全渲染。

前端新17项回归与既有28项合并运行：**45 passed**。类型、Vite构建、Prettier及设计审阅页同步检查通过；后端与隔离服务共90文件Ruff与格式检查、既有48项Node回归通过。OpenAPI及前端类型已生成，新策略为可选布尔字段，默认false。

两条新增无头测试经真实UI创建作品、保存独立原稿、选择全自动及自动采纳、勾选修订策略；通过真实API／持久Worker和确定性模型替身验证一轮修订与独立复检、父候选／报告／来源／原稿不变、历史禁采纳，以及cannot_repair后作者明确采用当前候选且原unknown报告不变。320px、200%根字号、面板无横向溢出及键盘Enter导航通过。**2 passed，8.794秒**；没有操作用户浏览器。

随后整个项目无头回归 **28 passed，0 skipped、0 unexpected、0 flaky，138.926秒**，覆盖参考流程、写作与采纳恢复、结构场景、资料、整书及本轮新路径。JSON报告、截图／trace附件和stderr在私有 `finite-repair-full-regression/`；本轮前端构建入口为 `index-BMTLrRbV.js`。28是一次完整执行，不与2项定向重复相加。

首次运行1过1败，原因是测试遗漏实际同时采用场景时的专用成功文案；只修正精确提示预期，未删除人工采纳、报告或原稿断言。首次JSON、截图和trace保留于私有 `mvp-e2e-first-20261113T133918Z/`，复测完整证据在 `mvp-e2e-retest-20261113T134044Z/`。这是测试预期修正，不当作业务修复或真实模型质量改善。

## 实际验证

评测命令额外支持 `--character-field-repair / --no-character-field-repair`，只在新运行显式选择；续跑未指定时继承已冻结选项。选项保存在独立 `workflow_options`，没有扩充旧 `RunSpec.configuration` 或重写既有 payload。旧记录缺少选项一律视为关闭；尝试在旧目录打开策略、payload与策略不符或非布尔输入均在派发前拒绝。响应丢失后沿原 payload 与幂等键恢复。该命令及兼容回归 **23 passed**，Ruff 与格式检查通过；没有由这项检查触发付费调用。

定向命令：

```text
.venv/Scripts/python.exe -m pytest -q backend/tests/test_character_field_repair.py backend/tests/test_character_field_repair_contract.py backend/tests/test_character_review.py backend/tests/test_workflow_structure.py backend/tests/test_budget_provider.py backend/tests/test_workflow_interventions.py
```

实际结果：**117 passed，0 failed、0 errors、0 skipped**；执行墙钟21.806秒（含pytest启动及JUnit输出）。八个本轮后端/测试文件的 Ruff check、Ruff format --check 及差异空白检查均退出0。既有 Starlette/httpx 弃用提示1条，不影响结果。

私有完整日志、JUnit、命令、退出码、耗时及逐文件SHA-256位于：

`~/.ainovel/acceptance/character-evidence-quality/mvp-regression-20261113T132820Z/summary.json`

随后运行整个 `backend/tests`：**659 passed，0 failed、0 errors、0 skipped，230.583秒**。执行前后全部后端 Python 源码 SHA-256 一致；日志、JUnit 及完整源码清单保存在 `~/.ainovel/acceptance/character-evidence-quality/finite-repair-full-regression/`。659 是该源码状态的一次全量结果，不与117项定向或23项评测命令回归相加。

覆盖：正常修订→独立复检→维护、仍unknown两轮停止、原contradicted不被改判、cannot_repair、空/无变化/泛化/错源拒绝、原稿与非白名单字段保持、正式旧人物及旧/编辑候选隔离、跨人物来源边界、来源/brief/候选/attempt/取消晚到、作者并发采用、暂停、原子回滚、付费步骤与独立复检报告落盘前后的崩溃、换账户及围栏JSON恢复、未知结果不重发、初始白名单外问题停止、真实预算适配器加MockTransport在共享预算耗尽时零网络派发。

另有独立只读审查核对两处恢复缺口已修复：围栏响应解析一致；独立复检付费完成但报告尚未持久化时可按同链来源恢复，保留原账户/实际模型。未发现新增阻断；该审查没有运行模型或操作日常实例。

## 交付边界与剩余验证

本轮仅证明隔离替身下的契约、有限编排与数据保护，不证明真实模型能保留全部有支持信息或提升文学质量。常见泛化措辞拦截是保守的确定性检查，不能代替完整语义判断；独立模型支持报告也不等于作者认可或真实质量通过。当前分类评测的真值和旧失败不因本功能改变。

工程全量、前端接入与无头验证已如上完成。显式预算内的真实“可修→复检/不能修→停止”质量评估，以及统一提交、推送、CI和部署核对仍需分别记录；工程通过不追认旧质量失败。没有修改原真实长篇候选、已采用稿或评测输入。
