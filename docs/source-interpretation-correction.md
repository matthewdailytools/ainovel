# 显式半自动来源解释纠偏

`source-interpretation-correction/1`仅处理当前半自动待审候选中原解释的主体回指、过度主体限定或文本指令误分。原P编号、引用、动作、对象、方向、结果、数值、阶段和只/仅/时点保持；原情节真正存在多解时仍待核对。它不会重新创作情节、选择新人名或修改正文。

## API

1. `GET /api/artifacts/{candidate_id}/source-interpretation-correction-preview`返回原情节、全部原P项、候选修订和`basis_sha256`。
2. `POST /api/artifacts/{candidate_id}/source-interpretation-corrections`创建独立任务，必填`candidate_revision`、`preview_basis_sha256`、`account`、`client_request_id`、`item_ids`；可填`budget_limit`、`reason`，`source_identity_context`仅允许false。提交标识8—128字符；预算须正数。
3. `GET /api/artifacts/{candidate_id}/source-interpretation-corrections/{client_request_id}`按原提交标识查询任务，提交结果未知时先查询，不更换标识盲发。
4. 成功任务的`result.artifact_id`指向只读来源回执，沿用`GET /api/artifacts/{id}`读取。`payload.state`仅在全部事项明确、逐P及固定条件保持均通过时为`ready`，其余为`unverified`。
5. 仅ready回执可用于`GET /api/artifacts/{candidate_id}/recheck-source-correction-preview?receipt_id={id}`。该预览同样返回`basis_sha256`。
6. `POST /api/artifacts/{candidate_id}/recheck-source-correction`另行创建`source_corrected_candidate_recheck`任务，政策`source-corrected-candidate-recheck/1`；必填`candidate_revision`、`preview_basis_sha256`、`receipt_id`、`account`和`client_request_id`，可填正数`budget_limit`，`source_identity_context`仅允许false。`GET`同路径加`/{client_request_id}`查询幂等结果。

两类POST都返回202及标准Task结构。预览基线覆盖候选完整payload、原情节与解释、源任务payload/progress、故事修订与采用配置；正文复检还覆盖来源回执完整payload。同修订号的审稿/解释变化也使旧基线409，须保留输入、重新读取并显式使用新提交标识。瞬时费用余额不加入基线；选择的P作用域单独显式提交，预览基线覆盖全部原P。

两种报告的accept、edit和reject接口均拒绝。旧`candidate-recheck/1`继续只用原解释，其请求与行为不升级；全自动写前重做也不能用于已有半自动候选。

## 模型与持久化边界

第一阶段只发送原章情节、旧P解释及允许修正范围；第二阶段独立逐P核对并另外核对原固定条件是否保持。两个来源阶段都不接收候选正文、正文摘要、旧review/check、自由reason、全书brief或后续章纲。固定条件仅作解释保持约束，不能证明本段actor；其未来节点尚未兑现不构成来源保持unknown。未知母亲去向与后章安排照原样保留。

提案仅包含actor、statement_kind、certainty、解释理由和来源引用；所有不可变事件字段由服务器复制。文本指令须有原话说明类指令支持，不能把实际测量、交付或角色选择改分类来消除未知。阶段一和独立核对都保留带UTF-16出处的引用。空actor不是默认明确：已选纠偏项把ambiguous改成explicit或更改actor后仍未知时须停止；未选、未改的合法环境事项可保持原explicit与空actor。事项性质、理由和引用留在独立proposal，核心items保持旧ScopedAuthorPlot格式；显式新正文核对把元数据另存plot_audit.source_correction，作者再次编辑后仍可读取原契约复检。

来源纠偏只产生独立回执，原候选、旧解释、旧检查及原工作任务保持。之后的显式正文复检只更换候选检查及记忆候选并保留完整旧payload事件，不改正文、候选修订、已保存稿或原批次状态，不自动采纳或恢复。来源、目标、回执和配置在每次派发、调用返回及发布事务内重新核对。

新任务仍计入原服务商共享账本，调用上限受停止工作任务的剩余额度及关联纠偏/复检已用费用约束；旧已付调用及步骤缓存不清空。已派发而无完整回执的同一步骤不盲重发，完整步骤恢复可复用缓存。仅真实适配器在HTTP前校验/预算预留分支给出的RequestNotSent证据，可为最后一次逻辑派发记录step_not_sent并允许显式恢复；无ledger或usage为0不是未发送证明，已发送后缺回执仍阻断。有本族辅助任务时，原workflow及其续跑也扣除这些已付辅助费用，原payload、历史步骤和账本不改写。

## 验证

`uv run pytest backend/tests/test_source_interpretation_correction.py`运行隔离替身用例，包含本例三处解释纠偏、真实角色选择、不可变字段、来源/候选/配置/回执竞态、同修订报告变化、固定条件保持与当前履约分离、只读接口、幂等、预算及未知步骤恢复。旧候选复检回归仍需执行。替身测试不代表真实模型文学质量或作者认可。
