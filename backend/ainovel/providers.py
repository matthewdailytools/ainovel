"""Streaming adapters with bounded input/output, cancellation and shared money reservation."""

import asyncio
import hashlib
import json

import httpx

from .db import DomainError
from .settings import CONTEXT_SAFETY_TOKENS
from .openai_schema import VERSION as WIRE_SCHEMA_VERSION, strict_schema


class ModelClient:
    def __init__(self, settings, budget, transport=None):
        self.settings, self.budget, self.transport = settings, budget, transport

    async def generate(
        self,
        account,
        task_id,
        system,
        prompt,
        max_output=8192,
        on_text=None,
        cancelled=None,
        run_limit=None,
        schema=None,
    ):
        provider = self.settings.providers.get(account)
        if not provider or not provider.key:
            raise DomainError("configuration", "所选模型账户未配置密钥。")
        if type(max_output) is not int or max_output <= 0:
            raise DomainError("configuration", "单次输出上限必须为正整数。")
        output, usage, completed = "", None, False
        received_terminal = False
        input_tokens = output_tokens = None
        actual_model, request_id, actual_tier = "", "", ""
        repairable = False

        def identifier(value):
            if not isinstance(value, str):
                return ""
            for configured in self.settings.providers.values():
                if configured.key:
                    value = value.replace(configured.key, "[REDACTED]")
            return "".join(c for c in value if 32 <= ord(c) < 127)[:160]

        def service_message(value):
            if not isinstance(value, str):
                return ""
            for configured in self.settings.providers.values():
                if configured.key:
                    value = value.replace(configured.key, "[REDACTED]")
            return " ".join("".join(c for c in value if c.isprintable()).split())[:600]

        body = {"model": provider.model, "stream": True}
        if provider.kind == "openai":
            body.update(
                input=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                max_output_tokens=max_output,
                store=False,
                service_tier="default",
            )
            if provider.reasoning:
                body["reasoning"] = {"effort": provider.reasoning}
            endpoint = provider.base_url + "/responses"
            if schema is not None:
                body["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "ainovel_result",
                        "strict": True,
                        "schema": strict_schema(schema),
                    }
                }
        else:
            body.update(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                max_tokens=max_output,
                thinking={"type": "disabled"},
                stream_options={"include_usage": True},
            )
            endpoint = provider.base_url + "/chat/completions"
            if schema:
                body["response_format"] = {"type": "json_object"}
        # Serialize once so both gates inspect the exact UTF-8 body sent below,
        # including schema, JSON escaping and provider-specific request fields.
        request_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
        request_bytes = len(request_body)
        request_metadata = {}
        if provider.kind == "openai" and schema is not None:
            request_metadata = {
                "wire_schema_version": WIRE_SCHEMA_VERSION,
                "wire_schema_sha256": hashlib.sha256(
                    json.dumps(
                        body["text"]["format"]["schema"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                ).hexdigest(),
                "request_sha256": hashlib.sha256(request_body).hexdigest(),
                "request_bytes": request_bytes,
            }
        if request_bytes > provider.max_input_bytes:
            raise DomainError("context_limit", "包括结构契约的请求超过账户字节上限，未截断或付费调用。")
        # Bytes are deliberately a conservative token upper bound, not a model
        # tokenizer estimate. A window is only taken from explicit configuration.
        if (
            provider.context_window_tokens is not None
            and request_bytes + max_output + CONTEXT_SAFETY_TOKENS > provider.context_window_tokens
        ):
            raise DomainError(
                "context_limit", "请求字节数、预留输出与安全余量超过已配置模型窗口，未截断或付费调用。"
            )
        reservation = self.budget.reserve(provider, task_id, request_bytes + 2048, max_output, run_limit)
        sent = False
        try:
            if cancelled and cancelled():
                self.budget.settle(reservation, known_no_charge=True)
                raise DomainError("cancelled", "请求发送前已取消。", 409)
            async with asyncio.timeout(self.settings.timeout):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    follow_redirects=False,
                    timeout=httpx.Timeout(self.settings.idle_timeout, connect=20),
                ) as client:
                    sent = True
                    async with client.stream(
                        "POST",
                        endpoint,
                        headers={
                            "Authorization": "Bearer " + provider.key,
                            "Content-Type": "application/json",
                        },
                        content=request_body,
                    ) as response:
                        request_id = identifier(response.headers.get("x-request-id", ""))
                        if response.status_code != 200:
                            # Only explicit rejection can release a reservation. Unknown server failures stay held.
                            known_rejection = response.status_code in (400, 401, 402, 403, 404, 422, 429)
                            self.budget.settle(reservation, known_no_charge=known_rejection)
                            category = {
                                401: "authentication",
                                402: "quota",
                                403: "permission",
                                404: "model_or_endpoint",
                                429: "rate_limit",
                            }.get(response.status_code, "provider_error")
                            hint = ""
                            rejected = bytearray()
                            async for piece in response.aiter_bytes():
                                rejected.extend(piece[: 4096 - len(rejected)])
                                if len(rejected) >= 4096:
                                    break
                            try:
                                detail = json.loads(rejected).get("error", {})
                                hint = detail.get("message", "") if isinstance(detail, dict) else ""
                                error_code = detail.get("code") if isinstance(detail, dict) else None
                                category = {
                                    "insufficient_quota": "quota",
                                    "invalid_api_key": "authentication",
                                    "model_not_found": "model_or_endpoint",
                                }.get(error_code, category)
                            except ValueError, AttributeError:
                                pass
                            if isinstance(hint, str):
                                for configured in self.settings.providers.values():
                                    if configured.key:
                                        hint = hint.replace(configured.key, "[REDACTED]")
                                hint = " ".join("".join(c for c in hint if c.isprintable()).split())[:600]
                            else:
                                hint = ""
                            raise DomainError(
                                category,
                                f"模型请求未成功（HTTP {response.status_code}）。未切换账户或自动重试。"
                                + (" 服务提示：" + hint if hint else ""),
                                502,
                            )
                        frame = []
                        async for line in response.aiter_lines():
                            if cancelled and cancelled():
                                raise DomainError("cancelled", "已取消本地生成；服务端可能已计费。", 409)
                            if len(line) > 1_000_000:
                                raise DomainError("invalid_response", "模型事件过大，已停止读取。")
                            if line:
                                if line.startswith("data:"):
                                    frame.append(line[5:].lstrip())
                                continue
                            if not frame:
                                continue
                            payload, frame = "\n".join(frame), []
                            if payload == "[DONE]":
                                received_terminal = True
                                break
                            try:
                                event = json.loads(payload)
                            except (ValueError, TypeError) as error:
                                raise DomainError(
                                    "invalid_response", "模型流包含无效 JSON；部分候选已保留。"
                                ) from error
                            delta = ""
                            if not isinstance(event, dict):
                                raise DomainError("invalid_response", "模型事件不是有效对象，未采纳。")
                            metadata = event.get("response", {}) if provider.kind == "openai" else event
                            actual_model = identifier(metadata.get("model")) or actual_model
                            request_id = request_id or identifier(metadata.get("id"))
                            actual_tier = identifier(metadata.get("service_tier")) or actual_tier
                            if provider.kind == "openai":
                                event_type = event.get("type")
                                if event_type == "response.output_text.delta":
                                    delta = event.get("delta", "")
                                if event_type == "response.completed":
                                    result = event.get("response", {})
                                    completed = result.get("status") == "completed"
                                    received_terminal = True
                                    actual = result.get("usage", {}) or {}
                                    input_tokens, output_tokens = (
                                        actual.get("input_tokens"),
                                        actual.get("output_tokens"),
                                    )
                                if event_type in ("error", "response.failed", "response.incomplete"):
                                    incomplete = event.get("response", {}) or {}
                                    actual = incomplete.get("usage", {}) or {}
                                    input_tokens, output_tokens = (
                                        actual.get("input_tokens"),
                                        actual.get("output_tokens"),
                                    )
                                    reason = (incomplete.get("incomplete_details") or {}).get("reason")
                                    repairable = reason == "max_output_tokens"
                                    raw_error = incomplete.get("error") or event.get("error") or event
                                    raw_error = raw_error if isinstance(raw_error, dict) else {}
                                    details = {
                                        "event_type": identifier(event_type),
                                        "status": identifier(incomplete.get("status")),
                                        "provider_code": identifier(
                                            raw_error.get("code") or raw_error.get("type")
                                        ),
                                        "reason": identifier(reason),
                                        "service_tier": actual_tier,
                                    }
                                    hint = service_message(raw_error.get("message"))
                                    error = DomainError(
                                        "incomplete"
                                        if event_type == "response.incomplete"
                                        else "provider_error",
                                        (
                                            "模型达到输出上限，已保留部分产物。"
                                            if repairable
                                            else "模型服务未完整结束，产物与费用状态已保留。"
                                        )
                                        + (
                                            f" 服务代码：{details['provider_code'] or details['reason']}。"
                                            if details["provider_code"] or details["reason"]
                                            else ""
                                        )
                                        + (" 服务提示：" + hint if hint else ""),
                                    )
                                    error.provider_details = details
                                    raise error
                            else:
                                for choice in event.get("choices", []):
                                    delta += choice.get("delta", {}).get("content") or ""
                                    if choice.get("finish_reason") is not None:
                                        completed = choice["finish_reason"] == "stop"
                                        repairable = choice["finish_reason"] == "length"
                                actual = event.get("usage")
                                if actual:
                                    input_tokens, output_tokens = (
                                        actual.get("prompt_tokens"),
                                        actual.get("completion_tokens"),
                                    )
                            if delta:
                                if not isinstance(delta, str):
                                    raise DomainError("invalid_response", "模型返回了无效文本。")
                                output += delta
                                if len(output.encode("utf-8")) > 1_000_000:
                                    raise DomainError("output_limit", "输出超过本机单次保护上限。")
                                if on_text:
                                    await on_text(delta)
                            if provider.kind == "openai" and received_terminal:
                                break
            if not completed or not received_terminal or not output.strip():
                raise DomainError("incomplete", "模型流中断或为空，不能作为完整稿件采纳。")
            if (
                type(input_tokens) is int
                and type(output_tokens) is int
                and input_tokens >= 0
                and output_tokens >= 0
            ):
                usage = {"input": input_tokens, "output": output_tokens}
            self.budget.settle(reservation, usage)
            self.budget.metadata(reservation, actual_model, request_id)
            if not usage:
                raise DomainError(
                    "usage_unknown", "文本已保留，但服务未返回用量；费用预留保持占用，自动推进已停止。", 409
                )
            return {
                "text": output.strip(),
                "usage": usage,
                "call_id": reservation,
                "model": actual_model or provider.model,
                "requested_model": provider.model,
                "request_id": request_id,
                "service_tier": actual_tier,
                **({"request_metadata": request_metadata} if request_metadata else {}),
            }
        except BaseException as error:
            if (
                type(input_tokens) is int
                and type(output_tokens) is int
                and input_tokens >= 0
                and output_tokens >= 0
            ):
                usage = {"input": input_tokens, "output": output_tokens}
            # Re-settling is idempotent; a known rejection remains at zero.
            self.budget.settle(reservation, usage, known_no_charge=not sent)
            self.budget.metadata(reservation, actual_model, request_id)
            if output:
                error.partial_text = output
            error.known_usage = bool(usage)
            error.repairable = repairable
            if request_metadata:
                error.provider_details = {
                    **getattr(error, "provider_details", {}),
                    "request_metadata": request_metadata,
                }
            if isinstance(error, (httpx.TimeoutException, TimeoutError)):
                unknown = DomainError(
                    "unknown_timeout",
                    "请求超时，服务端结果和费用可能未知；未自动重发，请查看预留与已保存产物。",
                    502,
                )
                unknown.partial_text = output
                if request_metadata:
                    unknown.provider_details = error.provider_details
                raise unknown from error
            if isinstance(error, httpx.HTTPError):
                unknown = DomainError(
                    "network_unknown", "网络连接中断，费用预留保留；未自动重试或切换账户。", 502
                )
                unknown.partial_text = output
                if request_metadata:
                    unknown.provider_details = error.provider_details
                raise unknown from error
            raise
