import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { buildConfig, ConfigError, PROVIDERS, readSettings, redact } from './api-test/config.mjs';
import { testProvider } from './api-test/providers.mjs';

const HELP = `用法：node scripts/test-api.mjs [all|deepseek|openai|chatgpt] [--json] [--env-file 路径]

默认读取项目根目录 .env，每家服务发送一次简短请求（会产生少量 API 用量）。
all 测试两家；chatgpt 是 openai 的别名。环境变量优先于配置文件。
--json      输出脱敏的 JSON 结果，便于自动检查
--env-file  使用指定配置文件（相对当前目录）
--help      查看帮助，不发起请求
Ctrl+C      取消本地等待；不保证服务端未计费
详情见 API_TEST.md。`;

export async function main(args = process.argv.slice(2), { environment = process.env, fetchImpl = fetch, output = console.log, signal } = {}) {
  let options;
  try {
    options = parseArgs({ args, allowPositionals: true, options: {
      json: { type: 'boolean' }, help: { type: 'boolean' }, 'env-file': { type: 'string' },
    } });
  } catch {
    output(`参数不正确。\n${HELP}`);
    return 2;
  }
  if (options.values.help) { output(HELP); return 0; }
  const target = options.positionals[0] ?? 'all';
  if (options.positionals.length > 1 || !['all', 'deepseek', 'openai', 'chatgpt'].includes(target)) {
    output(`服务商参数不正确。\n${HELP}`);
    return 2;
  }
  const selected = target === 'all' ? ['deepseek', 'openai'] : [target === 'chatgpt' ? 'openai' : target];
  const file = options.values['env-file'] ? resolve(options.values['env-file']) : fileURLToPath(new URL('../.env', import.meta.url));
  const results = [];
  let settings;
  try {
    settings = await readSettings(file, environment, Boolean(options.values['env-file']));
  } catch (error) {
    const message = error instanceof ConfigError ? error.message : '配置读取失败，请检查本地文件。';
    output(options.values.json ? JSON.stringify({ ok: false, error: { kind: 'configuration', message } }) : message);
    return 2;
  }
  const secrets = [settings.DEEPSEEK_API_KEY?.trim(), settings.OPENAI_API_KEY?.trim()].filter(Boolean);
  if (!options.values.json) output('API 连通性测试：仅发送固定测试句，每家最多一次请求；按 Ctrl+C 取消。');
  for (const provider of selected) {
    if (signal?.aborted) break;
    let result;
    try {
      const config = buildConfig(provider, settings);
      if (!options.values.json) output(redact(`正在测试 ${PROVIDERS[provider].label} · ${config.model} · ${config.endpoint}`, secrets));
      result = await testProvider(config, { fetchImpl, signal, secrets });
    } catch (error) {
      result = { provider, ok: false, status: 'failed', error: { kind: 'configuration', message: error instanceof ConfigError ? error.message : '配置无效，请检查 .env 文件。' } };
    }
    results.push(result);
    if (!options.values.json) {
      if (result.ok) {
        output(`✓ ${PROVIDERS[provider].label} 连接成功（${result.elapsedMs} ms）`);
        output(`  实际模型：${result.model}\n  回复：${result.text}`);
        output(`  token 用量：输入 ${result.usage.inputTokens ?? '未返回'} / 输出 ${result.usage.outputTokens ?? '未返回'} / 合计 ${result.usage.totalTokens ?? '未返回'}`);
      } else {
        output(redact(`✗ ${PROVIDERS[provider].label}：${result.error.message}${result.httpStatus ? `（HTTP ${result.httpStatus}）` : ''}`, secrets));
      }
      if (result.requestId) output(`  请求 ID：${result.requestId}`);
    }
  }
  const ok = results.length === selected.length && results.every((result) => result.ok);
  if (options.values.json) output(JSON.stringify(redact({ ok, cancelled: Boolean(signal?.aborted), results }, secrets), null, 2));
  if (signal?.aborted) return 130;
  return ok ? 0 : 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  process.on('SIGINT', cancel);
  try {
    process.exitCode = await main(undefined, { signal: controller.signal });
  } finally {
    process.off('SIGINT', cancel);
  }
}
