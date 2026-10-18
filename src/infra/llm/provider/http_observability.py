"""裁判调用的请求级观测：httpx event hooks + 按 trace 归集的证据桶。

为什么必须挂 `http_async_client`：ragas 的裁判调用走**异步**（`evaluate` 提交
`metric.single_turn_ascore` → `LangchainLLMWrapper.agenerate_text` →
`await agenerate_chat_result`），而 langchain 的 `http_client` 字段**只喂同步
client**（`chat_models/base.py:806`）⇒ 挂错字段 hooks 一条都不会响（实测
A 臂 4 事件 / B 臂 0 事件，见 docs/tmp/deepeval-poc/RESULTS.md「Hook 冒烟」）。

hook 必须是 `async def`：异步 client 的调用形式是 `await hook(response)`
（`httpx/_client.py:1697`），同步 hook 会抛 TypeError；读体必须
`await response.aread()`（同步 read() 在异步流上抛 RuntimeError）。
"""

from __future__ import annotations

import json
import time

import httpx
from loguru import logger

from src.core import logging as core_logging
from src.core.log_events import Event


class HookEvidence:
    """按样本归集的观测证据（裁判 client 跨样本复用 ⇒ 用可变桶，不闭包固定 id）。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.measures: list[dict] = []
        self._trace_id: str | None = None
        self._run_trace_id: str | None = None
        self._judge_model: str | None = None
        self._measure: dict | None = None
        self._calls: dict[int, dict] = {}
        self._call_index = 0
        self._event_start = 0

    def set_run(self, run_trace_id: str | None, judge_model: str | None = None) -> None:
        """绑定整轮 run trace 与裁判模型；`reset()` 不会清除它们。"""
        self._run_trace_id = run_trace_id
        self._judge_model = judge_model

    def mark(self, trace_id: str | None) -> None:
        """切到某题的证据窗口（评分串行，并发=1，故单槽安全）。"""
        self._trace_id = trace_id

    def reset(self) -> None:
        """清当前题窗口；保留 run trace。"""
        self.events.clear()
        self.measures.clear()
        self._trace_id = None
        self._measure = None
        self._calls = {}
        self._call_index = 0
        self._event_start = 0

    @property
    def has_active_measure(self) -> bool:
        """当前是否有未结束的 measure（决定 HTTP 调用是否归集到调用明细）。"""
        return self._measure is not None

    def begin_measure(
        self,
        *,
        measure_id: str,
        metric: str,
        attempt: int,
        trace_id: str | None,
        q_index: int | None,
    ) -> None:
        """开始一次指标打分；后续 HTTP 调用归到该 measure。"""
        self._trace_id = trace_id
        self._measure = {
            "judge_measure_id": measure_id,
            "metric": metric,
            "attempt": attempt,
            "q_index": q_index,
        }
        self._calls = {}
        self._call_index = 0
        self._event_start = len(self.events)

    def end_measure(self, *, reason: str | None, error: str) -> dict:
        """结束 measure：补齐无 response 的调用、写 JUDGE_HTTP、返回汇总。"""
        measure = self._measure or {
            "judge_measure_id": None,
            "metric": None,
            "attempt": None,
            "q_index": None,
        }
        for idx in range(1, self._call_index + 1):
            call = self._calls.setdefault(
                idx,
                {
                    "call_index": idx,
                    "judge_call_id": f"{measure['judge_measure_id']}:{idx}",
                    "status": None,
                    "finish_reasons": None,
                    "tokens_in": None,
                    "tokens_out": None,
                    "latency_ms": None,
                    "json_ok": None,
                    "error": "no_response",
                },
            )
            if call.get("status") is None and call.get("error") is None:
                # 已由 on_request 建好记录、但本次请求没走到 on_response
                call["error"] = "no_response"
        calls = [self._calls[i] for i in sorted(self._calls)]

        for call in calls:
            if call.get("status") is None:
                self._log_http(call)

        responses = [c for c in calls if c.get("status") is not None]
        tokens_ok = bool(responses) and all(
            c.get("tokens_in") is not None and c.get("tokens_out") is not None
            for c in responses
        )
        record = {
            "judge_measure_id": measure["judge_measure_id"],
            "metric": measure["metric"],
            "attempt": measure["attempt"],
            "model": self._judge_model,
            "trace_id": self._trace_id,
            "q_index": measure["q_index"],
            "run_trace_id": self._run_trace_id,
            "request_count": len(calls),
            "response_count": len(responses),
            "statuses": [c.get("status") for c in calls],
            "tokens_in": sum(c["tokens_in"] for c in responses) if tokens_ok else None,
            "tokens_out": sum(c["tokens_out"] for c in responses)
            if tokens_ok
            else None,
            "error_count": sum(
                1
                for c in calls
                if c.get("status") is None
                or not (200 <= c["status"] < 300)
                or c.get("json_ok") is False
            ),
            "calls": [
                {k: v for k, v in c.items() if not k.startswith("_")} for c in calls
            ],
            "reason": reason,
            "error": error,
        }
        self.measures.append(record)
        self._measure = None
        self._calls = {}
        return record

    def _log_http(self, call: dict) -> None:
        measure = self._measure or {}
        try:
            core_logging.log_event(
                Event.JUDGE_HTTP,
                run_trace_id=self._run_trace_id,
                trace_id=self._trace_id,
                q_index=measure.get("q_index"),
                judge_measure_id=measure.get("judge_measure_id"),
                judge_call_id=call.get("judge_call_id"),
                component="judge",
                metric=measure.get("metric"),
                attempt=measure.get("attempt"),
                call_index=call.get("call_index"),
                status=call.get("status"),
                finish_reasons=call.get("finish_reasons"),
                tokens_in=call.get("tokens_in"),
                tokens_out=call.get("tokens_out"),
                latency_ms=call.get("latency_ms"),
                json_ok=call.get("json_ok"),
                error=call.get("error"),
            )
        except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
            logger.warning("[provider] judge http log failed err={}", exc)

    def append(self, rec: dict) -> None:
        """追加一条原始事件（保留 trace_id，供异常判据与重试退避使用）。"""
        rec["trace_id"] = self._trace_id
        self.events.append(rec)

    @property
    def status_codes(self) -> list[int]:
        return [e["status"] for e in self.events if e.get("phase") == "response"]

    @property
    def non_2xx(self) -> bool:
        return any(not (200 <= s < 300) for s in self.status_codes)

    @property
    def abnormal(self) -> bool:
        """响应侧异常：非 2xx / 响应体非合法 JSON / `finish_reason` 非 stop。

        **不覆盖传输层失败**：httpx 0.28.1 只派发 `request` / `response` 两个 hook，
        没有异常 hook；连接/池/DNS 失败会在响应 hook 循环**之前**抛出 ⇒ 表现为
        「有 request 事件、无 response 事件」（观测缺口，由 `end_measure()` 的
        `no_response` 调用明细补齐）。靠本属性判"没事"是错的，调用方若要区分，
        须显式检查"该格是否有 response 事件"。
        """
        for e in self.events:
            if e.get("phase") != "response":
                continue
            if not (200 <= e["status"] < 300):
                return True
            if not e.get("json_ok"):
                return True
            if e.get("finish_reason") and any(
                fr != "stop" for fr in e["finish_reason"]
            ):
                return True
        return False


def make_logging_client(
    *,
    timeout: httpx.Timeout,
    evidence: HookEvidence,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """构造带请求级日志的异步 client（同时承载细粒度超时）。

    Args:
        timeout: 细粒度超时（connect/read/write/pool 四段）。
        evidence: 证据桶（每次请求/响应写入；失败只记 warning，绝不阻断评测）。
        transport: 测试注入点（生产传 None，用 httpx 默认 transport）。
    """

    async def on_request(request: httpx.Request) -> None:
        try:
            measure = evidence._measure
            if measure is not None:
                evidence._call_index += 1
                call = {
                    "call_index": evidence._call_index,
                    "judge_call_id": (
                        f"{measure['judge_measure_id']}:{evidence._call_index}"
                    ),
                    "status": None,
                    "finish_reasons": None,
                    "tokens_in": None,
                    "tokens_out": None,
                    "latency_ms": None,
                    "json_ok": None,
                    "error": None,
                    "_started": time.monotonic(),
                }
                evidence._calls[evidence._call_index] = call
                request.extensions["cb_judge"] = call
                evidence.append(
                    {
                        "phase": "request",
                        "url": str(request.url),
                        "call_index": call["call_index"],
                        "judge_call_id": call["judge_call_id"],
                    }
                )
            else:
                evidence.append({"phase": "request", "url": str(request.url)})
        except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
            logger.warning("[provider] judge request hook failed err={}", exc)

    async def on_response(response: httpx.Response) -> None:
        try:
            rec: dict = {"phase": "response", "status": response.status_code}
            rec["retry_after"] = response.headers.get("retry-after")
            if not (200 <= response.status_code < 300):
                logger.warning(
                    "[provider] bridge judge http non-2xx status={}",
                    response.status_code,
                )
            tokens_in = None
            tokens_out = None
            try:
                body = await response.aread()
                parsed = json.loads(body)
                rec["body_bytes"] = len(body)
                rec["json_ok"] = True
                choices = parsed.get("choices") or []
                rec["finish_reason"] = [c.get("finish_reason") for c in choices]
                usage = parsed.get("usage") or {}
                if isinstance(usage.get("prompt_tokens"), int):
                    tokens_in = usage["prompt_tokens"]
                if isinstance(usage.get("completion_tokens"), int):
                    tokens_out = usage["completion_tokens"]
            except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
                rec["json_ok"] = False
                rec["body_exc"] = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[provider] bridge judge http body unreadable status={} err={}",
                    response.status_code,
                    rec["body_exc"],
                )

            call = response.request.extensions.get("cb_judge")
            if call is not None:
                call["status"] = response.status_code
                call["finish_reasons"] = rec.get("finish_reason")
                call["tokens_in"] = tokens_in
                call["tokens_out"] = tokens_out
                call["latency_ms"] = int((time.monotonic() - call["_started"]) * 1000)
                call["json_ok"] = rec.get("json_ok")
                if not (200 <= response.status_code < 300):
                    call["error"] = f"http_{response.status_code}"
                elif rec.get("json_ok") is False:
                    call["error"] = "json_error"
                evidence._log_http(call)

            evidence.append(rec)
        except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
            logger.warning("[provider] judge response hook failed err={}", exc)

    logger.debug("[provider] bridge judge http logging hooks attached")
    return httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        event_hooks={"request": [on_request], "response": [on_response]},
    )
