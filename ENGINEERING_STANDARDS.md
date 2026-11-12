# 工程规范的行业依据与采用范围

核实日期：2026-11-12。本文记录工程基线的来源、性质及项目裁剪方式，执行入口为 [默认工程规范](ENGINEERING_RULES.md)。

## 采用原则

- 正式标准、最佳实践框架、社区规范和本项目约定分别标注，不把所有偏好称为行业强制标准。
- 采用公开可核实的一手资料；正式版本用于固定基线，草案用于跟踪，升级依据时检查规则影响。
- 对外协议必须遵守实际采用的协议要求；框架与风险清单按项目能力选取适用控制。
- 规范制定、工具检查和实际符合性评估分别记录；文档存在不代表控制已经实现或取得认证。

## 依据映射

| 依据与版本 | 性质 | 本项目采用范围 | 对应规范 |
| --- | --- | --- | --- |
| [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) / [RFC 8174](https://www.rfc-editor.org/rfc/rfc8174)，BCP 14 | IETF 最佳当前实践 | 借用 MUST／SHOULD／MAY 语义定义中文规则等级 | 工程入口与专题 |
| [NIST SP 800-218，SSDF 1.1](https://csrc.nist.gov/pubs/sp/800/218/final) | 安全开发框架，正式版 | 开发保护、缺陷预防、供应链与漏洞处理的适用实践 | 安全、质量与任务 |
| [OWASP ASVS 5.0.0](https://github.com/OWASP/ASVS/releases/tag/v5.0.0) | 应用安全验证标准，稳定版 | 按 Web／服务实际暴露面选择安全控制与验证项 | 安全与编码 |
| [W3C WCAG 2.2（2024-12-12）](https://www.w3.org/TR/2024/REC-WCAG22-20241212/) | W3C Recommendation | Web／Web 技术界面的适用 A 与 AA 成功准则作为质量目标 | 设计与 UI |
| [WAI-ARIA APG](https://www.w3.org/WAI/ARIA/apg/) | 组件模式与实现指导 | 弹窗、键盘、焦点等模式参考；优先原生控件，APG 不替代 WCAG 评估 | UI 与编码 |
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html) | HTTP 语义标准轨文档 | 存在 HTTP 接口时的方法、状态、幂等与条件请求 | 架构与 AI |
| [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) | HTTP API 错误格式标准轨文档 | 选择 Problem Details 时按其字段与媒体类型实现 | 架构 |
| [OWASP GenAI 风险实践](https://genai.owasp.org/llm-top-10/)，专题使用其标注的 2025 风险项 | 风险清单与缓解指导 | 提示注入、输出处理、资源消耗及工具权限边界 | AI 与安全 |
| [SemVer 2.0.0](https://semver.org/spec/v2.0.0.html) | 社区版本规范 | 适用的软件与公开接口版本，记录兼容及破坏性变化 | 任务与发布 |
| [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) | 社区提交规范 | 提交类型、可选 scope 和破坏性变化标记 | 任务 |
| [EditorConfig 规范](https://spec.editorconfig.org/)，核实时页面为 0.17.2 | 编辑器配置格式规范 | 用标准配置格式表达文本约定，具体值由项目选择 | 编码与 `.editorconfig` |
| [Google Engineering Practices](https://google.github.io/eng-practices/review/reviewer/looking-for.html) | 公开工程实践指南 | 变更自查、维护性、复杂度与有效测试 | 编码与质量 |
| [SQLite 原子提交](https://www.sqlite.org/atomiccommit.html)、[在线备份](https://www.sqlite.org/backup.html) 与 [PostgreSQL 18 隔离说明](https://www.postgresql.org/docs/18/transaction-iso.html) | 官方实现与机制文档 | 核实原子性、备份及并发假设，具体引擎方案在选型后适用 | 数据 |

NIST 的 [SSDF 1.2 初始公开草案](https://csrc.nist.gov/pubs/sp/800/218/r1/ipd) 与 SSDF 1.1 正式基线分别记录；本次采用 1.1，不把草案标为正式要求。

## 项目默认值与执行方式

- 默认使用 UTF-8、LF、文件末尾换行；Markdown 保留必要的行尾空格语义。配置位于 [.editorconfig](.editorconfig)，由支持 EditorConfig 的编辑器读取。
- 间距尺度、字号、触控舒适尺寸等属于项目设计默认值；对比度、目标尺寸等标准要求及适用例外按设计与 UI 专题准确区分。
- 模块职责、轻量决策记录、质量检查矩阵、按风险选择测试和自动 GitHub 提交属于项目工程约定。
- 不预先指定框架、数据库、扫描平台、覆盖率百分比或性能数值；选择后将必要配置与命令纳入可执行工程流程。
- 根目录 `AGENTS.md` 与 `alwaysApply: true` 的规则入口负责默认加载；已有检查按任务执行，后续建立的 CI 应复用相同检查命令。
- 所有规范默认仅作用于本仓库；业务规则依据真实使用与用户反馈单独迭代。
