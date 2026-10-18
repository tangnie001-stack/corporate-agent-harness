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

import httpx
from loguru import logger


class HookEvidence:
    """按样本归集的观测证据（裁判 client 跨样本复用 ⇒ 用可变桶，不闭包固定 id）。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._trace_id: str | None = None
        # 整轮评测的 trace 身份（`begin_eval_run` 产物），随请求记录一并落日志
        self.run_trace_id: str | None = None
        self.judge_model: str | None = None

    def set_run(self, run_trace_id: str, judge_model: str | None = None) -> None:
        """登记整轮评测的 run trace id 与裁判模型名。

        Args:
            run_trace_id: 整轮 trace id（`eval_run_<timestamp>_<8hex>`）
            judge_model: 裁判模型名；None 表示未知
        """
        self.run_trace_id = run_trace_id
        self.judge_model = judge_model

    def mark(self, trace_id: str | None) -> None:
        """切到某题的证据窗口（评分串行，并发=1，故单槽安全）。"""
        self._trace_id = trace_id

    def reset(self) -> None:
        self.events.clear()
        self._trace_id = None

    def append(self, rec: dict) -> None:
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
        「有 request 事件、无 response 事件」（观测缺口）。靠本属性判"没事"是错的，
        调用方若要区分，须显式检查"该格是否有 response 事件"。
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
        evidence.append({"phase": "request", "url": str(request.url)})

    async def on_response(response: httpx.Response) -> None:
        rec: dict = {"phase": "response", "status": response.status_code}
        rec["retry_after"] = response.headers.get("retry-after")
        if not (200 <= response.status_code < 300):
            logger.warning(
                "[provider] bridge judge http non-2xx status={}", response.status_code
            )
        try:
            body = await response.aread()
            parsed = json.loads(body)
            rec["body_bytes"] = len(body)
            rec["json_ok"] = True
            choices = parsed.get("choices") or []
            rec["finish_reason"] = [c.get("finish_reason") for c in choices]
        except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
            rec["json_ok"] = False
            rec["body_exc"] = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[provider] bridge judge http body unreadable status={} err={}",
                response.status_code,
                rec["body_exc"],
            )
        evidence.append(rec)

    logger.debug("[provider] bridge judge http logging hooks attached")
    return httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        event_hooks={"request": [on_request], "response": [on_response]},
    )
