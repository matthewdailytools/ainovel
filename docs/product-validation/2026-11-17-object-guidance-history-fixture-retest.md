# 物品辨认指导：完整后端失败与历史夹具修正

日期：2026-11-17。接续[物品辨认指导工程记录](2026-11-17-candidate-object-observation-guidance.md)，保留该报告“完整后端待终态”的原截止；本篇追加随后取得的真实失败和最小测试修正，不回写旧报告为通过。首章[r1及P06未知](2026-11-17-first-chapter-r1-and-recheck-quality.md)不改，日常实例仍为237bdf7。

## 完整后端的实际失败

物品辨认指导已提交推送为`8b7327819fe142c1b819be59b3cb6bd4e779b4fe`。其434源码输入下的首轮完整后端实际为1 failed／2661 passed／1 warning，pytest耗时1591.71秒、runner墙时1594.089秒，退出码1。backend/summary的all_passed为false，inputs_stable为true；不能用已通过的静态、745前端、48Node或168无头替代失败的完整后端。

唯一失败为`test_flow_recheck_policy.py::test_historical_frozen_recheck_does_not_gain_new_call_or_marker`。该用例从当前queue产生的新复检任务构造“历史”现场，只删除task／snapshot的FLOW标记，却保留新OBJECT执行模板。这个混合样本不可能由其声称的旧创建路径生成。生产守卫拒绝的是相互不一致的冻结依据，不是实际历史稿件、原付费返回或费用已经损坏。Root及独立代码审查均确认应修正夹具，不放宽生产校验。

已保存的[CI 44015241255](https://github.com/matthewdailytools/ainovel/actions/runs/44015241255) REST记录对应精确8b提交，状态仍为in_progress、conclusion为null；本篇没有取得其success证明，不标绿，也不能用它证明下面的工作树测试修正。代码尚未部署到日常实例，没有执行新模板实付复检。

## 忠实历史夹具与保留反例

本次仅修改`backend/tests/test_flow_recheck_policy.py`，没有改生产实现、接口或旧稿：

1. 构造早于两种模板的历史任务时，同时移除task／snapshot的FLOW标记及snapshot的OBJECT标记，使样本符合当时真实可生成形状。
2. 成功恢复后明确断言task及snapshot均没有OBJECT标记，原有“不增加FLOW标记、不调用独立来源蕴含、父任务不改”的断言保持。
3. 另加`flow_removed_with_guidance`负例，故意保留OBJECT而删除双方FLOW，仍要求blocked_runtime、model.actions为空且model.calls为0；原候选payload及父任务完整保持。

这不是把原失败跳过、删除保护断言或把不一致样本放行。合法历史路径和故意不一致路径分别验证；模拟现场不能冒充真实历史数据出现过损坏，也不能把夹具修正说成真实P06已得到解决。

## 窄复测通过，新的完整回归仍待终态

Root对修正后的测试及受影响回归实际完成33项，pytest为33 passed／1 warning，14.26秒；runner墙时15.953秒、退出码0。Ruff与format检查通过，测试文件SHA为`6367c22e6b8819dc6c30ba87e0f0dbaad35cf6e5a69c1bdefa47d7e43749a540`，生产代码保持8b字节。

新的同输入完整回归另行重跑，本记录不预填通过数。当前只判这33项限定复测通过；完整后端、新精确提交CI、日常发布以及新执行模板的真实产出质量继续待验。原8b的完整失败、此前26／10类静态／168浏览器记录与本轮33窄检查分别保留，不合并为“全项目通过”。

本轮测试没有操作日常API或模型，没有修改Master、正式空稿revision0、候选r0／r1或其历史，未采纳、维护或resume；费用沿上一实际端点，DeepSeek已结CNY41.645616、未知0／余38.354384，OpenAI历史已结及未知预留保持。两书、连续十章／跨卷、文学及总投入收益仍不提高为通过。

## 仓库外证据

完整测试日志在`conditional-book-policy-20261115/`，稿件、提示和模型原始输出不入库：

| 私有相对路径 | SHA256／结果 |
| --- | --- |
| `body-object-guidance-root-verification-20261117/backend/00.log` | `4a0df4722e50d196680b0b6b4d2326437958cf5a7ca05e44f15016cca329fbe0`，1 failed／2661 passed |
| 同目录`backend/summary.json` | `fb911bb3dd32eea2eec9d4b5b69be716c571dc9ec156645798df765a21488651`，434稳定但all_passed=false |
| `ci-8b73278/runs-1794889043989.json` | `767db8160ec8e9faf7d28824fef7a3d7cbe39718ab99517bee6b4940a3c77866`，8b CI运行中 |
| `body-object-guidance-root-verification-r2-20261117/root-narrow.log` | `d6a82b9cd1609c50119e2961047a6c75fa05826f59694783f8e374817c5319d2`，33 passed |
| 同目录`root-narrow.json` | 15.953秒／退出0，列出范围、测试SHA及生产代码未变声明；不作为完整回归结果 |
