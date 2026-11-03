"""证据采集：两波并行、单源超时、总预算、三态归一。

第一波跑无 when 的条目；第二波跑 when 成立（依赖前一波结果）的条目。
必需源 failed 时立即结束，不再跑第二波。任何异常归一为 failed，不向上抛。
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Awaitable, Callable
from typing import cast

from loguru import logger

from src.config.rca import RcaConfig
from src.core.logging import encode_value
from src.rca.models import EvidenceResult, EvidenceSpec, EvidenceStatus
from src.rca.sources.base import LogSource, SourceEmptyError, SourceFailedError

_PLACEHOLDER = re.compile(r"\$\{alert\.([a-zA-Z0-9_]+)\}")
_WHEN = re.compile(
    r"^([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+)?)$"
)
_WINDOW_KEYS = ("start_ts", "end_ts")


def _resolve_params(params: dict[str, str], fields: dict) -> dict:
    """替换 alert 占位符；缺字段抛 KeyError（严格模式）。"""

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in fields or fields[name] is None:
            raise KeyError(name)
        return str(fields[name])

    return {key: _PLACEHOLDER.sub(_sub, raw) for key, raw in params.items()}


def _window_value(alert_fields: dict, key: str) -> int:
    """取时间窗字段；缺失或非数值按 0（生产由编排层注入真实窗口）。"""
    value = alert_fields.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _accepted_params(method: Callable) -> set[str]:
    """取工具可接收的参数名；签名不可内省时视为空集（不注入任何窗口）。"""
    try:
        return set(inspect.signature(method).parameters)
    except (TypeError, ValueError):
        return set()


def _build_kwargs(
    params: dict[str, str], alert_fields: dict, accepted: set[str]
) -> dict:
    """合并显式参数与时间窗：窗口只注入工具签名确实声明的键（显式参数优先）。

    窗口不是 skill 必须声明的参数，由编排层按告警上下文统一注入，故只在工具
    签名接收时才补；否则会给不取窗口的工具塞多余关键字 ⇒ TypeError。
    """
    kwargs: dict = dict(params)
    for key in _WINDOW_KEYS:
        if key not in kwargs and key in accepted:
            kwargs[key] = _window_value(alert_fields, key)
    return kwargs


def _eval_when(expr: str | None, results: dict[str, EvidenceResult]) -> bool:
    """求值 when 简易表达式；无法解析时视为不成立。"""
    if expr is None:
        return True
    match = _WHEN.match(expr.strip())
    if match is None:
        return False
    source_id, field, op, raw_value = match.groups()
    result = results.get(source_id)
    if result is None or not isinstance(result.data, dict):
        return False
    left = result.data.get(field)
    if isinstance(left, bool) or not isinstance(left, (int, float)):
        return False
    right = float(raw_value)
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return left == right


def _elapsed_ms(started: float) -> int:
    """把起算点换算成已耗时毫秒。"""
    return int((time.monotonic() - started) * 1000)


async def _run_one(
    spec: EvidenceSpec,
    source: LogSource,
    alert_fields: dict,
    cfg: RcaConfig,
    semaphore: asyncio.Semaphore,
) -> EvidenceResult:
    """执行单条证据并归一三态。"""
    started = time.monotonic()
    try:
        params = _resolve_params(spec.params, alert_fields)
    except KeyError as exc:
        return EvidenceResult(
            spec.id, EvidenceStatus.FAILED, None, 0, f"missing_field:{exc}"
        )

    method = getattr(source, spec.tool, None)
    if not callable(method):
        return EvidenceResult(spec.id, EvidenceStatus.FAILED, None, 0, "unknown_tool")

    kwargs = _build_kwargs(params, alert_fields, _accepted_params(method))

    call = cast("Callable[..., Awaitable[dict | list | None]]", method)
    timeout = min(spec.timeout, cfg.source_timeout_seconds)
    async with semaphore:
        try:
            data = await asyncio.wait_for(call(**kwargs), timeout=timeout)
        except TimeoutError:
            return EvidenceResult(
                spec.id, EvidenceStatus.FAILED, None, _elapsed_ms(started), "timeout"
            )
        except SourceEmptyError as exc:
            return EvidenceResult(
                spec.id,
                EvidenceStatus.EMPTY,
                None,
                _elapsed_ms(started),
                f"empty:{exc}",
            )
        except SourceFailedError as exc:
            return EvidenceResult(
                spec.id, EvidenceStatus.FAILED, None, _elapsed_ms(started), str(exc)
            )
        except Exception as exc:  # noqa: BLE001  单源异常不得中断整体采集
            logger.warning(
                "[rca] evidence failed id={} err={}",
                encode_value(spec.id),
                encode_value(type(exc).__name__),
            )
            return EvidenceResult(
                spec.id,
                EvidenceStatus.FAILED,
                None,
                _elapsed_ms(started),
                "internal_error",
            )

    elapsed = _elapsed_ms(started)
    if data is None or data == {} or data == []:
        return EvidenceResult(spec.id, EvidenceStatus.EMPTY, None, elapsed, "no_data")
    return EvidenceResult(spec.id, EvidenceStatus.OK, data, elapsed)


async def _run_wave(
    specs: list[EvidenceSpec],
    source: LogSource,
    alert_fields: dict,
    cfg: RcaConfig,
    semaphore: asyncio.Semaphore,
    deadline: float,
) -> tuple[dict[str, EvidenceResult], bool]:
    """并行执行一波，返回（结果, 是否因必需源失败而短路）。

    必需源 failed 时取消同波剩余条目并立即返回，未跑完的条目不出现在结果里。
    超出总预算的条目标记为 failed:budget_exceeded；deadline 由调用方按整轮
    采集算一次，两波共用，预算不会被重复计算。
    """
    results: dict[str, EvidenceResult] = {}
    if not specs:
        return results, False

    running: dict[asyncio.Task, EvidenceSpec] = {
        asyncio.create_task(_run_one(spec, source, alert_fields, cfg, semaphore)): spec
        for spec in specs
    }
    short_circuit = False
    budget_exceeded = False
    while running:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            budget_exceeded = True
            break
        done, _ = await asyncio.wait(
            set(running), timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            budget_exceeded = True
            break
        for task in done:
            spec = running.pop(task)
            result = task.result()
            results[result.id] = result
            if result.status is EvidenceStatus.FAILED and spec.required:
                short_circuit = True
        if short_circuit:
            break

    for task in running:
        task.cancel()
    if budget_exceeded:
        for spec in running.values():
            results[spec.id] = EvidenceResult(
                spec.id,
                EvidenceStatus.FAILED,
                None,
                int(cfg.collect_budget_seconds * 1000),
                "budget_exceeded",
            )
    return results, short_circuit


async def collect_evidence(
    specs: tuple[EvidenceSpec, ...] | list[EvidenceSpec],
    source: LogSource,
    alert_fields: dict,
    cfg: RcaConfig,
) -> dict[str, EvidenceResult]:
    """按两波策略采集全部证据；必需源失败时立即返回。"""
    semaphore = asyncio.Semaphore(cfg.concurrency)
    results: dict[str, EvidenceResult] = {}
    deadline = time.monotonic() + cfg.collect_budget_seconds

    first_wave = [spec for spec in specs if spec.when is None]
    first_results, short_circuit = await _run_wave(
        first_wave, source, alert_fields, cfg, semaphore, deadline
    )
    results.update(first_results)
    if short_circuit:
        return results

    second_wave = [
        spec
        for spec in specs
        if spec.when is not None and _eval_when(spec.when, results)
    ]
    second_results, _ = await _run_wave(
        second_wave, source, alert_fields, cfg, semaphore, deadline
    )
    results.update(second_results)
    return results
