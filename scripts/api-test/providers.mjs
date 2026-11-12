import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { redact } from './config.mjs';

export const TEST_PROMPT = '这是 API 连通性测试，请只回复：连接成功';
export const PROMPT_VERSION = 'api-connectivity-v1';
const MAX_RESPONSE_BYTES = 1024 * 1024;

class ApiError extends Error {
  constructor(kind, message, httpStatus) {
    super(message);
    this.kind = kind;
    this.httpStatus = httpStatus;
  }
}

function requestBody(config) {
  if (config.provider === 'openai') {
    return {
      model: config.model, input: TEST_PROMPT, max_output_tokens: config.maxOutputTokens, store: false, stream: false,
      ...(config.reasoningEffort ? { reasoning: { effort: config.reasoningEffort } } : {}),
    };
  }
  return {
    model: config.model, messages: [{ role: 'user', content: TEST_PROMPT }],
    max_tokens: config.maxOutputTokens, thinking: { type: 'disabled' }, stream: false,
  };
}

function httpError(status, body) {
  const code = body?.error?.code;
  if (status === 401) return new ApiError('authentication', '密钥无效或已失效，请检查对应的 API Key。', status);
  if (status === 402 || code === 'insufficient_quota') {
    return new ApiError('quota', '账户额度不足，请检查服务商控制台的余额与 API 计费配置。', status);
  }
  if (status === 403) return new ApiError('permission', '访问被拒绝，请检查账户、地区或项目权限。', status);
  if (status === 404 || code === 'model_not_found') {
    return new ApiError('model_or_endpoint', '模型或接口不可用，请检查模型标识、模型权限和 BASE_URL。', status);
  }
  if (status === 429) return new ApiError('rate_limit', '请求受到限流，请稍后手动重试并检查用量限制。', status);
  if (status >= 300 && status < 400) {
    return new ApiError('redirect', '服务地址返回重定向，已阻止转发密钥；请核对 BASE_URL。', status);
  }
  if (status >= 500) return new ApiError('service', '服务暂不可用，请稍后手动重试。', status);
  return new ApiError('request', '请求被拒绝，请检查模型及参数是否受该接口支持。', status);
}

async function readJson(response) {
  const reader = response.body?.getReader();
  if (!reader) throw new ApiError('invalid_response', '服务返回了空响应，请检查接口地址。');
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new ApiError('invalid_response', '服务响应超过 1 MiB，已停止读取，请检查接口地址。');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new ApiError('invalid_response', '服务返回的内容不是有效 JSON，请检查接口地址或网关。');
  }
}

function tokenCount(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function normalize(body, config) {
  const openai = config.provider === 'openai';
  let text;
  let finishReason;
  if (openai) {
    finishReason = body?.status;
    text = Array.isArray(body?.output) ? body.output
      .filter((item) => item?.type === 'message' && item.role === 'assistant' && item.status === 'completed')
      .flatMap((item) => Array.isArray(item.content) ? item.content : [])
      .filter((part) => part?.type === 'output_text' && typeof part.text === 'string')
      .map((part) => part.text).join('') : '';
  } else {
    finishReason = body?.choices?.[0]?.finish_reason;
    text = body?.choices?.[0]?.message?.role === 'assistant' ? body.choices[0].message.content : '';
  }
  if (typeof body?.model !== 'string' || !body.model || typeof text !== 'string' || !text.trim() || body?.error) {
    throw new ApiError('invalid_response', '响应缺少模型或有效文本，可能是拒答、异常输出或接口不兼容。');
  }
  if (finishReason !== (openai ? 'completed' : 'stop')) {
    throw new ApiError('incomplete', '生成未完整结束，请检查输出 token 上限及模型参数后手动重试。');
  }
  return {
    model: body.model, text: text.trim(), finishReason,
    usage: {
      inputTokens: tokenCount(openai ? body.usage?.input_tokens : body.usage?.prompt_tokens),
      outputTokens: tokenCount(openai ? body.usage?.output_tokens : body.usage?.completion_tokens),
      totalTokens: tokenCount(body.usage?.total_tokens),
    },
  };
}

export async function testProvider(config, { fetchImpl = fetch, signal, secrets = [] } = {}) {
  const started = performance.now();
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, config.timeoutMs);
  const requestSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
  const result = {
    provider: config.provider, runId: randomUUID(), checkedAt: new Date().toISOString(),
    promptVersion: PROMPT_VERSION, requestedModel: config.model, endpoint: config.endpoint,
    ok: false, status: 'failed',
  };
  try {
    requestSignal.throwIfAborted();
    const response = await fetchImpl(config.endpoint, {
      method: 'POST', redirect: 'manual', signal: requestSignal,
      headers: { Authorization: `Bearer ${config.apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody(config)),
    });
    result.httpStatus = response.status;
    result.requestId = response.headers.get('x-request-id') ?? response.headers.get('x-ds-request-id');
    let body;
    try {
      body = await readJson(response);
    } catch (error) {
      // HTTP 错误页可能是 HTML；仍优先显示可操作的鉴权、限流等分类。
      if (response.ok || timedOut || signal?.aborted) throw error;
    }
    requestSignal.throwIfAborted();
    if (!response.ok) throw httpError(response.status, body);
    Object.assign(result, normalize(body, config), { ok: true, status: 'success' });
  } catch (error) {
    if (signal?.aborted) {
      result.status = 'cancelled';
      result.error = { kind: 'cancelled', message: '已取消本地等待；服务端可能已处理请求并计费。' };
    } else if (timedOut) {
      result.error = { kind: 'timeout', message: '请求超时，请检查网络或调大 API_TEST_TIMEOUT_MS；本次未自动重试，服务端可能已处理请求。' };
    } else if (error instanceof ApiError) {
      result.error = { kind: error.kind, message: error.message, httpStatus: error.httpStatus };
    } else {
      result.error = { kind: 'network', message: '连接失败，请检查网络、DNS、代理和 HTTPS 证书，不要关闭证书校验。' };
    }
  } finally {
    clearTimeout(timer);
  }
  result.elapsedMs = Math.round(performance.now() - started);
  return redact(result, [config.apiKey, ...secrets]);
}
