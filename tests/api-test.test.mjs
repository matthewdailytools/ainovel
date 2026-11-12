import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildConfig, readSettings } from '../scripts/api-test/config.mjs';
import { testProvider, TEST_PROMPT } from '../scripts/api-test/providers.mjs';
import { main } from '../scripts/test-api.mjs';

const KEYS = { DEEPSEEK_API_KEY: 'fixture-deepseek-credential', OPENAI_API_KEY: 'fixture-openai-credential' };
const deepseekBody = {
  model: 'deepseek-flash', choices: [{ message: { role: 'assistant', content: '连接成功' }, finish_reason: 'stop' }],
  usage: { prompt_tokens: 12, completion_tokens: 3, total_tokens: 15 },
};
const openaiBody = {
  model: 'gpt-6-luna', status: 'completed',
  output: [{ type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: '连接成功' }] }],
  usage: { input_tokens: 10, output_tokens: 4, total_tokens: 14 },
};
const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'x-request-id': 'fixture-request-id' } });
const config = (provider = 'deepseek') => buildConfig(provider, KEYS);
let fixtureDirectory;
let fixtureEnvFile;

before(async () => {
  fixtureDirectory = await mkdtemp(join(tmpdir(), 'ainovel-api-cli-'));
  fixtureEnvFile = join(fixtureDirectory, '.env');
  await writeFile(fixtureEnvFile, '');
});
after(async () => { await rm(fixtureDirectory, { recursive: true, force: true }); });

test('配置读取：支持 BOM、引号和环境覆盖，默认 .env 缺失可用环境变量', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'ainovel-api-test-'));
  try {
    const file = join(dir, '.env');
    await writeFile(file, '\uFEFFDEEPSEEK_API_KEY="from-file"\nOPENAI_API_KEY=local-value # comment\n');
    const settings = await readSettings(file, { DEEPSEEK_API_KEY: 'from-environment' });
    assert.equal(settings.DEEPSEEK_API_KEY, 'from-environment');
    assert.equal(settings.OPENAI_API_KEY, 'local-value');
    assert.deepEqual(await readSettings(join(dir, 'missing'), KEYS), KEYS);
    await assert.rejects(readSettings(join(dir, 'missing'), {}, true), /无法读取配置文件/);
    await writeFile(file, 'a'.repeat(65537));
    await assert.rejects(readSettings(file, {}), /64 KiB/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('缺少密钥及无效配置给出可定位错误，不回显配置值', () => {
  for (const [name, value] of [
    ['DEEPSEEK_API_KEY', ''], ['DEEPSEEK_API_KEY', 'Bearer secret'],
    ['DEEPSEEK_API_KEY', 'secret\r\nInjected: header'], ['DEEPSEEK_MODEL', ''],
    ['DEEPSEEK_MODEL', 'invalid model'], ['API_TEST_TIMEOUT_MS', '0'],
    ['API_TEST_TIMEOUT_MS', 'Infinity'], ['API_TEST_TIMEOUT_MS', '120001'],
    ['API_TEST_MAX_OUTPUT_TOKENS', '15'], ['API_TEST_MAX_OUTPUT_TOKENS', '4097'],
  ]) {
    assert.throws(() => buildConfig('deepseek', { ...KEYS, [name]: value }), new RegExp(name));
  }
  assert.throws(() => buildConfig('unknown', KEYS), /服务商/);
});

test('禁止非 HTTPS、内嵌凭据、查询参数和片段地址', () => {
  for (const url of ['http://api.example.com', 'file:///secret', 'not-a-url', 'https://user:pass@example.com', 'https://example.com/?key=secret', 'https://example.com/#fragment']) {
    assert.throws(() => buildConfig('openai', { ...KEYS, OPENAI_BASE_URL: url }), /OPENAI_BASE_URL/);
  }
});

for (const provider of ['deepseek', 'openai']) {
  test(`${provider}：真实请求契约、一次调用、正常文本与用量`, async () => {
    let calls = 0;
    const result = await testProvider(config(provider), { fetchImpl: async (url, options) => {
      calls++;
      assert.equal(url, provider === 'openai' ? 'https://api.openai.com/v1/responses' : 'https://api.deepseek.com/chat/completions');
      assert.equal(options.method, 'POST');
      assert.equal(options.redirect, 'manual');
      assert.equal(options.headers.Authorization, `Bearer ${KEYS[provider === 'openai' ? 'OPENAI_API_KEY' : 'DEEPSEEK_API_KEY']}`);
      const body = JSON.parse(options.body);
      assert.equal(body.stream, false);
      assert.ok(!options.body.includes(KEYS.DEEPSEEK_API_KEY));
      assert.ok(!options.body.includes(KEYS.OPENAI_API_KEY));
      if (provider === 'openai') {
        assert.equal(body.input, TEST_PROMPT);
        assert.equal(body.store, false);
        assert.equal(body.max_output_tokens, 64);
        assert.deepEqual(body.reasoning, { effort: 'none' });
      } else {
        assert.deepEqual(body.messages, [{ role: 'user', content: TEST_PROMPT }]);
        assert.equal(body.thinking.type, 'disabled');
        assert.equal(body.max_tokens, 64);
      }
      return jsonResponse(provider === 'openai' ? openaiBody : deepseekBody);
    } });
    assert.equal(calls, 1);
    assert.equal(result.ok, true);
    assert.equal(result.status, 'success');
    assert.equal(result.text, '连接成功');
    assert.equal(result.requestId, 'fixture-request-id');
    assert.equal(result.usage.totalTokens, provider === 'openai' ? 14 : 15);
    assert.ok(result.elapsedMs >= 0);
    assert.match(result.runId, /^[0-9a-f-]{36}$/);
    assert.equal(result.model, provider === 'openai' ? openaiBody.model : deepseekBody.model);
    assert.ok(!JSON.stringify(result).includes('fixture-deepseek-credential'));
  });
}

test('用量缺失不编造 0；自定义模型和 HTTPS 前缀生效', async () => {
  const custom = buildConfig('deepseek', { ...KEYS, DEEPSEEK_MODEL: 'deepseek-v4-pro', DEEPSEEK_BASE_URL: 'https://example.com/v1/' });
  const result = await testProvider(custom, { fetchImpl: async (url, options) => {
    assert.equal(url, 'https://example.com/v1/chat/completions');
    assert.equal(JSON.parse(options.body).model, 'deepseek-v4-pro');
    return jsonResponse({ ...deepseekBody, usage: { prompt_tokens: -1 } });
  } });
  assert.equal(result.ok, true);
  assert.deepEqual(result.usage, { inputTokens: null, outputTokens: null, totalTokens: null });
});

test('OpenAI 思考强度显式配置，留空时不向其他模型发送 reasoning 参数', async () => {
  for (const effort of ['low', '']) {
    const custom = buildConfig('openai', { ...KEYS, OPENAI_MODEL: 'custom-model', OPENAI_REASONING_EFFORT: effort });
    const result = await testProvider(custom, { fetchImpl: async (_url, options) => {
      const body = JSON.parse(options.body);
      if (effort) assert.deepEqual(body.reasoning, { effort });
      else assert.equal('reasoning' in body, false);
      return jsonResponse(openaiBody);
    } });
    assert.equal(result.ok, true);
  }
  assert.throws(() => buildConfig('openai', { ...KEYS, OPENAI_REASONING_EFFORT: 'invalid' }), /OPENAI_REASONING_EFFORT/);
});

for (const [status, code, kind] of [
  [401, 'invalid_api_key', 'authentication'], [402, null, 'quota'],
  [403, null, 'permission'], [404, 'model_not_found', 'model_or_endpoint'],
  [400, 'model_not_found', 'model_or_endpoint'], [429, null, 'rate_limit'],
  [429, 'insufficient_quota', 'quota'], [500, null, 'service'],
  [302, null, 'redirect'], [400, null, 'request'],
]) {
  test(`HTTP ${status} / ${code} 分类为 ${kind}，不自动重试或泄露服务端错误`, async () => {
    let calls = 0;
    const result = await testProvider(config(), { fetchImpl: async () => {
      calls++;
      return jsonResponse({ error: { code, message: KEYS.DEEPSEEK_API_KEY } }, status);
    } });
    assert.equal(result.ok, false);
    assert.equal(result.httpStatus, status);
    assert.equal(result.error.kind, kind);
    assert.equal(calls, 1);
    assert.ok(!JSON.stringify(result).includes(KEYS.DEEPSEEK_API_KEY));
  });
}

test('HTML 错误页保留 HTTP 分类，网络异常不回显堆栈和密钥', async () => {
  const html = await testProvider(config(), { fetchImpl: async () => new Response('<html>bad gateway</html>', { status: 503 }) });
  assert.equal(html.error.kind, 'service');
  const network = await testProvider(config(), { fetchImpl: async () => { throw new Error(KEYS.DEEPSEEK_API_KEY); } });
  assert.equal(network.error.kind, 'network');
  assert.ok(!JSON.stringify(network).includes(KEYS.DEEPSEEK_API_KEY));
});

for (const body of [null, [], {}, { ...deepseekBody, model: null }, { ...deepseekBody, choices: [] },
  { ...deepseekBody, choices: [{ message: { role: 'assistant', content: '' }, finish_reason: 'stop' }] }]) {
  test(`非法成功响应不能误报成功：${JSON.stringify(body).slice(0, 75)}`, async () => {
    const result = await testProvider(config(), { fetchImpl: async () => jsonResponse(body) });
    assert.equal(result.ok, false);
    assert.equal(result.error.kind, 'invalid_response');
  });
}

test('HTML、空响应及过大响应被拒绝', async () => {
  for (const body of ['<html>ok</html>', null, 'x'.repeat(1024 * 1024 + 1)]) {
    const result = await testProvider(config(), { fetchImpl: async () => new Response(body) });
    assert.equal(result.error.kind, 'invalid_response');
  }
});

test('截断、拒答及未完成响应不能误报成功', async () => {
  const truncated = await testProvider(config(), { fetchImpl: async () => jsonResponse({
    ...deepseekBody, choices: [{ ...deepseekBody.choices[0], finish_reason: 'length' }],
  }) });
  assert.equal(truncated.error.kind, 'incomplete');
  for (const body of [
    { ...openaiBody, status: 'incomplete' },
    { ...openaiBody, error: { message: 'internal error' } },
    { ...openaiBody, output: [{ type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'refusal', refusal: 'No' }] }] },
  ]) {
    assert.equal((await testProvider(config('openai'), { fetchImpl: async () => jsonResponse(body) })).ok, false);
  }
});

test('响应文本、模型和请求 ID 都脱敏并清除终端控制字符', async () => {
  const body = { ...deepseekBody, model: KEYS.OPENAI_API_KEY, choices: [{
    message: { role: 'assistant', content: `\x1b[31m${KEYS.DEEPSEEK_API_KEY}\n${KEYS.OPENAI_API_KEY}` }, finish_reason: 'stop',
  }] };
  const result = await testProvider(config(), { secrets: Object.values(KEYS), fetchImpl: async () => new Response(JSON.stringify(body), {
    headers: { 'x-request-id': KEYS.OPENAI_API_KEY },
  }) });
  assert.equal(result.ok, true);
  assert.ok(!JSON.stringify(result).includes(KEYS.DEEPSEEK_API_KEY));
  assert.ok(!JSON.stringify(result).includes(KEYS.OPENAI_API_KEY));
  assert.doesNotMatch(result.text, /[\r\n\x1b]/);
});

const pendingFetch = async (_url, { signal }) => new Promise((_resolve, reject) => {
  signal.addEventListener('abort', () => reject(signal.reason), { once: true });
});

test('超时中止请求且不重试', async () => {
  let calls = 0;
  const result = await testProvider({ ...config(), timeoutMs: 15 }, { fetchImpl: (...args) => { calls++; return pendingFetch(...args); } });
  assert.equal(result.error.kind, 'timeout');
  assert.equal(calls, 1);
});

test('响应体停滞也受完整请求时限约束', async () => {
  const result = await testProvider({ ...config(), timeoutMs: 15 }, { fetchImpl: async (_url, { signal }) => new Response(new ReadableStream({
    start(controller) { signal.addEventListener('abort', () => controller.error(signal.reason), { once: true }); },
  })) });
  assert.equal(result.error.kind, 'timeout');
});

test('取消在途请求，保留取消终态', async () => {
  const controller = new AbortController();
  const waiting = testProvider(config(), { signal: controller.signal, fetchImpl: pendingFetch });
  controller.abort();
  const result = await waiting;
  assert.equal(result.status, 'cancelled');
  assert.equal(result.ok, false);
  assert.equal(result.error.kind, 'cancelled');
});

test('已取消任务不发起网络请求', async () => {
  const controller = new AbortController();
  controller.abort();
  let calls = 0;
  const result = await testProvider(config(), { signal: controller.signal, fetchImpl: async () => { calls++; } });
  assert.equal(calls, 0);
  assert.equal(result.status, 'cancelled');
});

async function cli(args, environment, fetchImpl, signal) {
  const output = [];
  const exitCode = await main(['--env-file', fixtureEnvFile, ...args], { environment, fetchImpl, signal, output: (line) => output.push(line) });
  return { exitCode, output: output.join('\n') };
}

test('命令行：两家均成功，JSON 可解析且无密钥', async () => {
  let calls = 0;
  const result = await cli(['all', '--json'], KEYS, async (url) => { calls++; return jsonResponse(url.endsWith('/responses') ? openaiBody : deepseekBody); });
  const json = JSON.parse(result.output);
  assert.equal(result.exitCode, 0);
  assert.equal(json.ok, true);
  assert.equal(json.results.length, 2);
  assert.equal(calls, 2);
  for (const secret of Object.values(KEYS)) assert.ok(!result.output.includes(secret));
});

test('命令行：一家配置错误仍测试另一家，整体退出码为 1', async () => {
  let calls = 0;
  const result = await cli(['--json'], { ...KEYS, DEEPSEEK_API_KEY: '' }, async () => { calls++; return jsonResponse(openaiBody); });
  const json = JSON.parse(result.output);
  assert.equal(result.exitCode, 1);
  assert.equal(json.results[0].error.kind, 'configuration');
  assert.equal(json.results[1].ok, true);
  assert.equal(calls, 1);
});

test('命令行：chatgpt 别名、帮助与无效参数', async () => {
  assert.equal((await cli(['chatgpt', '--json'], KEYS, async () => jsonResponse(openaiBody))).exitCode, 0);
  let calls = 0;
  const noRequest = async () => { calls++; };
  assert.equal((await cli(['--help'], {}, noRequest)).exitCode, 0);
  assert.equal((await cli(['unknown'], {}, noRequest)).exitCode, 2);
  assert.equal((await cli(['deepseek', 'openai'], {}, noRequest)).exitCode, 2);
  assert.equal((await cli(['--bad-option'], {}, noRequest)).exitCode, 2);
  const missing = await cli(['--env-file', join(tmpdir(), 'ainovel-config-not-present.env'), '--json'], {}, noRequest);
  assert.equal(missing.exitCode, 2);
  assert.equal(JSON.parse(missing.output).error.kind, 'configuration');
  assert.equal(calls, 0);
});

test('命令行取消后不继续第二家服务，退出码为 130', async () => {
  const controller = new AbortController();
  let calls = 0;
  const result = await cli(['all', '--json'], KEYS, async (_url, { signal }) => {
    calls++;
    controller.abort();
    throw signal.reason;
  }, controller.signal);
  const json = JSON.parse(result.output);
  assert.equal(result.exitCode, 130);
  assert.equal(json.cancelled, true);
  assert.equal(json.results[0].status, 'cancelled');
  assert.equal(calls, 1);
});
