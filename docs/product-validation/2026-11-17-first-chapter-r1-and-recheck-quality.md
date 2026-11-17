# 首章r1最小修稿与正式复检质量记录

日期：2026-11-17。接续[诊断修复发布与首章r0待审](2026-11-17-diagnostic-recovery-runtime-and-first-chapter.md)。以下为独立后续截止，不修改前一报告的r0、费用或未执行状态。运行产品仍为`237bdf7 / index-CbKTBuhK.js`；文档提交不是新产品发布。

## 操作员最小修稿与历史保护

原首章候选`487912487d9d488fb1debb4aac4b0e48`的r0为2626非空白字、base_revision0，来源及完整12章规划保持。本轮先制作独立私有r1草稿，7处限定替换后为2796非空白字，仍在2500—3500范围；其它正文逐字保持，反向替换可还原r0。旧私有草稿及替换记录保留，不是模型自动返修或作者文学认可。

修稿只处理先前确认的局部问题：补清告知内容；移除从旧痕直接推出本次异常的无依据推理，把读数比对保留为待核、首次有效同刻对照仍留在原第二章；恢复购买物品的原测量用途。没有新增校准工具或动作，也没有用已完成校准的新设定代替原规划；不因P06的对象辨认问题改写已经发生的借用。

Root随后使用现有候选编辑API，携candidate_revision0，正式保存为同一候选r1。PATCH返回200，候选status仍candidate、base_revision仍0，正文SHA为`058575295c06b82b64fa3bdac5b1e2d6080317bd965ecc167d06174de7caab99`，与私有草稿完全一致。`candidate_edited.previous_candidate`保留完整r0 payload，独立历史核对为r0_payload_exact=true；编辑使旧检查失效，没有覆盖原r0依据。正式章节仍为空稿revision0，未写入新正文修订，采纳回执仍为null。

## 正式复检执行完成，正文仍未通过

Root另行显式提交一次现有`/api/artifacts/{id}/recheck`请求，返回202，任务为`10efc4222f864392be4fad67ba1f54a9`；原E7没有恢复、没有新小说批次或替代子任务。新任务冻结同一候选r1、base_revision0、正文SHA及原E7来源契约，没有从修稿反改作者情节。

复检最终任务status为succeeded，表示这次检查执行完成；报告`544d1a3e6615409aa1bd069fa41bb638`只读，不能当正文候选采纳。报告target精确指向同一候选revision1／base_revision0及上述正文SHA，当前候选仍candidate，review_passed=false、plot_audit.status=unverified。

本轮10项情节检查中9项pass，只有P06为unknown。P03已从旧unknown变为pass；原测量证据边界及物品用途的局部问题本轮未再被列为阻断，普通固定条件为12项pass及1项合法not_triggered，另有1条suggestion。它们是本次模型复检结果，不追认旧r0已经通过，也不代表表达、叙事、全部因果或作者审美验收。

## 剩余P06：流向物品辨认仍不可靠

复检输出仍存在具体归属错误：实际借得物品的F12被观察为completed，却把object_id写成other；另一处读数行为F01被错误映为来源物品O02。对真实借用的对象归属遗漏、对读数的错误对象关联必须分别保留，不能仅凭某处出现O02或completed就认定P06已得到正确证据。

因此本次P06 unknown不能直接解释成“借用没有发生”，也不能手工改pass；现有正文证据、独立观察、匹配及来源蕴含结果需要对照核实。为迎合对象漏配而反复改写正文，没有本轮业务依据。内部物品辨认指导拟作最小工程改进，但本记录截止尚无Root确认的新工程结果、部署或新提示真实质量复测；不得称产品缺陷已修复或提示已验证有效。

## 原稿、费用与运行范围

新的只读sidecar记录显式归属一次候选编辑和这一精确复检任务，不能把旧driver原有的一次诊断resume／任务白名单说成已覆盖本次独立任务。after记录逐值保护旧行、原事件／缓存、旧费用／元数据、25原件及原revision0，old_rows_and_all_fees_preserved=true；旧r2包、旧capture与Master49没有热改。

本轮复检6笔调用均有完整返回、已结，共CNY0.581950，新增未知0；全部归属`deepseek_thinking_32k`及这一复检任务，计入同一半自动书的原CNY12上限，不另开12元额度。DeepSeek累计CNY41.645616、未知0、余38.354384／总80；家族已结2.023726、余9.976274。自动书原12元和额外10元保留，所需余额为31.976274。两个OpenAI账户仍合计USD17.740313、历史未知预留1.683170／总20，未新增OpenAI调用。

原E7仍needs_review／next_chapter1／repair_round0；r1未采纳、正式空稿未变，没有结构维护、普通resume或后续章节调用，全自动未启动。记录器自身的model_calls0只代表采集没有发模型请求，不能替代本轮6笔实际复检费用。两种整书、十章／跨卷、文学、其余作者盲审及总投入收益均保持未验证。

## 私有证据索引

正文、差异、完整提示和原始模型输出只保存在仓库外`conditional-book-policy-20261115/`，不入本目录。

| 私有相对路径 | SHA256／结果 |
| --- | --- |
| `e7-chapter1-r1-local-candidate-r2-20261117/delivery-manifest.json` | `8b0ea12a0e8afeed9861d7100dff1b891fd26363327b8d94088e238a893997bb`，7替换／2796字 |
| 同目录`changes.json` | `ba30b6434f287047c4d012ba2dab52bef5b1b502b56a28c6e3cd071064549ea6`，其它文本及来源／固定项保持 |
| `e7-first-candidate-followup-prepared-20261117/before-e7-ch1.json` | `22d9247a0b7544ceaddc0de4a8dcf3bfcf63996ec8ecdc3fd9e000a263436824`，操作前现场 |
| 同目录`patch-response.json` | `b3c13a927a26676a4136058c34b65ffc7d2d70d05e98989dc05685d02e88fbb3`，200／r1 |
| 同目录`patch-history-verified.json` | `ff9c874639a2ee0f953de4d8b30b2fc1080727dd33a97bd7a2509232baac81a1`，完整r0及null采纳回执 |
| 同目录`recheck-response.json` | `4112329d9b704bb173d21a1afea742a9cb07bcd47aa78d4789bdb9c80c332444`，202／唯一复检任务 |
| 同目录`recheck-final-candidate-and-report.json` | `9e6bce436811247f11a6c3d6368a76c0f635a1d4a1b1b35b8e9e0a5cd990576a`，当前r1及只读报告 |
| 同目录`after-recheck-e7-ch1.json` | `94e137f1c133e72e4790cea6f0e72ebd0398bb7182d2441254aedb46b130d423`，旧行／全部费用保持、未采纳／维护／resume |
