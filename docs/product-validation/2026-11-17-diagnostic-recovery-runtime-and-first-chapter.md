# 诊断修复实际发布、同任务恢复与首章待审

日期：2026-11-17。接续[已付有效缓存兼容](2026-11-17-paid-cache-recovery-compatibility.md)和[解释停点与有限恢复](2026-11-16-interpretation-diagnostic-stop-and-recovery.md)。本轮工程恢复已完成；半自动首章仍待审，未采纳、未维护、未继续下一章。旧文、旧失败与各轮截止保持。

## 同输入回归与精确提交

产品提交为`237bdf7854a77b596e439006d0861abb92be0945`。Root在同一组433个源码输入、7个tested构建文件下实际完成2653项后端、745项前端、168项全项目无头及48项Node检查；API生成、Ruff、格式、类型、构建和审阅页检查通过，三个阶段before／after及最终post清单保持。后端日志为2653 passed及1条warning，无头日志为168 passed，未把前一提交的2649项结果用于本版本。

该精确提交的[CI 43911364245](https://github.com/matthewdailytools/ainovel/actions/runs/43911364245)及job133453822644已实际completed／success，REST记录的head SHA与提交一致。本次文档只读取已落证据，没有重新执行完整回归、CI或模型。

## V7真实发布、恢复与历史保护

日常实例已更新为`237bdf7 / index-CbKTBuhK.js`。即使主UI文件名相同，也重新核对了新提交、精确CI及新绑定。运行包由664个Git来源文件和14个dist文件组成，共678项；其中7项为同输入验证的构建文件，旧资源保留。

正式备份`6cd64e7433f94974937de2cff8283116`为31,080,162字节，官方verify实际隔离恢复且integrity为ok；发布流程另有两次实际完整恢复和API读回，目标版本再备份为`da6b14f052114e9f996dbb62e8d9ee2d`。两次恢复保持40个非sqlite业务表及1个序列表、另2个usage表，272章（含空章）、243任务、25资料原件及旧费用，未迁移。隔离副本禁用Worker／原生启动，API读回不冒称真实服务启动。

旧Worker自然退出，正式start／verify使用原restart gate；新API为66892、Worker为11152，并有token、birth及来源链见证。发布阶段business_mutations和model_calls均为0，原36章、18／15章候选、U02手工r1、U10候选及旧来源／报告保持。发布阶段的0模型不扩展到下面另行授权的E7恢复。Master49只作外部独立侧护，未写入V9、未修改其49项记录，也不将V9说成包含该文件。

## A07日常只读入口

在新运行版本的独立无头浏览器中，使用既有明确测试作品，实际完成最近任务预览、确认默认关闭、取消不改变表单／资料选择，再读取确认，仅在本地恢复instruction／fixed／profile及唯一原reference_id。原空稿r0与目标API字节保持。桌面及320窄屏、实际文字翻倍、键盘操作完成；预览和恢复区域的测量比值均为2，document宽度仍320。

普通计时器保持运行，记录86个浏览器请求；1次精确owned阅读位置PUT尝试被abort，非GET发送0、fatal0、pageerror0，两个自有context均关闭。不能表述为“没有非GET尝试”，也未证明正常阅读保存已发生。此例没有原选区，不能声称日常正向UTF-16选区恢复已验；只核当前引用内容及已导入书目，不是历史资料快照重演或重新校验磁盘EPUB。模型、采纳及新子任务均为0。

## 私有CLI反例、r2及真实准备

准备阶段的只读审查发现，旧私有driver在部分helper定义前调用main；匿名真实`__main__`→argparse路径分别复现prepare缺sources及capture缺capture的NameError。它是执行设施的确定反例，发现于实际prepare／POST之前，不记为产品、模型或原稿失败；不是旧53项import式检查已经覆盖的路径。

另立r2仅将原main guard移到文件末尾，15个函数原始文本及AST不变，来源、预算、nonce及所有其它守卫保持。新增4项窄检查实际通过：其中2个参数化用例分别执行旧／新脚本，共4次真实main路径；另2项验证函数／模块及模板一致性。不能把4项全部称为独立CLI用例。旧反例和原包保留。

r2随后真实prepare成功，prepared SHA为`0d43bdf1604cf95b4713289de65be4e9767b9a5a9841ea29531b95d50e14d461`，计划SHA为`95fcf50daeb7f09e6a87691b09b34a4fb83722c3025227b92d7094131e4bfb55`。原E7、父E5／536、完整12章与结局、旧4份返回／CNY0.286862、首章空稿revision0及账户公示配置均匹配；全自动原nonce尚未发送，plots仍为空。首次所需余额为11.580792−0.286862＋12＋10＝CNY33.293930，低于当时DeepSeek可用39.672040；没有清零历史费用或重复预留已付半自动额度。

## 同E7实际恢复及正文出口

Root核对新准备后只发送一次正式resume，任务仍为`e7e0f7c62e364dcba9147b4b667c5ed9`；未创建替代子任务、未改来源、完整规划、账户或原正文。新的interpret纠错授权实际只消耗1个wire额度，上限仍为2；旧三份错误解释和旧费用未改判。独立完整原意保持随后完成，observe／interpret／preserve均completed，来源state为ready。期间独立保持的格式／引用修正仍保留，未绕过检查。

最新稳定capture的errors为空，old_rows_and_calls_preserved为true；本轮新增10份完整返回、10笔已结CNY0.735706，未知0。E7合计14份返回及14笔费用CNY1.022568，103事件和14份调用元数据可回查；原4份返回／CNY0.286862保持。source ready表示本次来源链完成，不等于正文、文学或整书通过。

首章候选为`487912487d9d488fb1debb4aac4b0e48`，r0／base_revision0，2626非空白字，处于2500—3500配置范围；正式正文仍为空稿r0。任务为needs_review／next_chapter1／repair_round0，无采纳或结构维护，全自动仍未启动。普通条件为12项pass及1项合法not_triggered、4条suggestion；真正停点是plot_audit为unverified，P03／P06两项unknown，不能因来源就绪或条件检查无失败而覆盖。

独立只读审读区分了问题：P03的告知内容交代仍有证据缺口；P06已有借得物品的正文证据，流向观察的对象归属漏配，不能据此认定借用未发生或为迎合检查反复改写。另有测量基准／因果推断和购买对象用途的局部修订需要。这是代理客观核查，不是作者文学认可；没有提交手工新稿、正文复检、采纳或后续resume的完成证据。

## 下一步边界与仍未完成

原r2 driver只授权一次带诊断marker的resume，且新Task白名单只有E7及原自动请求；后续候选复检会产生独立candidate_recheck任务，正常正文采纳后的第二次resume也超出该范围。只读代码审查已经确认该范围限制，尚未实际执行或视为产品故障，也没有热改原封存包。首章候选是在准备后新增，编辑／采纳该候选本身符合原行保护；首章当前修订及branch故事版本可按原例外前进，原revision0仍须逐字段保留。

应先明确后续操作的最小归属记录，再按当前候选版本核对或最小编辑、显式复检、作者决定采纳，并以现有resume的pause_after_maintenance完成维护后暂停；不能继续用旧capture声明新增任务／第二次resume已经受其保护。整书出口仍要求production_complete、全部12章当前修订、状态维护、承诺及结局闭合，无阻断后才能另行启动原全自动请求。半自动首章、全自动新书、Source2真实复测、十章／跨卷、两种整书、其余盲审、文学和总投入收益均不提高为通过。

## 费用及可追溯证据

本轮终态：DeepSeek累计CNY41.063666、未知0、剩38.936334／总80；两个OpenAI账户合计USD17.740313、历史未知预留1.683170保持／总20。半自动家族累计CNY1.441776；本轮10笔费用均计入，不把准备、发布和只读浏览器的0调用混算为整个阶段0费用。

完整稿件、原始提示、响应、截图和诊断均在仓库外`conditional-book-policy-20261115/`。下列摘要不携带正文或凭据：

| 私有相对路径 | SHA256／结果 |
| --- | --- |
| `e7-compatibility-root-verification-20261117/final-proof.json` | `0cd926e72ba8abc517258ee6996972f952b1607dfe1715c97cebc9dd1d2ad3f3`，433／7三阶段保持 |
| `ci-237bdf7/final-run.json` | `53d975222115538e4932c5b52d09a3ef13170d42a69d1c9b3dd122a6c44b5fa8`，精确提交success |
| `runtime-scene41-next/20261116-aftere3-diagnostic-resume-v7/b-237bdf7.json` | `6dd147932562ea21fd42d388528c384237ad6e92e82bf9dcede9232910ee6d2b` |
| 同V7目录`r-237bdf7/rollout-result.json` | `68e12179189432f0f5cd9acbf3d6d40cb66388555db107597fbb5c7623f96033`，实际verify |
| 同V7目录`r-237bdf7/actual-restore.json` | `969987829e7815b78f62b0b599ad7478f68dceb72aaadf7780fdaed22a445fb6`，双真实恢复 |
| 同V7目录`fresh-v9-237bdf7.json` | `1823a841a22e17005591f78919c2cf3cfe96ae7744de05a1219712dfa973d742`，稳定现场；Master另护 |
| `a07-task-input-readonly-ui-v7-prepared-20261117/run-237bdf7/final-summary.json` | `19e6f62b5a3bc2aa08d1752719de771cc558975e25b6544d65427c42aa8a1f95` |
| `reduced12-e7-formal-resume-r2-prepared-20261117/manifest.json` | `2463b5f1753136a630caafd221fbf582c1ac96263103285bab464c06e381b25a`，私有r2／4窄检查 |
| 同r2目录`run-237bdf7/start-resume-intent.json` | `7c635439ab65f6811d0cacaff9ac6e3a45e72d12333818f85da0c097fac25998`，先持久intent |
| 同r2目录`run-237bdf7/capture-1794880578769368100.json` | `cf12ca5200299f69d89a7cbc581684fbb5617f656bbeea056b91c3892263a836`，needs_review／旧行保持 |
| 同r2目录`review-e7-chapter1/artifact.json` | `b549c5cc5faed0d997b03d0893d876cfc7e9c840b77f7a3a8be0e97ea023895f`，原r0未采用 |
| `e7-resumed-chapter1-independent-20261117/review.md` | `2c9bb07cbf345e7ad2b32520f22f50b762fdb5e8495c779d41ac00abfb255a9d`，独立审读／不替作者判断 |
