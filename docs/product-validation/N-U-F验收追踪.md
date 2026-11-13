# N需求→U场景→F功能→任务→证据追踪

日期：2026-11-13。需求名称及编号依据根目录13、14号设计；参考闭环补充16号方案及本轮明确计划。以下映射为实现验收的对照，不是外部调研的市场占比结论。

证据列指向本轮已运行的相关测试范围，不能据此把整个U场景或F功能标通过。统一状态为“部分证据，业务验收未完成”；作者审美、收益未验证。后端文件位于`backend/tests/`，浏览器文件位于`tests/e2e/`；实际结果见同目录阶段记录及HTML历史。

| 需求 | 场景 | 作者任务 | 功能 | 验收操作 | 已有实际证据范围 | 未完成/限制 |
| --- | --- | --- | --- | --- | --- | --- |
| N01、N02、N08、N11 | U01 | 空白起笔与卡点 | F01、F08、F14 | 无大纲手写→辅助/拒绝→冲突与恢复 | test_domain.py；writing-conflict.offline.spec.ts；reference.offline.spec.ts | 卡点方向选择到本书补写的完整实测及作者投入 |
| N01、N02、N12 | U02 | 情绪、钩子与方向 | F02、F03、F04、F05、F06、F07 | 原话→意图提案→确认→必要世界人物规划 | test_author_contract.py；test_genre_integration.py；story-inheritance.offline.spec.ts | 完整作者任务和审美方向判断 |
| N02、N04、N12 | U03 | 频道、题材与机制 | F02、F03、F04、F09、F10 | 组合题材→查看例外→按作者约定生成 | test_genre_catalog.py；test_genre_integration.py | 36题材×频道实际输出适配，不能由目录齐全证明 |
| N03、N04、N12 | U04 | 指定情节补写 | F02、F05、F07、F08、F10 | 固定事件结果→正文候选→情节覆盖核对 | test_api_worker.py；workflow-structure.offline.spec.ts；真实七类首轮进行中 | 独立核对交易方向等隐含情节，质量保持未验证 |
| N03、N04、N12 | U05 | 打斗描写与参考学习 | F08、F09、F10、F14 | 选段→证据拆解→本书方案→试写→比较 | test_reference_flow.py；reference.offline.spec.ts；真实七类首轮进行中 | 代表样例三次重复、作者盲审及投入比较 |
| N05、N08 | U06 | 人物在目标时点的知识与能力 | F05、F06、F11 | 身份别名→来源事件→视角状态→回原文 | test_story_graph.py；test_scene_time.py；scene-time.offline.spec.ts | 真实连续十章认知/能力质量评估 |
| N05、N06、N10 | U07 | 多线与伏笔 | F06、F07、F11 | 依赖/多线→埋设强化→回收/保留→定位 | test_story_graph.py；test_manual_finalization.py；story-graph.offline.spec.ts；test_longform_scale.py | 真实跨卷叙事及未解决承诺的整体判断 |
| N07、N08、N11 | U08 | 获取、阅读与取材 | F12、F08、F16 | 获取/导入→原文搜索→摘录→任务资料包 | test_library_backup.py；reference.offline.spec.ts；日常reference.live.spec.ts | 本轮真实官方获取全路径与材料相关性评估 |
| N02、N05、N08 | U09 | 已有稿件继续创作 | F01、F05、F11、F14 | 编码章界预览→映射草稿→导入→整理候选 | test_manuscript_import.py；test_story_extraction.py；manuscript-import.offline.spec.ts；36/100章规模 | 20MB边界与长文本编辑器响应专项 |
| N05、N06 | U10 | 前文修改影响 | F10、F11、F14 | 修改原文→依赖失效→有限修订→复检 | test_targeted_revision.py；test_targeted_revision_race.py；test_longform_scale.py | 真实长篇的检出/误报和修订质量 |
| N02、N03、N05、N06、N10、N12 | U11 | 连续一卷至整书 | F02、F03、F04、F05、F06、F07、F08、F09、F10、F11、F13、F14、F15、F16 | 批次规划/作者情节→写作→维护→暂停恢复→完结 | test_api_worker.py中两模式工程36章；test_workflow_structure.py；workflow-structure.offline.spec.ts | 真实新结构十章、跨卷、独立两模式整书与故障验收 |
| N04、N06、N09、N10、N12 | U12 | 反馈、审稿与交付 | F10、F14、F15 | 反馈分类定位→定向修订→承诺核对→导出 | test_targeted_revision.py；test_manual_finalization.py；test_evidence.py；test_library_backup.py | 当前手动反馈为复制修订要求；完整浏览器连接、整书作者评价待完成 |
| N08、N11、N12 | U13 | 模型与预算 | F16、F13 | 账户配置→队列→取消/未知/续跑→费用恢复 | test_budget_provider.py；test_api_worker.py；test_library_backup.py；日常费用账本逐条核对 | 全部故障编号逐项结案及本轮真实调用的最终费用归档 |

编号覆盖核对：N01—N12共12项、U01—U13共13项、F01—F16共16项均有映射。覆盖完整仅说明没有漏列编号，不证明每项行为已经实现或验收通过。半自动与全自动均在U11单独保留真实整书出口；参考闭环在U05单独保留质量出口。
