# 限定接收者：v2保留未知与版本冻结

日期：2026-11-13。来源：[共享核对v1真实复检](2026-11-13-shared-plot-live.md)。解释保留“只向师傅”，比较仍标通过，独立流向却观察同一信息传给无法映射的另一接收者。本轮解决观察与结论不一致时的自动通过问题，不根据unknown猜出具体人物或强行判违背。

## 范围与结果

- 新`author-plot-audit/2`解释增加必填`recipient_scope=only/unrestricted/unspecified`和`scope_condition`。限制只来自原话／本章工作章纲，不能仅因为正文只出现一人就推导only；条件／时点必须保留来源连续原话，无法解释保留歧义。
- 独立流向继续只读正文和无预期方向的名称表，不向它泄露only及预期接收者关系。服务在得到观察后核对范围：only且同物／同给出方出现额外unknown或非允许接收者的明确当前传递时，即使已有正向例子，也把原pass保留为历史并将最终结果降为unknown。
- 保存`original_model_check`、原摘要、对应观察、逐字引文及`guard_override`。不猜unknown是谁，不创建新blocker，不据此自动改正文。planned、refused、歧义、异物、异给出方等不用于当前越界判定，原已明确fail及反向转移政策保留。
- 未解决范围或时点时保留作者核对，不把AI解释升级为新增作者锁定。模型流向提取仍可能遗漏；该检查不能证明所有接收者已穷尽，也不是通用语义或文学质量保证。

## 冻结与旧报告

新显式开启核对的任务，在server-owned `result.plot_contract_version`冻结v2；不改变API布尔字段或CLI。旧任务缺marker视为v1，旧nonce重放返回原任务，续跑保持原result，不补marker或重派已付步骤。

新参考任务也使用冻结v2；旧reference保留原`source_kind=None`模型schema、提示词和缓存身份。报告绑定按实际执行版本，缺少scope字段的旧产物不能只补元数据冒充v2。原真实v1报告、费用、原文及旧流程均不改。

工作台支持有内容绑定的v1／v2。v1仍可核对其原正文内容，明确显示未含限定接收者补充，不将旧内容一律误称已经过期，也不假作已执行v2；无绑定、未知版本仍保留历史／待核对。v2展示范围、条件、最终unknown原因；原pass和原摘要可折叠查看，不盖过最终未知结论。冻结政策显示实际server marker。

## 实际工程验证

- v2新专项21项通过，相关组合135项通过，均为隔离替身，不作模型检出率。覆盖仅降unknown、原结论和观察保留、旧schema／缓存／nonce／续跑不升级、不能旧产物盖v2、条件／时点和其它流向边界。
- 完整后端`backend/tests` **718 passed，0 failures／errors／skipped，228.257秒**，前后全部Python源码哈希一致。私有日志、JUnit、完整清单：`character-evidence-quality/plot-scope-v2-full-regression/`。
- 完整前端 **97 passed，9文件**；类型、Vite构建、Prettier和后端／隔离服务96文件Ruff／格式通过。构建入口`index-DOna-YLb.js`。
- 新v2场景及参考全流程5条独立无头首轮 **5 passed，0 failures／skipped／flaky，36.639秒**。包含295字合成正文、真实UI创建／选择／开启／查看，不用API写捷径；确认同物／同给出方正向与unknown观察原文、最终unknown、原pass保留、身份未确认提示、无blocker、不自动采用、不生成后章且原稿不动。
- 替身仅在instruction明确v2时增加scope字段；旧v1／无marker响应与原版逐字段相等，来源引用精确；契约证据位于私有`plot-contract-check/v2-fixture-contract-20261113T145125Z/`，五条无头证据在`v2-e2e-first-20261113T145727Z/`。

完整浏览器回归 **32 passed，0 skipped／unexpected／flaky，146.970秒**，JSON、截图／trace附件在私有`plot-scope-v2-full-regression/`。32是一次完整执行，不与上述5条定向相加。真实v2复检另行执行，不把v1漏检重新标成通过。

## CI失败与定位证据

`187b893`远端CI除E2E均通过，E2E为30过1未过：场景表单切章后控件不存在。旧用例在异步切章尚未完成时无条件点击summary，可能命中旧章已展开的面板并将其关闭。正常目标章表单按章节key重新挂载，无业务串章证据。CI当时只留下附件路径，没有上传截图／trace，不能据日志声称已看到当次DOM。

`fe7f3d4`只修测试等待目标章节标题、正文和可编辑状态，再按`details.open`确保展开；保留取消、保存、空表单／隔离断言。使用CI原构建`index-TBBcTC5X.js`，首轮定向 **2 passed，6.437秒**，未重跑；不算v2界面验证。日志和端口释放记录在私有`ci-187b893-leave-guard-fix-20261113T145443Z/`，原CI解码日志另存`ci-187b893-job.log`。

为后续CI失败保留合成测试证据，增加仅失败时上传`playwright-report/`和`test-results/`，7天保留，不含隐藏文件或本机真实资料。Action固定官方v7.0.2提交`cf430e030ddbb5b0abf93d22962f4752f3646cd9`，已通过官方release、tag和action.yml核实，使用Node24。新CI和实际模型结果分别判定，不把本机通过冒充远端通过。
