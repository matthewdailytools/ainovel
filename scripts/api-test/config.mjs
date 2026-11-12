import { readFile } from 'node:fs/promises';
import { parseEnv } from 'node:util';

export const PROVIDERS = Object.freeze({
  deepseek: { label: 'DeepSeek', prefix: 'DEEPSEEK', model: 'deepseek-flash', baseUrl: 'https://api.deepseek.com' },
  openai: { label: 'OpenAI / ChatGPT', prefix: 'OPENAI', model: 'gpt-6-luna', baseUrl: 'https://api.openai.com/v1' },
});

export class ConfigError extends Error {}

export async function readSettings(file, environment = process.env, required = false) {
  let local = {};
  try {
    const content = await readFile(file, 'utf8');
    if (Buffer.byteLength(content) > 65536) throw new ConfigError('配置文件不能超过 64 KiB。');
    local = parseEnv(content.replace(/^\uFEFF/, ''));
  } catch (error) {
    if (error instanceof ConfigError) throw error;
    if (error.code !== 'ENOENT' || required) {
      throw new ConfigError('无法读取配置文件，请检查 --env-file 路径及文件读取权限。');
    }
  }
  return { ...local, ...environment };
}

function integer(settings, name, fallback, min, max) {
  const raw = settings[name] ?? String(fallback);
  const value = Number(raw);
  if (!/^\d+$/.test(raw) || !Number.isSafeInteger(value) || value < min || value > max) {
    throw new ConfigError(`${name} 必须是 ${min}–${max} 之间的整数。`);
  }
  return value;
}

export function buildConfig(provider, settings) {
  const preset = PROVIDERS[provider];
  if (!preset) throw new ConfigError('服务商必须是 deepseek 或 openai。');
  const keyName = `${preset.prefix}_API_KEY`;
  const apiKey = settings[keyName]?.trim();
  if (!apiKey) throw new ConfigError(`缺少 ${keyName}，请在本地 .env 文件中填写。`);
  if (!/^[\x21-\x7e]{1,4096}$/.test(apiKey)) {
    throw new ConfigError(`${keyName} 格式不正确，请只填写密钥，不含空格、换行或 Bearer 前缀。`);
  }
  const modelName = `${preset.prefix}_MODEL`;
  const model = settings[modelName] ?? preset.model;
  if (typeof model !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(model)) {
    throw new ConfigError(`${modelName} 必须填写有效的模型标识。`);
  }
  const urlName = `${preset.prefix}_BASE_URL`;
  let url;
  try {
    url = new URL(settings[urlName] ?? preset.baseUrl);
  } catch {
    throw new ConfigError(`${urlName} 必须是有效的 HTTPS 服务地址。`);
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
    throw new ConfigError(`${urlName} 必须使用 HTTPS，且不能包含账号、密码、查询参数或片段。`);
  }
  const reasoningEffort = provider === 'openai' ? (settings.OPENAI_REASONING_EFFORT ?? 'none') : '';
  if (!['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'].includes(reasoningEffort)) {
    throw new ConfigError('OPENAI_REASONING_EFFORT 必须为 none、minimal、low、medium、high、xhigh、max 或留空。');
  }
  return {
    provider, apiKey, model,
    reasoningEffort,
    endpoint: url.href.replace(/\/+$/, '') + (provider === 'openai' ? '/responses' : '/chat/completions'),
    timeoutMs: integer(settings, 'API_TEST_TIMEOUT_MS', 30000, 1000, 120000),
    maxOutputTokens: integer(settings, 'API_TEST_MAX_OUTPUT_TOKENS', 64, 16, 4096),
  };
}

export function redact(value, secrets = []) {
  if (typeof value === 'string') {
    let result = value;
    for (const secret of secrets.filter(Boolean).sort((a, b) => b.length - a.length)) {
      result = result.split(secret).join('[已隐藏密钥]');
    }
    // 外部响应不能向终端注入控制序列或伪造多行日志。
    return result.replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, ' ');
  }
  if (Array.isArray(value)) return value.map((entry) => redact(entry, secrets));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, redact(entry, secrets)]));
  }
  return value;
}
