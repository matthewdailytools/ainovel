# N需求→U场景→F功能→任务→证据追踪

日期：2026-11-13。需求名称及编号依据根目录13、14号设计，参考闭环补充16号方案与本轮明确计划。本次同步按截至源码ea7a2ec（含28e9a01的U02与设定范围）及已有运行记录核对；不读取日常数据库或代替正在推进的长篇任务报告。以下是验收对照，不是市场需求占比结论。

证据列只证明相应范围：确定性替身验证工程行为，真实模型产物验证可运行性与具体问题，作者盲审验证文学体验。三者不能互相替代。源码ea7a2ec的新一轮全量后端554项通过（201.51秒），全量无头26项通过（128.55秒），类型、构建及远端CI通过，见[第三轮稳定性](2026-11-13-stability-round3.md)。历史后端与无头次数保留于[第二轮稳定性](2026-11-13-stability-round2.md)及各集成记录，不能合并为当前源码的同一次全量。

| 需求 | 场景 | 作者任务 | 功能 | 验收操作 | 已有实际证据范围 | 剩余验证／限制 |
| --- | --- | --- | --- | --- | --- | --- |
| N01、N02、N08、N11 | U01 | 空白起笔与卡点 | F01、F08、F14 | 无大纲手写→建议→作者选方向→补写／拒绝→恢复 | test_domain.py；writing-conflict.offline.spec.ts；writing-faults.offline.spec.ts；long-manuscript.offline.spec.ts；direction-handoff.offline.spec.ts已完成方向编辑、来源保护、仅载入不生成、固定条件保留及拒绝／采纳恢复 | 工程完整任务已实测；作者文学质量与准备、等待、核查、返修等总投入未评 |
| N01、N02、N12 | U02 | 情绪、钩子与方向 | F02、F03、F04、F05、F06、F07 | 一句想法→AI意图提案→作者修改确认→按需设定→正文候选与恢复 | 28e9a01；intent-to-writing.offline.spec.ts三项覆盖三类本书设定的真实生成来源、本章资料隔离、历史范围恢复、跳过全部规划及无章节边界；test_author_contract.py、test_genre_integration.py、author-genre.offline.spec.ts及story-inheritance.offline.spec.ts的保存／隔离证据保留 | 不再列为缺少完整工程作者任务；AI提案不冒充锁定项，旧章级产物不自动升级，设定不变成已发生事实；真实模型表达与作者体验／总投入仍待评 |
| N02、N04、N12 | U03 | 频道、题材与机制 | F02、F03、F04、F09、F10 | 组合题材→查看例外→按作者约定生成 | test_genre_catalog.py；test_genre_integration.py；author-genre.offline.spec.ts；女频无CP、仙侠与自定义辅题材、作者例外的界面与请求隔离已实测 | 36题材与频道组合已融入目录／上下文／自动化的工程证据，不代表每种组合的真实表达质量已验收；不能仅据目录齐全宣称质量覆盖 |
| N03、N04、N12 | U04 | 指定情节补写 | F02、F05、F07、F08、F10 | 固定事件结果→正文候选→情节覆盖核对 | test_api_worker.py；workflow-structure.offline.spec.ts；七类首轮7对14稿已完成；交涉／调查／打斗另各三次，共9对18稿；真实原稿方向反转检出与失败产物保留 | 固定条件模型pass不保证完整情节忠实；真实方向错误、数量／长度控制及误报仍需独立核对，不能判总体质量通过 |
| N03、N04、N12 | U05 | 打斗描写与参考学习 | F08、F09、F10、F14 | 选段→证据拆解→本书方案→试写→比较 | test_reference_flow.py；reference.offline.spec.ts完整闭环；首轮及三类三次重复均已完成，匿名包、答案索引与费用已分开保存；规则／方案／候选编辑的离开保护已实测 | 代表三次重复不再列为未执行；作者尚未盲审，表达、情绪、声音、偏好与完整总投入收益仍未验证；不同模板轮次分别记录 |
| N05、N08 | U06 | 人物在目标时点的知识与能力 | F05、F06、F11 | 身份别名→来源事件→视角状态→回原文 | test_story_graph.py；test_scene_time.py；test_extraction_scenes.py；scene-time.offline.spec.ts；真实采用的前文／结构／场景及旧十章超限记录保留，账户边界与新阶段独立复核另列 | 连续十章出口尚未达到；来源定位和已修复重复不等于认知／能力或数值因果正确，新阶段前两章仍有基准／时点阻断，后续质量须逐章核实 |
| N05、N06、N10 | U07 | 多线与伏笔 | F06、F07、F11 | 依赖／多线→埋设强化→回收／保留→定位 | test_story_graph.py、test_manual_finalization.py、story-graph.offline.spec.ts及test_longform_scale.py的100事件跨卷／来源失效证据保留；0693e5e、45da49a及test_story_extraction_promises.py／test_story_extraction_append.py验证独立阶段来源、明确追加保留旧阶段／旧证据／历史、100阶段上界、伪造或过期来源回滚，safe_automatic未放宽 | 默认仍为原有替换提案；保留／放弃不借追加模式自动决定。引用正确不保证伏笔解释正确；真实跨卷、主线闭合及承诺处理仍待整体核对 |
| N07、N08、N11 | U08 | 获取、阅读与取材 | F12、F08、F16 | 获取／导入→原文搜索→摘录→任务资料包 | test_library_backup.py；reference.offline.spec.ts；reference.live.spec.ts；中文短词／跨单元短语、部分EPUB、回源及主动选段已验证 | 官方单页真实获取→阅读→搜索回源→摘录→主动资料包已分次完成（library-acquisition.live.spec.ts）；多页EPUB及模型取材质量未评 |
| N02、N05、N08 | U09 | 已有稿件继续创作 | F01、F05、F11、F14 | 编码章界预览→映射草稿→导入→整理候选 | test_manuscript_import.py；test_story_extraction.py；manuscript-import.offline.spec.ts；36／100章规模、test_import_maximum.py恰好20MiB及超界、250000字符与UTF-16边界；long-manuscript.offline.spec.ts长章编辑／保存／刷新逐字一致 | 20MiB服务端边界和长章浏览器专项不再列为未执行；本轮没有证明20MiB原文全部在编辑器同时渲染，且大尾部跳过回执只证明选定范围，不冒充整文件导入 |
| N05、N06 | U10 | 前文修改影响 | F10、F11、F14 | 修改原文→依赖失效→有限修订→复检 | test_targeted_revision.py；test_targeted_revision_race.py；test_longform_scale.py；真实开篇局部修订49f02254575349608dc691e85535f352只改结尾追问，未选正文保持，旧候选未采用 | 局部最小修订已实际执行，不再列为完全未做；长篇跨章检出／误报、后续影响的修订质量及作者认可仍待评，不能由单个局部修订外推 |
| N02、N03、N05、N06、N10、N12 | U11 | 连续一卷至整书 | F02、F03、F04、F05、F06、F07、F08、F09、F10、F11、F13、F14、F15、F16 | 选择阶段／整书→规划／作者情节→写作维护→暂停恢复→范围审查 | test_api_worker.py两模式工程36章、test_workflow_structure.py、workflow-structure.offline.spec.ts、真实Worker终止／90秒租约及runner证据保留；ea1cb95、test_workflow_scope.py、test_completion_scope_evidence.py及workflow-scope.offline.spec.ts验证阶段终态、逐章ID／修订／UTF-16证据、暂停恢复和整书默认兼容；ea7a2ec补混合干预回归 | segment_complete固定closed=false，不能充当整书生产完成或三层通过；旧十章不追认阶段通过，当前不含向同一已完成阶段版追加下一批。真实十章、跨卷及两模式独立36章仍待验收；旧36章与18／15章候选保持待审 |
| N04、N06、N09、N10、N12 | U12 | 反馈、审稿与交付 | F10、F14、F15 | 反馈分类定位→定向修订→承诺核对→导出 | 423447c已接通反馈到对应章节的主动局部修订；feedback-revision.offline.spec.ts验证旧来源确认、作者选区、来源元数据、仅替换选区、恢复及键盘退出；test_targeted_revision.py、test_manual_finalization.py、test_evidence.py、test_library_backup.py | 不再只是复制提示词；人工反馈不伪装模型审稿，审美意见不升级事实。真实整书评价、最终稿／恢复一致性以及作者定稿仍待完整长篇出口 |
| N08、N11、N12 | U13 | 模型与预算 | F16、F13 | 账户配置→队列→取消／未知／续跑→费用恢复 | test_budget_provider.py、test_api_worker.py、test_worker_process_restart.py、writing-faults.offline.spec.ts及真实费用／进程证据保留；cac0016按账户核实窗口与来源，以同一实际UTF-8 JSON字节串校验和发送；ea7a2ec、test_workflow_interventions.py核对混合干预、旧拒绝正文、同章产物／状态与最多8帧安全诊断 | 晚响应切目标、资料原件变化、物理故障及完整人工输入／缩放等未执行范围仍开放；离线恢复不等于现场成功。长篇累计费用待结束归档，两个OpenAI合计USD20、DeepSeek CNY80不变且账本不清零 |

## 已完成证据的更新入口

- [人工反馈与局部最小修订](2026-11-13-feedback-and-minimal-revision.md)：替代“反馈只有复制入口”“尚未做局部最小修订”的旧描述。
- [第二轮稳定性](2026-11-13-stability-round2.md)和[大稿边界](2026-11-13-input-size-boundaries.md)：替代“20MiB、组合输入、长章编辑、真实Worker重启均未执行”的旧描述；各测试限制仍保留。
- [代表三类三次重复](2026-11-13-reference-repeats.md)：9对18稿已完成；与七类首轮合计产物不混作同模板同配置的重复实验。
- [长篇上下文恢复](2026-11-13-longform-context-recovery.md)：真实十章执行已开始并出现已记录阻断；不能继续写成“尚未启动”，也不能写成“十章通过”。
- [一句想法到正文](2026-11-13-intent-to-writing.md)：U02完整工程任务及本书设定／本章资料范围已实际验证，不再保留“尚无完整任务证据”的旧描述；文学体验仍待作者评。
- [阶段与整书范围](2026-11-13-workflow-completion-scope.md)：批次范围、逐章证据和未来计划保留；这与U02的设定用途范围是两个独立选择。
- [伏笔阶段证据](2026-11-13-promise-stage-grounding.md)、[明确追加阶段](2026-11-13-promise-append-stages.md)及[混合干预与安全诊断](2026-11-13-workflow-interventions.md)：维护稳定性已修复并留专项回归；不把有限修复、机械定位或离线通过升级为真实连续性质量通过。
- [按账户窗口与真实发送字节](2026-11-13-account-context-limits.md)：cac0016的双边界已验证，不继续用旧统一120000字节描述当前所有模型能力；明确配置不改变费用上限和历史预留。
- [新阶段长篇与独立质量复核](2026-11-13-segment-quality-review.md)：旧任务986d69364771415289d04e6926e87ab8保留六章，冻结whole_book输入不改写；新segment任务248d30c01c98452988903558d16252e0已存四章，恢复前在第5章派发前blocked_runtime。前两章的基准／时点质量问题及未通过、未采纳的修订候选继续保留；不能合并两次正文或追认十章通过。

## 当前仍需完成的验收出口

1. 故障／需求矩阵继续按真实证据结案。U01方向连接及U02一句想法到设定／正文的完整工程任务已实测，不再重复列为未执行；未覆盖故障及人工界面专项仍保留，不用邻近用例替代。
2. 官方单页资料闭环已做，继续评估多页EPUB及模型取材相关性；分次下载失败仍保留。
3. 按cac0016已验证的账户模型窗口／官方来源与实际JSON发送字节双边界，在累计预算内继续真实十章和跨卷，不静默截断必要证据；同一简报、独立半自动／全自动36章版本，保留调用、返修、干预、预算和恢复结果。阶段与整书分别判定，旧冻结whole_book不改写，也不因章数写满追认通过。
4. 完整整书证据包与最终稿／实际恢复一致性，全部严重连续性问题解决后再作整书工程及业务分别判定。
5. 作者盲审与实际总投入测量。现有匿名包已交付，评价尚未收到；未审的表达、情绪、声音及收益不标通过。不从自用样例推导市场占比或付费结论。

编号覆盖：N01—N12、U01—U13、F01—F16均有映射。编号齐全仅说明没有漏列，不证明所有行为或业务结果已经验收通过。半自动与全自动在U11分别保留真实整书出口，参考学习在U05保留作者质量出口。PDF／OCR、百章真实创作等后续扩展不因列在历史构思中自动变成本轮首版的阻断前置项。

历史轮次补充：全量后端476项及全量无头21项曾通过；FTS异常分类再补4项后，11项迁移／恢复故障专项通过（相关回归59项）。这些历史次数与最新26项无头分开报告，不合并为一次全量。旧十章在第6章结构维护曾发现缺阶段引用，后续阶段证据和显式追加修复已交付；真实任务当前进度仍以独立记录为准，尚无十章或新整书通过的聚合判定。
