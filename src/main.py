"""FastAPI 应用入口 — app 工厂、CORS、生命周期管理。"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api import (
    alerts_router,
    chat_router,
    clarify_router,
    doc_router,
    feedback_router,
    health_router,
    kb_eval_router,
    kb_router,
    sessions_router,
)
from src.api import auth as auth_routes
from src.api import capabilities as capabilities_routes
from src.api import ragas_generate as ragas_generate_routes
from src.api import wecom as wecom_routes
from src.config.alert import load_alert_config
from src.config.prompts import validation
from src.config.response_codes import Code
from src.config.settings import validate_timeout_nesting
from src.core import logging as core_logging
from src.core.log_events import Event
from src.core.logging import setup_logging
from src.infra.llm.provider.factory import _registry
from src.infra.llm.provider.spec import validate_config
from src.infra.llm.token_count import warm_token_encoder
from src.infra.llm.tracing import configure_tracing, flush_tracing
from src.middleware.auth import auth_middleware
from src.middleware.response_processor import response_processor_middleware
from src.middleware.trace_id import trace_id_middleware
from src.services import wecom_service
from src.utils.errors import AppError


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期处理器 — 启动/关闭。

    数据库迁移通过 docker compose exec app alembic upgrade head 手动执行。

    启动阶段校验 prompt 模板集（`validation.validate_all`）：任一校验失败
    向上抛出 `TemplateLoadError`，进程启动失败，不留请求期降级分支。

    启动阶段校验 provider 配置（`validate_config`）：解析每个角色的 provider/
    模型/密钥，缺失或未知即抛 `RuntimeError`，进程启动失败（fail-fast）。

    启动阶段校验超时嵌套（`validate_timeout_nesting`）：rerank 内层总预算
    （`RERANK_MAX_ATTEMPTS × RERANK_TIMEOUT_READ`）须小于外层 `RERANK_OUTER_TIMEOUT`，
    且业务层四档 idle 阈值（`MAIN_IDLE_{FIRST,GAP}[_THINKING]_S`）须小于传输层
    `MAIN_TIMEOUT_READ`；任一不满足即抛 `RuntimeError`，进程启动失败（fail-fast，D13/D15）。

    启动阶段校验告警接入配置（`load_alert_config`）：解析 ALERT_TARGET 等非法即抛
    `ValueError`，进程启动失败（fail-fast），不在请求期降级成 500。未配置 token 属
    合法状态（不阻断启动）。

    启动阶段清空残留的 chat_lock:* 键：重启后进程内无任何生成任务，
    残留锁（来自被杀进程，TTL 兜底 180s）会阻塞新请求的并发锁获取。

    tracing 开关生效点（服务侧）：CLI 侧不经 lifespan，由 eval_deepeval 自行调用
    `configure_tracing()`。configure 必须在任何 flush 之前 —— SDK 的 flush 经
    `LangfuseSingleton` 惰性构造客户端，先 flush 会造出默认 enabled=True 的客户端，
    绕过 `settings.LANGFUSE_ENABLE`。

    启动阶段对被绑定角色模型做一次幂等定价 ensure（`ensure_model_pricing`）：非阻塞，
    `LANGFUSE_ENABLE=false` 跳过，Langfuse 不可用等异常降级记 warning；放在
    `configure_tracing()` 之后取 `client_instance`，避免造出未受开关约束的客户端。
    """
    core_logging.log_event(Event.APP_STARTING)
    # 预热 token 分词器：首次获取需联网下载词表（实测约 4s），放启动期可避免
    # 首个用户请求承担该延迟；不可用不影响启动（计数会自行降级）
    warm_token_encoder()
    section_chars = validation.validate_all()
    core_logging.log_event(Event.PROMPT_VALIDATED, section_chars=section_chars)
    await _clear_stale_chat_locks()
    validate_config(_registry())
    # rerank 三层超时嵌套的一致性断言（fail-fast，D13）：内层总预算必须 < 外层，
    # 否则外层 wait_for 先放弃、底层线程跑到底。不随 PROVIDER_VALIDATE 开关跳过。
    validate_timeout_nesting()
    # 告警接入配置启动期校验（fail-fast）：非法即抛，不留请求期降级分支
    load_alert_config()
    configure_tracing()
    await _ensure_model_pricing()
    await wecom_service.start()
    yield
    core_logging.log_event(Event.APP_STOPPING)
    await wecom_service.stop()
    # 关停前上报缓冲事件，否则最后一批 trace 随进程消失
    flush_tracing()


async def _clear_stale_chat_locks() -> None:
    """启动时清空残留的轮次锁与摘要锁键：重启后进程内无任何生成任务，残留锁必然过期。

    Redis 的 chat_lock 由 chat_stream 用 SETNX + TTL 设置，进程被杀时
    可能残留（TTL 兜底 180s），若不清理会阻塞重启后首个新请求的并发锁获取。
    摘要锁（chat_summary_lock:*）同属"进程内持有、进程被杀即泄漏"的键，一并清理。
    清理失败不阻塞启动：Redis 不可用或超时都只记录 warning。
    """
    try:
        from src.infra.redis_client import get_redis_client
        from src.services.summary_lock import SUMMARY_LOCK_PREFIX

        redis = get_redis_client()
        # 摘要锁若不清理，该会话此后 best-effort 永远拿不到锁 ⇒ 永不再生成摘要
        keys = await redis.keys("chat_lock:*")
        keys += await redis.keys(f"{SUMMARY_LOCK_PREFIX}*")
        if keys:
            await redis.delete(*keys)
            core_logging.log_event(Event.STALE_LOCKS_CLEARED, count=len(keys))
    except Exception as e:  # noqa: BLE001
        core_logging.log_event(Event.STALE_LOCKS_CLEAR_FAILED, err=str(e))


async def _ensure_model_pricing() -> None:
    """启动期对被绑定角色的模型做一次幂等定价 ensure（非阻塞）。

    必须在 `configure_tracing()` **之后**取 `client_instance`：SDK 惰性构造客户端，
    先取会造出默认 enabled=True、绕过 `settings.LANGFUSE_ENABLE` 的客户端。

    用 `asyncio.to_thread` 跑同步 SDK 调用 —— **不得**在 async lifespan 里
    `asyncio.run`（当前已有运行中的事件循环，会抛嵌套 loop 错误）。定价 ensure 本身
    已吞异常降级，这里不再重复处理。
    """
    from src.config import settings
    from src.infra.llm.provider.pricing_seed import ensure_model_pricing

    # 开关关闭时连 client_instance 都不取：SDK 惰性构造会缓存客户端，白白构造是浪费，
    # 也可能让后续对开关状态的判断失真（见 tracing.configure_tracing 的顺序契约）。
    if not settings.LANGFUSE_ENABLE:
        return

    from langfuse.decorators import langfuse_context

    await asyncio.to_thread(
        ensure_model_pricing,
        _registry(),
        client=langfuse_context.client_instance,
    )


app = FastAPI(
    title="财务问答 API",
    description="金融文档问答助手的 REST API — 知识库管理、文档上传、流式 RAG 问答",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
)


# 配置 Loguru — 收拢到统一模块
setup_logging(configure_trace_id=True)


# 异常处理器 — 所有 Router 层异常在此集中处理，补充 traceback 日志


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    from starlette.responses import JSONResponse

    # >=500 基础设施异常保留 exception 直调（含完整 traceback）
    # <500 业务异常事件化（code/message 字段，无需 traceback）
    if exc.status >= 500:
        logger.exception("[app] infra error code={} message={}", exc.code, exc.message)
    else:
        core_logging.log_event(Event.BIZ_ERROR, code=exc.code, message=exc.message)

    # TODO: ARMS Prometheus 接入后在此处打 exception_total.inc()
    return JSONResponse(
        {"code": exc.code, "message": exc.message, "data": None},
        status_code=exc.status,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    from starlette.responses import JSONResponse

    core_logging.log_event(Event.HTTP_ERROR, status=exc.status_code, detail=exc.detail)
    code = Code.NOT_FOUND if exc.status_code == 404 else Code.UNKNOWN_ERROR
    msg = exc.detail or (
        Code.NOT_FOUND_MSG if exc.status_code == 404 else Code.UNKNOWN_ERROR_MSG
    )
    return JSONResponse(
        {"code": code, "message": msg, "data": None}, status_code=exc.status_code
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    from starlette.responses import JSONResponse

    core_logging.log_event(Event.VALIDATION_ERROR, errors=exc.errors())
    return JSONResponse(
        {
            "code": Code.VALIDATION_ERROR,
            "message": Code.VALIDATION_ERROR_MSG,
            "data": None,
        },
        status_code=422,
    )


@app.exception_handler(Exception)
async def unknown_exception_handler(request: Request, exc: Exception):
    from starlette.responses import JSONResponse

    logger.exception(
        "[app] unhandled exception method={} url={}",
        request.method,
        core_logging.mask_alert_webhook_path(str(request.url)),
    )
    # 打印异常链根因
    c = exc
    depth = 0
    while depth < 5:
        nxt = c.__cause__ or c.__context__
        if nxt is None:
            break
        c = nxt
        core_logging.log_event(
            Event.EXCEPTION_CHAIN,
            depth=depth + 1,
            type=type(c).__name__,
            msg=str(c),
        )
        depth += 1
    # TODO: ARMS Prometheus 接入后在此处打 exception_total.inc()
    return JSONResponse(
        {"code": Code.INTERNAL_ERROR, "message": Code.INTERNAL_ERROR_MSG, "data": None},
        status_code=500,
    )


# 中间件注册顺序（请求从外到内）：
# CORS → ResponseProcessor → auth → TraceID → router
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(response_processor_middleware)
app.middleware("http")(auth_middleware)
app.middleware("http")(
    trace_id_middleware
)  # 最后注册 = 最外层，确保所有路径都写 X-Trace-ID

# 挂载路由模块
app.include_router(alerts_router, prefix="/api", tags=["alerts"])
app.include_router(auth_routes.router, prefix="/api", tags=["auth"])
app.include_router(health_router, prefix="/api", tags=["health"])
app.include_router(kb_router, prefix="/api", tags=["knowledge-bases"])
app.include_router(doc_router, prefix="/api", tags=["documents"])
app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(clarify_router, prefix="/api", tags=["chat"])
app.include_router(feedback_router, prefix="/api", tags=["feedback"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(kb_eval_router, prefix="/api", tags=["evaluation"])
app.include_router(ragas_generate_routes.router, prefix="/api", tags=["ragas"])
app.include_router(capabilities_routes.router, prefix="/api", tags=["capabilities"])
app.include_router(wecom_routes.router, prefix="/api", tags=["wecom"])
