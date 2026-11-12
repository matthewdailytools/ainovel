# DeepSeek / OpenAI API 连接测试

本工具测试 API Key、网络、模型权限及一次简短文本生成是否可用。OpenAI 对应 ChatGPT 系列模型的 API，命令中也可以使用 `chatgpt` 别名。

## 配置与运行

运行要求：Node.js 22 或以上。无需安装依赖，无构建步骤；本机已验证 Node.js 25.1.0。此命令独立于未来小说应用的技术选型。

本机已准备根目录 `.env`，填写以下两项并保存：

```dotenv
DEEPSEEK_API_KEY=填写你的DeepSeek密钥
OPENAI_API_KEY=填写你的OpenAI密钥
```

新克隆仓库时，在项目根目录运行以下 PowerShell 命令创建配置（已有文件时不要覆盖）：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

在项目根目录执行：

```powershell
# 默认依次测试两家
node scripts/test-api.mjs

# 单独测试
node scripts/test-api.mjs deepseek
node scripts/test-api.mjs openai
# openai 的别名
node scripts/test-api.mjs chatgpt

# 脱敏 JSON 结果
node scripts/test-api.mjs all --json

# 指定其他本地配置文件
node scripts/test-api.mjs all --env-file C:\本地配置\ainovel.env
```

每家只发送一次固定测试句：“这是 API 连通性测试，请只回复：连接成功”。会产生少量 API 用量，不读取或发送小说、设计文档及其他本地文件。任何正常完成的非空文本回复均可验证连通性，不要求模型逐字一致。

配置在每次运行时重新读取，进程环境变量优先于 `.env`。`.env` 使用 UTF-8，可使用引号与注释；不要给密钥添加 `Bearer` 前缀。默认文件位置固定为项目根目录，`--env-file` 的相对路径按当前终端目录解析。

## 可选配置

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `DEEPSEEK_MODEL` | `deepseek-flash` | DeepSeek 模型标识 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 自动追加 `/chat/completions` |
| `OPENAI_MODEL` | `gpt-6-luna` | OpenAI 模型标识，需有相应权限 |
| `OPENAI_REASONING_EFFORT` | `none` | 显式设置思考强度；留空时省略该参数 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | 自动追加 `/responses` |
| `API_TEST_TIMEOUT_MS` | `30000` | 每家完整请求时限，包含响应体读取；范围 1000–120000 毫秒 |
| `API_TEST_MAX_OUTPUT_TOKENS` | `64` | 每家输出 token 上限，范围 16–4096 |

默认选择用于连接测试的模型，不代表生成质量评估或主应用选型。两家默认模型均通过显式参数关闭思考以控制测试用量；OpenAI 使用 Responses API，并设置 `store: false`。更换模型时，需自行确认对应接口与参数支持情况；不支持 `reasoning` 参数的模型需将 `OPENAI_REASONING_EFFORT` 留空。推理模式可能需要更大的输出 token 上限。程序不会自动降级、切换模型或重试。

## 结果与故障处理

成功时显示实际返回模型、回复、总耗时和服务返回的 token 用量；服务未返回的用量显示“未返回”，不估算费用。JSON 还包含任务标识、检查时间、提示词版本、结束状态及可获得的请求 ID，便于排查。

| 结果 | 下一步 |
| --- | --- |
| 缺少配置 | 在 `.env` 中填写相应密钥；只测试一家时无需填写另一家 |
| HTTP 401 | 检查密钥是否正确或已失效 |
| HTTP 402 / `insufficient_quota` | 检查 API 余额与计费配置 |
| HTTP 403 | 检查账户、地区及项目权限 |
| HTTP 404 / `model_not_found` | 检查模型名、模型权限和服务地址 |
| HTTP 429 | 稍后手动重试，检查用量限制 |
| HTTP 5xx | 服务暂不可用，稍后手动重试 |
| 网络失败 / 超时 | 检查 DNS、代理、网络、证书；必要时调大时限 |
| 返回重定向 | 已阻止转发密钥，核对服务地址 |
| 非 JSON、空内容、拒答或生成未完成 | 检查接口兼容性、模型与 token 上限，不记为成功 |

退出码：`0` 为所选服务全部成功，`1` 为至少一家失败，`2` 为命令参数或配置文件读取错误，`130` 为 Ctrl+C 取消。一家失败不会阻止另一家测试。Ctrl+C 会取消当前本地请求并阻止继续测试；取消或超时不保证服务端未处理或未计费。

## 配置安全与实现边界

- `.env` 和 `.env.*` 已加入 Git 忽略规则，仅 `.env.example` 作为空密钥示例提交。实际密钥只在本地配置或进程环境中读取，不写入代码、命令参数或提示词。
- 密钥通过 HTTPS 的 `Authorization` 请求头发给配置的服务地址，保留证书验证并禁止跟随重定向。自定义地址必须可信，程序会向该地址发送对应密钥；不要填写完整接口路径或带查询参数的 URL。
- 输出只包含选定的诊断字段，不回显原始错误响应、堆栈或配置；已加载的两家密钥都会脱敏，外部文本移除终端控制字符。
- 不创建服务端口，不保存模型响应，不自动重试；两家依次执行，并发上限为 1，单次响应读取上限为 1 MiB。
- 当前是非流式连接测试。小说生成质量、流式解析、候选采纳、章节版本等不在本次测试范围。

## 开发检查与验收

不需要真实密钥的离线回归：

```powershell
node --test tests/api-test.test.mjs
node --check scripts/test-api.mjs
node --check scripts/api-test/config.mjs
node --check scripts/api-test/providers.mjs
git diff --check
```

离线测试覆盖请求契约、配置覆盖、成功响应、模型与用量、缺少密钥、鉴权、额度、权限、限流、服务错误、网络失败、重定向保护、异常与截断响应、输出脱敏、响应大小上限、完整请求超时和取消。均使用隔离样例与模拟响应，不产生 API 费用。

本次验证：Node.js 25.1.0 上 35 项离线测试通过，4 个 `.mjs` 文件语法检查通过，空密钥命令行返回明确的配置错误。测试使用临时配置，不依赖用户 `.env`。当前仓库未建立 CI，本次先保留可重复执行的原生检查命令，避免为独立测试工具增加构建与依赖设施；后续 CI 可直接复用。

业务验收方式：填好 `.env` 后执行默认命令，应分别显示两家服务实际结果；单独命令只调用所选服务，失败可修改配置后重试。当前密钥由用户后续填入，真实 API 连接与账户权限尚未验证，也未进行小说生成质量评估。

工程适用项：配置校验与模块隔离；SEC-01、06–10、13–14、19 的凭据、网络和输出边界；AI-01–04、07、10–15、17、19、22–23、25 的接口、资源、取消及回归要求。无前端、持久化创作数据、流式输出或自动重试，相关条款不适用。

## 官方接口依据

核对日期：2026-11-12。

- [DeepSeek 首次调用](https://api-docs.deepseek.com/)：服务地址与当前模型标识。
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion)：请求、非思考模式、结束原因与用量结构。
- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)：创建响应、输出上限、存储开关与响应结构。
- [OpenAI GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)：默认模型标识、Responses 支持及 `reasoning.effort: none` 能力。
