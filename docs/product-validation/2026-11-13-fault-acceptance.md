# 全项目故障验收逐项结案表

日期：2026-11-13。既有代码证据核对截至 `a8131b4`；其后补充本轮迁移原子性修复与故障回归，提交号待合并记录。本轮只使用临时数据，没有调用模型、写日常数据库或重启服务。

结论：已执行的确定性保护用例通过，包含真实 Worker 进程终止与恢复、迁移中途 SQL/回填错误的完整事务回滚、SQLite 恢复复制中断保护；**原计划故障范围尚不能整体标为全部通过**。真实系统输入法人工验收、生成中切目标的完整浏览器时序等仍未执行，详见下表。工程保护通过不替代真实模型产出、长篇连续性或作者审美验收。

## 证据与版本口径

| 编号 | 已执行证据 | 版本与边界 |
| --- | --- | --- |
| B1 | 全量后端 **404 项通过，176.46 秒** | `6f2d9cd`。覆盖该版本中下列后端用例；不将其算作后续修改的全量结果。 |
| B2 | 格式修复新增 5 项，加 API/Worker/候选关键回归，共 **61 项通过，35.25 秒** | `a8131b4`，见[记忆契约恢复记录](2026-11-13-memory-contract-recovery.md)。没有随后全量 409 项的执行证据。 |
| B3 | 首轮新增故障回归 7 项及相关回归共 55 项通过，13.79 秒；收紧 FTS 错误分类后新增合计 11 项，与资料备份、图谱、导入、参考及 CLI 共 **59 项通过，10.60 秒** | 工作区 `backend/ainovel/db.py` 与 `backend/tests/test_fault_restore.py`，提交号待记录。迁移修复后没有本轮全量后端或浏览器重跑证据；不将 B1/B2 移作本轮全量结果。 |
| U1 | 全量无头 **19 项通过，约 1.5 分钟** | 见[稳定性第二轮](2026-11-13-stability-round2.md)。新增中文、重连、约定测试源码在 `3edfa93` 可追溯；该运行不能冒充 `a8131b4` 后重跑全部浏览器测试。确定性替身配合真实本机 API/Worker，不调用付费模型。 |
| U2 | 追加长章无头 **1 项通过**，UI 链路 1.7 秒、总命令 6.0 秒 | [长章用例](../../tests/e2e/long-manuscript.offline.spec.ts)，后续格式提交 `f1895b7`。20 万字符交互与完整 API 正文比较；不是所有设备性能保证。 |
| P1 | 真实进程故障独立复测 **1 项通过，94.29 秒** | 实现提交 `c626c4f`；[记录](2026-11-13-worker-restart-and-longform-runner.md)。私有 `acceptance/worker-process-restart-20261113/` 保存 PID、派发、步骤指纹及原稿未变断言。 |
| L1 | 日常实例真实官方单页获取及后续闭环分次完成，最终无头 **1 项通过，2.5 秒** | [官方书库记录](2026-11-13-official-library-live.md)。一次成功下载、一次真实下载失败、显式复用成功来源完成后半链路；不声称一次无失败的网络端到端或多页整书获取。 |

下表“通过”仅表示列出的可观察范围已执行通过；“部分通过”表示同一要求仍有明确未执行部分；“未执行”表示没有能证明该场景的执行证据。源码存在用例本身不等于完成验收。文件链接后的函数名为可检索的实际用例名称。

## 任务、并发与费用故障

| 故障/边界 | 状态 | 具体测试及已证明结果 | 尚未证明的范围 |
| --- | --- | --- | --- |
| 生成时作者改稿 | 通过 | B1：[领域](../../backend/tests/test_domain.py) `test_edit_while_generating_is_not_overwritten`；[Worker](../../backend/tests/test_api_worker.py) `test_author_edit_after_generation_stops_before_review_call`；[反向整理](../../backend/tests/test_story_extraction.py) `test_mid_generation_edit_preserves_raw_result_without_staging`。新稿保留，过期结果不能成为当前候选；完成的原始模型结果可保留。 | 此行不等同于所有浏览器切换时序都已执行。 |
| 多窗口保存冲突 | 通过 | B1：`test_version_conflict_preserves_original`；U1：[双窗口](../../tests/e2e/writing-conflict.offline.spec.ts) `双窗口保存冲突：保留中文本地输入与先提交原稿，刷新可比较恢复`。独立浏览器上下文保留先提交稿、本地冲突稿及明确比较恢复。 | 无泛化为无限并发压力结论。 |
| 候选多窗口编辑、拒绝及采纳竞争 | 通过 | B1/B2：[API](../../backend/tests/test_api_worker.py) `test_candidate_cross_window_edits_and_acceptance_require_seen_version`。旧候选版本编辑、拒绝、采纳均返回冲突；不改原稿，见到新版本后才可采纳。 | 未执行浏览器端两个候选编辑窗口的全部点击竞争排列。 |
| 切章/切作品与未保存表单 | 部分通过 | U1：[离开保护](../../tests/e2e/leave-guard.offline.spec.ts) `未保存场景：拒绝切章保留表单，保存后可切换且不串用范围`、`未提交整书设置：取消切换保留输入，确认跨作品切换隔离表单`；[参考流程](../../tests/e2e/reference.offline.spec.ts) 验证返回书库/主导航取消后方案不丢。 | **正在生成时切章/切作品，再让旧响应晚返回的独立无头完整场景未执行**；后端所有权与版本校验不能代替此浏览器场景。 |
| 排队取消及运行中取消 | 通过 | B1：[API](../../backend/tests/test_api_worker.py) `test_queue_cancel_and_independent_workflows`；[结构](../../backend/tests/test_workflow_structure.py) `test_pause_preserves_pending_and_cancellation_blocks_adoption`。U1：[后台故障](../../tests/e2e/writing-faults.offline.spec.ts) `后台任务：刷新重连同一任务、取消保留片段、明确恢复后候选不覆盖原稿`。取消无候选发布、无原稿覆盖，片段留存。 | 替身取消不证明真实供应商立即停止计费。 |
| 刷新、事件重连及明确恢复 | 通过 | U1：上述后台故障用例使用 15 秒慢替身；刷新后重连同一任务并重放片段，未确认未知结果的恢复返回 409，明确恢复后只有一份未采纳候选。B1：[结构](../../backend/tests/test_workflow_structure.py) `test_staged_structure_recovers_without_another_model_call`。 | 不等同于公网代理断线、浏览器离线多日等网络组合全覆盖。 |
| 上游流不完整、错误终止及输出上限 | 通过 | B1：[供应商](../../backend/tests/test_budget_provider.py) `test_incomplete_stream_and_cancellation_hold_reservation`、`test_provider_failed_event_keeps_sanitized_reason_and_does_not_retry`、`test_explicit_output_limit_settles_known_usage_and_preserves_partial`。缺完成事件保留未知预留；失败不自动重试；已知上限用量结算并保留片段。 | 未执行真实供应商链路拔网；未证明任意字节分片、任意 SSE 畸形序列的全部排列。 |
| 结果未知超时 | 通过 | B1：[供应商](../../backend/tests/test_budget_provider.py) `test_unknown_timeout_is_not_repairable_or_retried`。MockTransport ReadTimeout 只派发一次，未知预留保留，不当成格式问题重发。 | 不是对真实账单“未产生费用”的承诺。 |
| HTTP 错误自动重试/切换风险 | 通过 | B1：[供应商](../../backend/tests/test_budget_provider.py) `test_provider_errors_never_retry_or_switch`，401/429/500 均一次请求、不静默换账户；500 保留未知预留。 | 未覆盖所有供应商错误码。 |
| 重复任务创建及重复采纳 | 通过 | B1/B2：[API](../../backend/tests/test_api_worker.py) `test_repeated_creation_returns_same_task_even_after_source_changes`；[领域](../../backend/tests/test_domain.py) `test_accept_is_atomic_and_idempotent`。稳定任务 ID、原采纳回执返回，不重复修订。 | 不以该结论代替所有业务动作幂等。 |
| Worker 真实进程终止与重启 | 通过 | P1/B1：[进程测试](../../backend/tests/test_worker_process_restart.py) `test_real_worker_kill_natural_lease_restart_and_explicit_recovery`。杀 helper 真实 PID并确认死亡；新进程自然等待 90 秒租约后 blocked_runtime，未自动派发；明确恢复后生成步骤不重做，仅重做未完成审稿。 | 实际子进程与临时数据库，不终止日常服务；不是机器断电/磁盘损坏测试。 |
| 旧 attempt 晚提交 | 通过 | P1：上述真实进程用例拒绝旧 attempt 写入；B1：[API](../../backend/tests/test_api_worker.py) `test_worker_restart_and_late_attempt_cannot_write`。原稿未变、无误采纳。 | 较早的 API 用例使用租约失效模拟，只有 P1 是真实进程死亡证据。 |
| 采纳与下一检查点原子性 | 通过 | B1：[API](../../backend/tests/test_api_worker.py) `test_accepted_chapter_and_next_checkpoint_survive_worker_loss_together`；[结构场景](../../backend/tests/test_extraction_scenes.py) `test_author_adoption_racing_workflow_publication_does_not_lose_progress`。采纳回执和推进状态一致，结构维护待办不跳过。 | 前一用例的丢失为租约模拟，不冒充第二种真实杀进程时点。 |
| 暂停、待审结构及完成边界 | 通过 | B1：[API](../../backend/tests/test_api_worker.py) `test_pause_during_last_chapter_stops_before_completion_requests`；[结构](../../backend/tests/test_workflow_structure.py) `test_completion_cannot_bypass_pending_maintenance_or_unresolved_promises`。暂停不继续完结请求，待维护或未解决承诺阻止完结。 | 替身 36 章工程运行不是文学质量通过。 |
| 总预算、双 Key、跨币种及重启 | 通过 | B1：[预算](../../backend/tests/test_budget_provider.py) `test_two_keys_share_twenty_dollars_and_restart`、`test_config_cannot_raise_authorized_cap`、`test_per_task_limit_unknown_price_and_settlement`；[API](../../backend/tests/test_api_worker.py) `test_account_continuation_preserves_checkpoint_and_separates_currency_ledgers`。OpenAI 两 Key 共 USD20、DeepSeek CNY80，预留重启不清零，配置不能抬高授权上限。 | 未执行真实费用耗尽至上限；离线账本保护通过不等于供应商最终账单已全部对账。 |
| 超大中文上下文发送前拦截 | 通过 | B1：[供应商](../../backend/tests/test_budget_provider.py) `test_oversized_chinese_context_stops_before_network_or_fee_reservation`。三个账户均在 UTF-8 实际大小超限时零网络、零新增预留；[上下文恢复](2026-11-13-longform-context-recovery.md)记录真实失败及完整可逆引用修复。 | 未保证任意未来长篇上下文都能装入上限，超限仍须可解释停止。 |
| 非法 JSON、错误枚举及有限格式修复 | 通过 | B2：[结构修复](../../backend/tests/test_structure_repair.py) `test_two_format_repairs_explain_enum_without_turning_belief_into_fact`（3 参数）、`test_repeated_wrong_kind_still_stops_after_two_repairs_without_adoption`、`test_enum_diagnostic_is_bounded_and_does_not_copy_other_invalid_payloads`。两轮上限、错误详情有界、不升级不确定性、错误产物不采纳。 | 真实第五章此前输出失败并保护性停止；代码回归通过不能追认为当次记忆提取已通过。 |
| 已完成步骤复用与过期缓存 | 通过 | B1/B2：[API](../../backend/tests/test_api_worker.py) `test_native_output_contract_changes_invalidate_completed_step_cache`、`test_changed_model_profile_cannot_reuse_previous_model_output`；[候选上下文](../../backend/tests/test_candidate_context.py) `test_lossless_wire_encoding_change_reuses_completed_generation_but_not_changed_input`。语义不变可复用，契约/模型/真实输入变化不误复用。 | 不将不同模板、不同配置的真实质量样例混成同配置重复。 |

## 数据、来源与安全故障

| 故障/边界 | 状态 | 具体测试及已证明结果 | 尚未证明的范围 |
| --- | --- | --- | --- |
| 导入中途写入失败 | 通过 | B1：[长篇导入](../../backend/tests/test_manuscript_import.py) `test_late_write_failure_rolls_back_chapters_revisions_and_receipt` 注入第二次修订写入异常，章节、修订、分支与回执均回滚；`test_whole_transaction_rejects_late_invalid_range_and_source_conflict` 检查后段错误不部分落稿。 | 这是导入事务故障，**不是数据库迁移失败注入**。 |
| 部分解析、错误编码及中断获取 | 通过 | B1：[资料/备份](../../backend/tests/test_library_backup.py) `test_partial_epub_and_bad_encoding_are_honest`、`test_interrupted_download_does_not_stay_running`。部分 EPUB 显示覆盖与失败范围；原件保留；中断不永久显示下载中。L1 保留一次真实官方请求失败，未发布伪造书籍。 | 不能保证官方网络随时成功；多页全书获取仍不在 L1 范围。 |
| 导入重复、同名映射及跨版本污染 | 通过 | B1：[长篇导入](../../backend/tests/test_manuscript_import.py) `test_preview_does_not_write_and_confirmation_is_complete_idempotent`、`test_duplicate_and_same_title_mapping_are_explicit_and_restore_original`、`test_saved_manual_mapping_reopens_and_conflicts_do_not_overwrite`、`test_surrogate_boundaries_and_cross_branch_targets_are_rejected`。预览不写、范围明确、确认幂等、旧映射冲突不覆盖。 | 无未声明自动覆盖旧稿的推论。 |
| 旧结构迁移的正常升级 | 部分通过 | B1：[人物多线](../../backend/tests/test_story_graph.py) `test_graph_backup_restore_and_old_schema_upgrade`：恢复图谱一致；删除扩展表和迁移标记模拟无记录的旧副本后，初始化重建表及标记。 | 仅覆盖该扩展的正常升级，未证明所有历史数据库版本与有复杂旧数据的迁移。 |
| 迁移执行中 SQL/初始快照回填异常 | 通过 | B3：[故障恢复](../../backend/tests/test_fault_restore.py) `test_migration_failure_rolls_back_ddl_rows_and_marker_before_retry`（schema/initial_snapshot 两参数）。真实 SQLite 在扩展表/迁移标记及模拟数据更新后遇错，或由触发器中止初始快照回填；完整数据库 dump 与旧稿/修订不变，费用不退，新增 DDL 和标记全回滚；去除故障后正常初始化成功。 | 发现原实现多个 executescript 在 autocommit 下可留下部分 DDL；本轮改为单次显式事务，不能把修复后结果追记为旧实现通过。 |
| 迁移中进程终止、磁盘满 | **未执行** | B3 的 SQL 错误与触发器中止证明 SQLite 事务回滚，不是杀进程或真实磁盘满。 | 物理中断/空间不足若纳入专项仍须隔离执行，不能由 SQL 异常结果替代。 |
| 前文/参考配置变化使结果失效 | 通过 | B1：[人物多线](../../backend/tests/test_story_graph.py) `test_upstream_edits_invalidate_records_and_keep_history`、`test_alias_extension_preserves_dependents_but_correction_and_revert_do_not`；[参考](../../backend/tests/test_reference_flow.py) `test_reference_edit_invalidates_queued_work_before_spending`；[整理](../../backend/tests/test_story_extraction.py) `test_changed_source_stops_queued_extraction_before_model`。依赖失效、历史留存、派发前拦截。 | 此处“参考变化”实测为流程配置变化，不冒充书库原始文件被外部替换。 |
| 外部资料原件丢失、被替换、重新解析导致来源变化 | **未执行** | 参考快照、文件 hash 与版本校验有正常链路证据；没有外部替换/删除原件后重新解析的完整故障执行证据。 | 需隔离数据中替换/删除原件并触发阅读、搜索、回源/重解析，核对旧摘录与旧检查状态，不把引用会话改名当此证据。 |
| 状态/事件重复应用与未来信息泄漏 | 通过 | B1：[人物多线](../../backend/tests/test_story_graph.py) `test_conflicting_states_remain_visible_and_events_do_not_accumulate`、`test_later_state_supersedes_earlier_without_replaying_event`、`test_one_event_can_have_later_presentations_without_leaking_future_quote`。同事件多次呈现不重复变更，未来出处不提前可见，冲突保留。 | 真实十章及跨卷模型连续性仍另验。 |
| 采纳时无效证据或批次内冲突 | 通过 | B1：[领域](../../backend/tests/test_domain.py) `test_bad_fact_rolls_back_adoption`；[结构整理](../../backend/tests/test_story_extraction.py) `test_update_and_new_record_roll_back_together_on_conflict`、`test_alias_conflict_rolls_back_entire_batch`；[场景](../../backend/tests/test_extraction_scenes.py) `test_scene_failure_after_first_insert_rolls_back_all_records_and_ranges`。正文/结构/范围保持原子性。 | 不据此声称所有数据库写路径均完成穷举。 |
| 局部修订越界与来源过期 | 通过 | B1：[定向修订](../../backend/tests/test_targeted_revision.py) `test_expired_wrong_or_unlocatable_report_is_blocked_before_spending`、`test_concurrent_edit_after_patch_stops_review_and_prevents_candidate_result`；[候选上下文](../../backend/tests/test_candidate_context.py) `test_local_revision_rejects_scope_escape_and_new_literal_newlines`、`test_invalid_local_scope_never_falls_back_to_whole_chapter_or_calls_model`。范围无效不扩为整章、不付费；并发改稿不发布当前结果。 | 编辑候选后检查失效并保留历史，不等于编辑后自动重查已通过。 |
| 实际备份恢复及费用不回退 | 通过 | B1：[资料/备份](../../backend/tests/test_library_backup.py) `test_backup_actual_restore_and_ledger_not_rolled_back`：快照校验、修改后正式恢复旧稿、SQLite integrity_check=ok，备份后未知费用预留不回退；`test_formal_restore_refuses_live_service` 拒绝活跃服务下覆盖恢复。 | 仅临时数据实际演练，没有覆盖运行中的日常数据库。 |
| 专项来源、导入与历史恢复 | 通过 | B1：[参考](../../backend/tests/test_reference_flow.py) `test_reference_backup_restores_snapshot_and_history`；[长篇导入](../../backend/tests/test_manuscript_import.py) `test_import_backup_restores_original_file_mapping_and_receipt`；[人物多线](../../backend/tests/test_story_graph.py) `test_graph_backup_restore_and_old_schema_upgrade`。恢复后快照、映射、回执、历史/图谱一致。 | 不泛化为损坏备份强制恢复或恢复中断后的回滚。 |
| 损坏备份与恢复写入中断 | 通过 | B3：[故障恢复](../../backend/tests/test_fault_restore.py) `test_corrupt_backup_hash_is_rejected_before_current_data_or_usage_changes`；`test_isolated_unpack_write_interruption_never_replaces_current_data`；`test_sqlite_backup_page_interruption_rolls_back_target_and_preserves_usage`。分别篡改备份但保留原 hash、在隔离目录已写入数据库后中断资料写入、由 native SQLite backup 第一页后的 progress callback 抛错。活动数据库完整 dump、资料原件及当前两币种费用均精确不变；复制前的恢复副本仍可通过完整性验证。 | SQLite backup 中断不是未执行备份调用的替身；也不等同于机器断电。 |
| 恢复进程强制终止、真实磁盘满及复制完成后清理阶段失败 | **未执行** | B3 覆盖隔离解包 OSError 和 native backup 中途 SQLite 异常；没有这些后续/物理故障的独立执行结果。 | 不把单一复制阶段的事务保护推广到整段恢复流程的任意中断时点。 |
| 高版本数据库拒写与可选 FTS 回退 | 通过 | B3：[故障恢复](../../backend/tests/test_fault_restore.py) `test_future_schema_is_rejected_before_manuscript_or_usage_changes` 保留 schema=2 的全部 dump 和费用；`test_optional_fts_unavailability_does_not_abort_atomic_initialization` 模拟 FTS 模块不可用，仍提交有效初始化并保留原稿，恢复模块后正常初始化。 | FTS 不可用以受限连接替身触发，不声称更换了本机 SQLite 构建。 |
| FTS 初始化致命错误被误当可选能力缺失 | 通过 | B3：[故障恢复](../../backend/tests/test_fault_restore.py) `test_fatal_fts_initialization_error_is_not_silently_treated_as_missing_capability` 注入 SQLITE_FULL、扩展码 SQLITE_IOERR_WRITE 和其他 SQLITE_ERROR，并先在真实 SQLite 连接执行事务回滚以覆盖引擎已回滚状态。初始化明确抛错，不能 `fts=False` 后静默成功；旧库完整 dump 和费用保持，去除故障后重启成功。生产只对明确缺少 FTS5/trigram 且不是致命错误码的情况回退。 | 这是确定性异常分类/事务状态注入，没有制造真实磁盘满或硬件 I/O 故障。 |
| 密钥脱敏、Origin/Host/写权限 | 通过 | B1：[API](../../backend/tests/test_api_worker.py) `test_boundary_and_secret_redaction`：错误 Origin、Host、CSRF 返回 403，公开配置不返回测试密钥；[供应商](../../backend/tests/test_budget_provider.py) `test_provider_failed_event_keeps_sanitized_reason_and_does_not_retry` 核对错误脱敏。 | 是列出的请求边界与输出检查，不是全面渗透测试或所有日志渠道的认证。 |
| EPUB 路径与外部请求边界 | 通过 | B1：[资料/备份](../../backend/tests/test_library_backup.py) `test_epub_paths_and_network_allowlist`：恶意 EPUB 路径拒绝；回环、非允许域、非标准端口、带凭证 URL 等拒绝。 | 未证明全部 DNS 重绑定/重定向组合，不升级为全网 SSRF 安全结论。 |

## 中文、交互与输入规模

| 故障/边界 | 状态 | 具体测试及已证明结果 | 尚未证明的范围 |
| --- | --- | --- | --- |
| 中文、表情、扩展汉字与 UTF-16 选区 | 通过 | B1：[领域](../../backend/tests/test_domain.py) `test_utf16`、`test_invalid_selection`；[长篇导入](../../backend/tests/test_manuscript_import.py) `test_chinese_encodings_preserve_original_bytes`、`test_surrogate_boundaries_and_cross_branch_targets_are_rejected`。非 BMP、换行、错误范围与半代理对覆盖；U1 双窗口用例逐字核对保存稿。 | 不将 Python 字符位置与 UTF-16 单元混用。 |
| 浏览器组合输入和组合 Enter | 部分通过 | U1：[中文故障](../../tests/e2e/writing-faults.offline.spec.ts) `中文组合输入：浏览器IME组合与Enter不触发生成、不插入额外换行，提交后逐字保存`。CDP 触发可信 compositionstart/update 和组合 Enter；提交“渡船😀𠮷”后无多余换行、无模型任务。 | 最终 compositionend 为 untrusted；**Windows 系统输入法真实人工全链路未执行**。不能称完整系统 IME 验收。 |
| 一二字查询、连续短语与原文定位 | 通过 | B1：[资料/备份](../../backend/tests/test_library_backup.py) `test_chinese_exact_search_and_utf16_evidence`、`test_phrase_crossing_text_unit_boundary`、`test_fts_unavailable_keeps_search`。短词回退、跨单元连续短语、错误顺序不命中；L1 对真实官方来源核对短词/短语精确选区与同一 file_hash。 | 不推导任意语义检索质量。 |
| 长标题/哈希、键盘与窄屏 | 部分通过 | U1：[参考完整链路](../../tests/e2e/reference.offline.spec.ts) 包含 320px、键盘操作、长 hash 换行和可操作性断言。 | 只覆盖所测页面路径；全部页面键盘顺序、长标题排列与辅助技术尚无完整人工清单。 |
| 200% 文字放大 | 部分通过 | U1：参考流程设置根字号 200%，完成关键操作并检查溢出。 | 这不是实际浏览器 200% 页面缩放，也不是全项目 WCAG 认证；实际浏览器缩放、文本间距与全页辅助技术检查未执行。 |
| 最大导入与单章保存边界 | 通过 | B1：[输入上限](../../backend/tests/test_import_maximum.py) `test_20mib_plus_one_is_rejected_without_persisting_partial_preview`、`test_exact_20mib_chinese_preview_explicit_skip_and_utf16_boundary`、`test_250000_character_chapter_limit_preserves_non_bmp_and_rejects_plus_one`。20MiB 原件/预览、明确跳过范围、250000 字符保存完整；超限不残留部分写入。详见[测量](2026-11-13-input-size-boundaries.md)。 | tracemalloc 是 Python 跟踪分配，不是进程 RSS；API 边界不等于编辑器渲染边界。 |
| 长章浏览器响应与重开 | 通过 | U2：[长章](../../tests/e2e/long-manuscript.offline.spec.ts) `长章写作：二十万字符定位末尾、修改保存和重开完整保留`。末尾键盘编辑、保存重开、完整 API 原文比较，模型任务 0。 | CodeMirror 可视区渲染；不声称 20MiB 全部进入 DOM 或所有设备均有相同耗时。 |
| 创作约定、女频无恋爱与跨作品隔离 | 通过 | U1：[约定/题材](../../tests/e2e/author-genre.offline.spec.ts) `手动创作约定与女频无CP题材：保存、预览、重载及跨作品隔离，不调用模型`。作者原话、锁定失败、禁止项、女频无 CP 及辅题材保存/重载精确一致，预览不采纳，预算不变。 | 证明配置与入口，不代表 36×男女频的模型产出全样本评估已完成。 |
| 手动反馈接入定向修订 | 通过 | 实现 `423447c`；U1：[反馈无头](../../tests/e2e/feedback-revision.offline.spec.ts) `人工反馈：定位旧来源、确认当前稿、作者选区生成候选并保留未选正文`。旧来源可定位，作者确认当前范围后进入现有修订流程，未选正文保留。 | 作者偏好仍须作者决定，不将反馈自动升级为事实错误或创作方向。 |

## 失败记录与结案原则

- 真实 Worker 用例首次 95.91 秒在 PID 断言失败：Windows 启动器 PID 与实际 Python PID 不同。修正为记录/终止实际 helper PID并验证死亡后，完整复测 94.74 秒通过，后续保留证据复测 94.29 秒通过。三个运行不合并为一次无失败运行。
- IME 最初“全部事件 trusted”断言失败。保留事件证据后，按 CDP 实际可证明的可信开始/更新/Enter分别断言，明确结束事件限制；没有宣称已补齐人工系统输入法验收。
- 约定测试初次未展开重载后的折叠表单，参考官方下载测试初次未等异步选区定位，均修正测试操作时序并保留首次 trace，没有放宽原文/配置精确比较。官方下载另一次真实网络失败仍按失败记录，后半链路显式使用本轮成功来源。
- 真实长篇曾发生上下文上限、格式包装重复正文、JSON 与枚举错误。对应工程防护/修复有独立回归，但失败模型输出不追认为通过，未采用正文与旧待审状态保留。第五章之后及两种整书的实际完成状态应查对应质量记录，不能从故障测试数量推断。
- 本轮只读审计发现迁移脚本分次执行且未统一事务。随后将全部基线/扩展结构、初始快照回填和可选 FTS 纳入同一显式事务；新增 schema 末尾故障与回填故障回归均核对完整 dump 回滚。高版本拒写与可选 FTS 回退另有回归，不通过扩大异常吞并维持初始化成功。新增恢复故障未发现需要修改恢复生产代码的缺陷。
- 复审收紧既有 FTS 广泛 OperationalError 捕获：仅明确缺少 `fts5` 模块或 `trigram` tokenizer 可回退，其他错误及致命基础错误码继续抛出。新增用例验证引擎已经回滚事务时也不能报告初始化成功；物理故障范围仍按表中未执行项保留。

## 下一步与最终判定

按证据缺口继续：补生成中切目标并晚返回的浏览器场景、外部资料原件变化场景；系统输入法与真实浏览器缩放保留人工验收项。迁移/恢复的 SQL 与写入异常已完成本轮注入，物理进程中断、磁盘满和恢复复制完成后的其他时点保持未执行。每次执行将首次失败、修复版本与复测结果追加至对应记录，不覆盖历史。

本表可结案的是**已列明确范围的工程保护**；未执行项保持开放，不以邻近测试代替。连续十章、跨卷、两种独立整书、真实模型对照、作者盲审和总投入收益另行判定。本表没有把作者尚未评价的文学质量或使用收益标为通过。
