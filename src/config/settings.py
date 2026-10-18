"""全局配置模块 — 集中管理所有环境变量和系统参数。

本模块从 .env 文件和环境变量中读取配置项，供其他模块统一导入使用。
所有配置项均提供合理的默认值，方便本地开发和 Docker 部署两套环境无缝切换。

主要配置分区：
  - DashScope API：阿里云大模型平台认证与模型选择
  - PostgreSQL：关系型元数据库连接参数
  - Redis：对话历史缓存参数
  - 文档处理：分块策略（chunk size/overlap）和检索参数
  - 对话管理：历史窗口大小和缓存过期时间
  - 重试策略：外部调用的指数退避参数
  - 文件上传：单文件大小限制
"""

import os
import urllib.parse

from dotenv import load_dotenv
from loguru import logger

# DeepEval 在 import 时会自动加载 .env / .env.local 进 process env，并默认开启
# posthog 遥测；两者都会污染本项目配置解析。必须在 deepeval 被 import 之前设好，
# 故放在 settings（最早被 import 的模块之一）顶部、且排在其它 src.* import 之前。
os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

# 加载项目根目录的 .env 文件（如存在），将键值对注入 os.environ
load_dotenv()

# 旧评测配置键 → 新键对照表。检测放在 load_dotenv() 之后：`.env` 里的旧键也会被拦下。
# 只报错、不 fallback —— 线上留着旧键说明部署配置未迁移，静默兜底会掩盖问题。
_LEGACY_ENV_MAP = {
    "RAGAS_LLM_MODEL": "JUDGE_MODEL",
    "RAGAS_PROVIDER": "JUDGE_PROVIDER",
    "RAGAS_JUDGE_TIMEOUT": "JUDGE_TIMEOUT",
    "RAGAS_HTTP_TIMEOUT_CONNECT": "JUDGE_HTTP_TIMEOUT_CONNECT",
    "RAGAS_HTTP_TIMEOUT_READ": "JUDGE_HTTP_TIMEOUT_READ",
    "RAGAS_HTTP_TIMEOUT_WRITE": "JUDGE_HTTP_TIMEOUT_WRITE",
    "RAGAS_HTTP_TIMEOUT_POOL": "JUDGE_HTTP_TIMEOUT_POOL",
    "RAGAS_METRIC_MAX_ATTEMPTS": "EVAL_METRIC_MAX_ATTEMPTS",
    "RAGAS_JUDGE_MAX_TOKENS": "JUDGE_MAX_TOKENS",
    "RAGAS_JUDGE_THINKING": "JUDGE_THINKING",
    "RAGAS_DATA_DIR": "EVAL_DATA_DIR",
    "RAGAS_TESTSET_DIR": "EVAL_TESTSET_DIR",
    "RAGAS_REPORT_DIR": "EVAL_REPORT_DIR",
    "RAGAS_LLM_CACHE_DIR": "EVAL_LLM_CACHE_DIR",
    "RAGAS_USER_ID": "EVAL_USER_ID",
    "RAGAS_METRIC_COLUMNS": "删除（已改为代码常量 EVAL_METRIC_COLUMNS，不再读 env）",
    "RAGAS_HTTP_MAX_RETRIES": "删除（裁判固定 max_retries=0）",
    "RAGAS_TEST_SIZE": "删除（出题路退役）",
    "RAGAS_DOC_WHITELIST": "删除（出题路退役）",
}
_LEGACY_RAGAS_ENV = sorted(key for key in os.environ if key.startswith("RAGAS_"))
if _LEGACY_RAGAS_ENV:
    hints = [
        f"{key} -> {_LEGACY_ENV_MAP.get(key, 'JUDGE_*/EVAL_*')}"
        for key in _LEGACY_RAGAS_ENV
    ]
    raise RuntimeError(
        "检测到已废弃的 RAGAS_* 环境变量："
        + "；".join(hints)
        + "（对照表见 docs/agents/deploy-runbook.md）"
    )


# ====== DashScope API ======
# 阿里云 DashScope 平台 API Key，必填项，用于调用 LLM / Embedding / Rerank 模型
DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
# DashScope OpenAI 兼容接口地址，qwen 系列模型走此 URL
DASHSCOPE_BASE_URL: str = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# ====== 模型选择 ======
# 模型常量默认一律为**空串**，表示"未配置"：由 provider 兼容层取
# registry.models[<role>] 兜底（见 src/infra/llm/provider/spec.py 的 resolve_role）。
# 这几个环境变量降级为**可选覆盖**（仅部署态可覆盖，见 design D3/D10）。
# 大语言模型：用于生成最终回答（main 角色）
LLM_MODEL: str = os.getenv("LLM_MODEL", "")
# LLM API Key（如未设置，自动 fallback 到 DASHSCOPE_API_KEY）
LLM_API_KEY: str = os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
# LLM API 地址（默认直连 DashScope；如需经 LiteLLM 网关，设为 http://litellm-proxy:4000
# 并显式启用其 disabled profile —— 见 docs/adr/0020-no-model-gateway-direct-provider.md）
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL") or DASHSCOPE_BASE_URL
# Phase 1 软迁移（design D10）：上面两个旧键仍被读取（行为等值），但已废弃；
# 仅当显式设置时告警。Phase 2 删除，改用 <PROVIDER>_BASE_URL / <PROVIDER>_API_KEY。
if os.getenv("LLM_BASE_URL") or os.getenv("LLM_API_KEY"):
    logger.warning(
        "[config] LLM_BASE_URL/LLM_API_KEY 已废弃（Phase 2 删除），"
        "改用 <PROVIDER>_BASE_URL/<PROVIDER>_API_KEY"
    )
# LLM 温度参数：越低回答越确定性（适合金融场景），0.1 几乎不产生随机性
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
# 主 agent 采样温度（chat-temperature-policy）：绑 KB 沿用 LLM_TEMPERATURE（默认 0.1）；
# 非 KB 聊天/探讨用中温避免过干（proposal：唯一新增可调参数，KB 档不另设旋钮）
NON_KB_MAIN_TEMPERATURE: float = float(os.getenv("NON_KB_MAIN_TEMPERATURE", "0.6"))
# LLM 额外参数（JSON 格式，如 {"extra_body": {"enable_thinking": false}}）
LLM_KWARGS: str = os.getenv("LLM_KWARGS", "{}")
# 分类器温度参数：查询路由分类时使用，略高于 LLM 温度以允许少量分类灵活度
CLASSIFIER_TEMPERATURE: float = float(os.getenv("CLASSIFIER_TEMPERATURE", "0.1"))
# 分类/路由专用模型：实体抽取 / 意图判定 / 时间解析使用的小模型（无思考模式）。
# 默认空串 → 由 registry.models[classify] 兜底；LLM_MODEL 之外的独立可选覆盖。
CLASSIFY_MODEL: str = os.getenv("CLASSIFY_MODEL", "")
# 跨轮历史摘要专用模型：默认沿用主模型，可用 env 覆盖为更便宜的档位。
# 摘要调用固定关闭思考（实测深思考让输出 token 涨约 2.7 倍，其中 64% 是不可见思考）。
SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", LLM_MODEL)
# LLM 输入/输出内容记录开关：开启后所有 LLM 调用（含 ragas 指标打分）会
# 把 prompt 与响应写入日志。默认关闭（避免生产日志被内容刷屏），调试时开启
LLM_LOG_CONTENT: bool = os.getenv("LLM_LOG_CONTENT", "0").lower() in (
    "1",
    "true",
    "yes",
)
# 查询澄清开关：true 时当分类器检测到缺失实体信息时，主动向用户追问澄清
CLARIFICATION_ENABLED: bool = os.getenv("CLARIFICATION_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
# 时间结构化约束开关：关闭时跳过时间解析（出错可即时关闭）
TEMPORAL_PARSE_ENABLED: bool = os.getenv("TEMPORAL_PARSE_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
# 验证循环开关：关闭时 verify 节点直通（出错可即时关闭）
VERIFY_ENABLED: bool = os.getenv("VERIFY_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)

# ====== Prompt 组装观测 ======
# 中文字符 → token 的换算系数（**跨模型借用**，非本模型分词器实测）：
# 出处为 DeepSeek 官方 token 用量文档「1 个中文字符 ≈ 0.6 token」；本项目跑
# DashScope/Qwen，该系数不是本项目所用模型的分词器实测，仅作量级估算。
PROMPT_TOKENS_PER_CJK_CHAR: float = float(
    os.getenv("PROMPT_TOKENS_PER_CJK_CHAR", "0.6")
)
# system 段估算占 context window 比例的告警阈值（**推断值**，非契约）：
# 仅触发 warning 不阻断。0.05 在 0.6 系数 / 32768 窗口下约 2731 字符触发，
# 略高于当前段总量（约 1266 字符）、随后续阶段加内容而生效，仅供参考，
# 取值可能需要按实测分布校准。
PROMPT_CONTEXT_SHARE_WARN: float = float(os.getenv("PROMPT_CONTEXT_SHARE_WARN", "0.05"))
# context window 大小（token）——**假设值**，仅用于 `src/rag/prompt.py` 的
# system 段占比告警（`PROMPT_CONTEXT_SHARE_WARN`）的量级换算，**不参与历史预算**
# （历史预算见 const.py 的 HISTORY_TOKEN_BUDGET，为与窗口解耦的绝对值）。
# 实测当前运行时模型（qwen3.8-2.4t-a95b）输入上限为 983616；此处仍未按实测更新，
# 待确证后单独处理（属 Phase B 的窗口注册表范围）。
MODEL_CONTEXT_WINDOW_TOKENS: int = int(
    os.getenv("MODEL_CONTEXT_WINDOW_TOKENS", "32768")
)

# 向量化模型：将文本转为向量，用于 pgvector 语义检索。默认空串 → registry.models[embedding] 兜底。
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "")
# Embedding API Key（fallback: DASHSCOPE_API_KEY → LLM_API_KEY）
EMBEDDING_API_KEY: str = (
    os.getenv("EMBEDDING_API_KEY")
    or os.getenv("DASHSCOPE_API_KEY")
    or os.getenv("LLM_API_KEY", "")
)
# Embedding API 地址（fallback: LLM_BASE_URL → DashScope 直连）
EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL") or LLM_BASE_URL
# 向量输出维度：固定维度后切换模型无需重建向量列
EMBEDDING_DIMENSION: int = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
# Embedding API 单次 batch 上限（DashScope 限制 20 条，超出需分批）
EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "20"))

# 重排序模型（默认空串 → registry.models[rerank] 兜底）
RERANK_MODEL: str = os.getenv("RERANK_MODEL", "")
# Rerank API Key（fallback 到 DASHSCOPE_API_KEY，非 LLM_API_KEY）
RERANK_API_KEY: str = os.getenv("RERANK_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
# Tavily 联网搜索（web-search-fallback） https://www.tavily.com/
TAVILY_API_KEY: str = os.getenv(
    "TAVILY_API_KEY", ""
)  # Tavily API Key（仅 .env，gitignored）
WEB_SEARCH_ENABLED: bool = os.getenv("WEB_SEARCH_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)  # 联网兜底总开关：false 时不注册 search_web 工具
WEB_SEARCH_PER_TURN_LIMIT: int = int(
    os.getenv("WEB_SEARCH_PER_TURN_LIMIT", "3")
)  # 每轮对话 search_web 调用上限
TAVILY_TIMEOUT: float = float(
    os.getenv("TAVILY_TIMEOUT", "5")
)  # Tavily 请求超时秒数，超时返回空走纯拒答
ASK_USER_MODE_DSH: bool = os.getenv("ASK_USER_MODE_DSH", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)  # ask_user 澄清模式：true=dsh 全自由；false=KB dimension 注入 + 非 KB 自由
# 评测裁判专用模型（独立于生产 LLM，temperature 固定为 0）
# 「无思考/非推理模型」的限制属于上方 CLASSIFY_MODEL（分类/路由小模型），
# 与评测裁判模型无关。JUDGE_MODEL 经 LangchainLLMWrapper(bypass_n=True)
# 包装后强制 n=1，推理模型（如 qwen3.7-max）可正常使用（已实测）。
JUDGE_MODEL: str = os.getenv("JUDGE_MODEL", "")
# 裁判/embedding 客户端请求超时秒数（思考模型偶尔 >30s，超时不足会把分数变成失败）
JUDGE_TIMEOUT: float = float(os.getenv("JUDGE_TIMEOUT", "300"))
# ── 评测 native 客户端（裁判 / embedding）的细粒度 HTTP 超时（秒）──
# 依据（实测 5 题 69 次裁判调用）：p50=7.6s · p95=66s · p99=111s（111s ≈ 跑满 8192 输出上限）。
# 原先平铺 300s：一次挂死调用白等 300s，再叠加 openai SDK 默认 2 次重试 = 900s 空转
# （全量 21 题 91 分钟里约 80 分钟是这种空转）。细粒度后把死等压到 read 上限。
# 参考：OpenAI SDK 默认 Timeout(connect=5, read=600, write=600, pool=600)；业界常见
# 细粒度写法 connect=10 / read=60 / write=20 / pool=10。
JUDGE_HTTP_TIMEOUT_CONNECT: float = float(os.getenv("JUDGE_HTTP_TIMEOUT_CONNECT", "10"))
# 正常调用 p99=111s ⇒ 取 150s（高于 p99 约 35%，误杀少；死等上限仅 150s）
JUDGE_HTTP_TIMEOUT_READ: float = float(os.getenv("JUDGE_HTTP_TIMEOUT_READ", "150"))
JUDGE_HTTP_TIMEOUT_WRITE: float = float(os.getenv("JUDGE_HTTP_TIMEOUT_WRITE", "30"))
JUDGE_HTTP_TIMEOUT_POOL: float = float(os.getenv("JUDGE_HTTP_TIMEOUT_POOL", "10"))
# 单指标最大尝试次数（我们自己这层的有界重试，替代被关掉的 SDK 重试）。
# 裁判客户端的 SDK 重试已固定为 0（只发 1 次 HTTP 请求），重试完全由本层控制。
# 依据：失败主因是模型偶发「吐空结构」（`finish_reason=stop`、输出仅 148–215 token、
# raw=`{"classifications":[{},{},{}]}`）——**快（7–16s）且随机**，重试成功率高；
# 而不重试会把失败行踢出均值，造成乐观偏差 + 分母不齐。取 3（两次重试）。
# 超时也重试（实测抓到一次 150s 超时重试后成功恢复）。代价上界 = attempts × read 上限。
EVAL_METRIC_MAX_ATTEMPTS: int = int(os.getenv("EVAL_METRIC_MAX_ATTEMPTS", "3"))
# v6 出题：每个上下文包含的连续分块数
EVAL_SYNTH_CHUNKS_PER_CONTEXT: int = int(
    os.getenv("EVAL_SYNTH_CHUNKS_PER_CONTEXT", "2")
)
# v6 出题：每个上下文生成多少条题（低产出时升到 2）
EVAL_SYNTH_GOLDENS_PER_CONTEXT: int = int(
    os.getenv("EVAL_SYNTH_GOLDENS_PER_CONTEXT", "1")
)
# v6 出题：并发上下文数（默认 100 会打出 429 风暴）
EVAL_SYNTH_MAX_CONCURRENT: int = int(os.getenv("EVAL_SYNTH_MAX_CONCURRENT", "4"))
# 裁判单次生成的最大输出 token。RAGAS 内建默认仅 **1024**，会截断正常输出 → 必须上调。
# 取 **8192** 而非 4096：两处独立观测都显示 8192 的失败**不多于** 4096
# （A/B 探针 15 次/档：4096 失败 1、8192 失败 0；真实 21 题全量：4096 → 5/21、8192 → 3/21），
# 且**均耗时无差异**（35s vs 33s；跑飞的调用本就稀有）⇒ 大 cap 不付时间代价。
# 注意：cap 只是"缓解"——失败根因是模型偶发退化成吐空对象，与 cap 无关（详见 ADR-0024）。
JUDGE_MAX_TOKENS: int = int(os.getenv("JUDGE_MAX_TOKENS", "8192"))
# 裁判是否开思考。定稿 = **关**（2026-10-15 A/B：关思考显著更快；开思考在 21 题全量下
# 出现 5/21 行 `Request timed out`、耗时 1h31m）。
JUDGE_THINKING: bool = os.getenv("JUDGE_THINKING", "false").lower() in (
    "1",
    "true",
    "yes",
)
# 评测数据根目录（测试集 / 报告 / 缓存均在其下）
EVAL_DATA_DIR: str = os.getenv("EVAL_DATA_DIR", "data/deepeval")
# 评测测试集 JSON 文件存放目录
EVAL_TESTSET_DIR: str = os.getenv("EVAL_TESTSET_DIR", "data/deepeval/testset")
# 评测报告输出目录
EVAL_REPORT_DIR: str = os.getenv("EVAL_REPORT_DIR", "data/deepeval/reports")
# 评测 LLM 缓存目录（DiskCacheBackend，用于提速重复生成）
EVAL_LLM_CACHE_DIR: str = os.getenv("EVAL_LLM_CACHE_DIR", "data/deepeval/llm_cache")
# 评测默认用户 ID（查询知识库时使用）
EVAL_USER_ID: str = os.getenv("EVAL_USER_ID", "24a93c0e-3c9b-4d8d-a371-2d8b3607892a")

# --- PostgreSQL（应用关系型库；LangfSuse 用同实例的另一个 database）---
POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "corporate_rag")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DATABASE: str = os.getenv("POSTGRES_DATABASE", "corporate_rag")


def build_postgres_dsn() -> str:
    """拼装应用库的 SQLAlchemy 异步 DSN。

    Returns:
        postgresql+asyncpg:// 形式的连接串，账号密码按 URL 规则转义。

    Raises:
        RuntimeError: 密码为空时抛出 —— 空密码只会得到一个连不上的 DSN，
            失败会推迟到首次查询，不如在这里直接失败。
    """
    if not POSTGRES_PASSWORD:
        raise RuntimeError("POSTGRES_PASSWORD 未配置，无法构造 PostgreSQL DSN")
    user = urllib.parse.quote(POSTGRES_USER, safe="")
    password = urllib.parse.quote(POSTGRES_PASSWORD, safe="")
    return (
        f"postgresql+asyncpg://{user}:{password}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DATABASE}"
    )


# --- Langfuse 观测库（同一 PG 实例上的独立 database）---
# 用途：保留期清理 CLI 直连该库执行 SQL 删除（ADR-0013）；业务请求不走这里。
LANGFUSE_PG_DATABASE: str = os.getenv("LANGFUSE_PG_DATABASE", "langfuse")
LANGFUSE_PG_USER: str = os.getenv("LANGFUSE_PG_USER", "langfuse")
# 密码默认复用编排层注入的 LANGFUSE_POSTGRES_PASS（app 容器已有该变量），再退空串
LANGFUSE_PG_PASSWORD: str = os.getenv(
    "LANGFUSE_PG_PASSWORD", os.getenv("LANGFUSE_POSTGRES_PASS", "")
)


def build_langfuse_postgres_dsn() -> str:
    """拼装 Langfuse 观测库的 SQLAlchemy 异步 DSN。

    仅用于保留期清理 CLI（ADR-0013）；宿主 / 容器均复用 POSTGRES_HOST 与
    POSTGRES_PORT —— Langfuse 库与应用库是同一 PostgreSQL 实例。

    Returns:
        postgresql+asyncpg:// 形式的连接串，账号密码按 URL 规则转义。

    Raises:
        RuntimeError: 密码为空时抛出 —— 空密码只会得到一个连不上的 DSN，
            失败会推迟到首次查询，不如在这里直接失败。
    """
    if not LANGFUSE_PG_PASSWORD:
        raise RuntimeError(
            "LANGFUSE_PG_PASSWORD 未配置，无法构造 Langfuse PostgreSQL DSN"
        )
    user = urllib.parse.quote(LANGFUSE_PG_USER, safe="")
    password = urllib.parse.quote(LANGFUSE_PG_PASSWORD, safe="")
    return (
        f"postgresql+asyncpg://{user}:{password}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{LANGFUSE_PG_DATABASE}"
    )


# ====== Redis ======
# 对话历史缓存，支持会话级上下文记忆；连接失败时自动降级为内存存储
REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
REDIS_USERNAME: str = os.getenv("REDIS_USERNAME", "default")
REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "financial_qa_pass")
# 拼接完整 Redis URL，供 redis-py 的 from_url() 直接使用
REDIS_URL: str = (
    f"redis://{REDIS_USERNAME}:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
)

# ====== 文档处理 ======
# 文本分块大小（字符数）：512 是金融文档的平衡点，太小丢上下文，太大检索不精准
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "512"))
# 相邻分块的重叠字符数：保证跨块的句子不会丢失关键信息
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "64"))
# 初步检索返回的 top-K 数量（送入 reranker 之前的候选数）
TOP_K_RETRIEVAL: int = int(os.getenv("TOP_K_RETRIEVAL", "30"))
# 重排序后保留的 top-N 数量（最终送入 LLM 的上下文数量）
TOP_K_RERANK: int = int(os.getenv("TOP_K_RERANK", "5"))
# 扫描件检测阈值：单页可提取文字少于 200 字符视为"扫描页"
MIN_TEXT_CHARS: int = int(os.getenv("MIN_TEXT_CHARS", "200"))
# 页眉页脚边距过滤（fitz 通道按 y 坐标剔除重复页眉/页脚）
# 顶部 45：保留页首内容标题（如 tencent 的"简明综合财务状况表"，y≈52-66）
# 底部 80：剔除页码页脚（如 neusoft 的 "1 / 10 东软集团..."，y≈768-791）
HEADER_MARGIN: int = int(os.getenv("HEADER_MARGIN", "45"))
FOOTER_MARGIN: int = int(os.getenv("FOOTER_MARGIN", "80"))
# pm 标题树子进程：超时秒数与主进程并发上限（每个子进程 import pymupdf4llm 约 200MB）
PDF_HEADING_SUBPROCESS_TIMEOUT: int = int(
    os.getenv("PDF_HEADING_SUBPROCESS_TIMEOUT", "180")
)
MAX_CONCURRENT_HEADING_SUBPROCESS: int = int(
    os.getenv("MAX_CONCURRENT_HEADING_SUBPROCESS", "2")
)
# 跨页表格合并阈值：TABLE → 短文本（< N 字符）→ TABLE 合并为一个 chunk
CROSS_PAGE_TABLE_MERGE_THRESHOLD: int = int(
    os.getenv("CROSS_PAGE_TABLE_MERGE_THRESHOLD", "100")
)
# 表格合并后最大 token 数（超过则不合并，避免 embedding 截断丢失信息）
# 使用时需 *2 转为字符数（中文 1 token ≈ 2 字符），如 2048 token → 4096 chars
MAX_TABLE_TOKENS: int = int(os.getenv("MAX_TABLE_TOKENS", "2048"))

# 大表格行级切分阈值：合并后的表格超过此字符数时，按行边界切分，复制表头
TABLE_ROW_CHUNK_CHARS: int = int(os.getenv("TABLE_ROW_CHUNK_CHARS", "2000"))
# 残差短文本合并阈值：小于此字符数的独立文本段，如果与表格相邻则粘到表格上
ORPHAN_THRESHOLD_CHARS: int = int(os.getenv("ORPHAN_THRESHOLD_CHARS", "200"))
# 实体抽取 LLM 兜底开关（三态）: off=纯规则 / on=每文档无条件走 LLM / auto=规则空或缺关键类型才走
ENTITY_LLM_FALLBACK: str = os.getenv("ENTITY_LLM_FALLBACK", "auto")
# 实体抽取 LLM 兜底输入的正文前缀长度（字符数）
ENTITY_TEXT_PREFIX_LEN: int = int(os.getenv("ENTITY_TEXT_PREFIX_LEN", "600"))

# ====== Hybrid Search ======
# 是否启用 dense + 词法两路混合检索（RRF 融合，两路同源于 PostgreSQL）
HYBRID_SEARCH_ENABLED: bool = (
    os.getenv("HYBRID_SEARCH_ENABLED", "true").lower() == "true"
)
# RRF 融合的平滑常数：控制排名权重衰减速度（沿用替换前的取值）
RRF_K: int = int(os.getenv("RRF_K", "60"))
# RRF 融合后保留条数：交给下游按 doc_id 去重与 rerank 截断
RRF_TOP_N: int = int(os.getenv("RRF_TOP_N", "50"))

# ====== 对话管理 ======
# 对话历史窗口大小：取最近 N 条消息作为 LLM 的上下文，避免 token 溢出
MEMORY_WINDOW: int = int(os.getenv("MEMORY_WINDOW", "6"))
# Redis 中对话历史的过期时间（秒），默认 7 天
REDIS_TTL: int = int(os.getenv("REDIS_TTL", "604800"))

# ====== Skill 委派 ======
# skill 内容库根目录（delegate_task 工具读源），空 = 缺省项目根 skills/（<name>/SKILL.md）
SKILLS_DIR: str = os.getenv("SKILLS_DIR", "")
# fork 子代理事件级空闲阈值（秒）：超时无任一增量事件（reasoning/content/工具）即断，
# 正常长思考为流式增量不误杀，仅完全静默才断
DELEGATE_MAX_IDLE_S: float = float(os.getenv("DELEGATE_MAX_IDLE_S", "60"))
# fork 子代理总时长保险丝（秒，deep_thinking=false 档）；deep_thinking=true 走下一条
DELEGATE_TOTAL_TIMEOUT_S: float = float(os.getenv("DELEGATE_TOTAL_TIMEOUT_S", "240"))
# fork 子代理总时长保险丝（秒，deep_thinking=true 档）：深思考长题放宽
DELEGATE_TOTAL_TIMEOUT_THINKING_S: float = float(
    os.getenv("DELEGATE_TOTAL_TIMEOUT_THINKING_S", "600")
)
# 每会话"模型裁量委派"次数上限（design D10）。
# 只对 `delegate_task` 计数；`/xxx` 直出不消耗。取值依据：P2 前提确认实测的
# 真实会话委派高水位为 3/会话（79 会话中仅 2 个有委派），50 约为其 16 倍，
# 宁宽不宁紧；配合 `delegate skip` 的命中率观测，上线一版数据后再收紧。
DELEGATE_MAX_PER_SESSION: int = int(os.getenv("DELEGATE_MAX_PER_SESSION", "50"))

# ====== 生产链路角色级超时（自有键名；评测链用 JUDGE_*/EVAL_* 族，两者不共享）======
# 依据：change timeout-and-degradation-hardening 的 design D2/D11。
# ⚠ 这些值待真实分位校准（D11 取值表标注"待校准"）；全部可被 .env 覆盖。
HTTP_TIMEOUT_CONNECT: float = float(os.getenv("HTTP_TIMEOUT_CONNECT", "10"))
HTTP_TIMEOUT_WRITE: float = float(os.getenv("HTTP_TIMEOUT_WRITE", "30"))
HTTP_TIMEOUT_POOL: float = float(os.getenv("HTTP_TIMEOUT_POOL", "10"))
# main 为流式：read 是「请求发出 → 字节到达」的**单一预算**（含首字节等待，D4）
MAIN_TIMEOUT_READ: float = float(os.getenv("MAIN_TIMEOUT_READ", "180"))
CLASSIFY_TIMEOUT_READ: float = float(os.getenv("CLASSIFY_TIMEOUT_READ", "60"))
SUMMARY_TIMEOUT_READ: float = float(os.getenv("SUMMARY_TIMEOUT_READ", "60"))
# embedding 两档：查询（在线、失败可退词法）要紧；入库（离线批量）可宽（D10 的唯一例外）
EMBEDDING_TIMEOUT_READ_QUERY: float = float(
    os.getenv("EMBEDDING_TIMEOUT_READ_QUERY", "15")
)
EMBEDDING_TIMEOUT_READ_INGEST: float = float(
    os.getenv("EMBEDDING_TIMEOUT_READ_INGEST", "150")
)
# rerank：内层唯一预算（外层 wait_for 见 RERANK_OUTER_TIMEOUT，必须 > 内层，D13）
RERANK_TIMEOUT_READ: float = float(os.getenv("RERANK_TIMEOUT_READ", "5"))
RERANK_OUTER_TIMEOUT: float = float(os.getenv("RERANK_OUTER_TIMEOUT", "6"))
# rerank 只做 1 次尝试：它是每轮检索都调的高频路径，重试一次 5s 同步调用收益很小；
# 且必须让「内层唯一预算（5s）」成为真实上界，否则外层 6s 会先放弃、线程跑到底（D13）。
RERANK_MAX_ATTEMPTS: int = int(os.getenv("RERANK_MAX_ATTEMPTS", "1"))
# ── 业务层「生成任务静默」兜底（P3 / D15；与传输层 read 是**两个层次**）──
# 判据不同：read 看「字节间隔」，本组看「事件条数是否增长」（模型只吐 tool_call
# 而不吐 token 时字节在动、用户无感）。四档 = {首事件前 / 首事件后} × {非思考 / 思考}；
# 开思考时首个 chunk 显著更晚 ⇒ 思考档更宽（与 DELEGATE_MAX_IDLE_S 的既有做法同构）。
# ⚠ 两档都必须 **小于** MAIN_TIMEOUT_READ，否则传输层先触发、该档永不负责（启动期断言）。
MAIN_IDLE_FIRST_S: float = float(os.getenv("MAIN_IDLE_FIRST_S", "60"))
MAIN_IDLE_FIRST_THINKING_S: float = float(
    os.getenv("MAIN_IDLE_FIRST_THINKING_S", "150")
)
MAIN_IDLE_GAP_S: float = float(os.getenv("MAIN_IDLE_GAP_S", "30"))
MAIN_IDLE_GAP_THINKING_S: float = float(os.getenv("MAIN_IDLE_GAP_THINKING_S", "60"))
# 轮询间隔：最小档位是 30s，5s 轮询把"发现延迟"控制在阈值 +5s 内
MAIN_IDLE_POLL_INTERVAL_S: float = float(os.getenv("MAIN_IDLE_POLL_INTERVAL_S", "5"))
# 按角色查表；judge **不在表内**（其真源归评测链，D17）
ROLE_TIMEOUT_READ: dict[str, float] = {
    "main": MAIN_TIMEOUT_READ,
    "classify": CLASSIFY_TIMEOUT_READ,
    "summary": SUMMARY_TIMEOUT_READ,
    "embedding": EMBEDDING_TIMEOUT_READ_INGEST,
    "rerank": RERANK_TIMEOUT_READ,
}


def validate_timeout_nesting() -> None:
    """启动期 fail-fast：① rerank 内层总预算必须**小于**外层；② 四档 idle 阈值必须小于传输层 read。

    三层超时必须**嵌套**（D13）：内层是客户端 `request_timeout`，外层是
    `asyncio.wait_for`。若内层总预算 ≥ 外层，外层会先放弃等待，而底层
    `to_thread` 线程**不随 wait_for 取消**、仍跑到底 —— 表现为"看似降级成功、
    实则线程堆积"。默认取值（1 × 5s = 5s < 6s）满足约束；一旦被 env 把
    `RERANK_MAX_ATTEMPTS` 调大（如 3 ⇒ ≈18s），该复合超时会原样回归，故在此
    启动期拦截（不随 `PROVIDER_VALIDATE` 开关跳过）。

    业务层四档 idle 阈值（D15）与传输层 read 是**两个层次**：两档都必须
    < `MAIN_TIMEOUT_READ`，否则传输层先触发、业务层该档永不负责。

    Raises:
        RuntimeError: 内层总预算 ≥ 外层超时；或某档 idle ≥ 传输层 read。
    """
    budget = RERANK_MAX_ATTEMPTS * RERANK_TIMEOUT_READ
    if budget >= RERANK_OUTER_TIMEOUT:
        raise RuntimeError(
            "rerank 三层超时未嵌套：内层总预算 "
            f"RERANK_MAX_ATTEMPTS({RERANK_MAX_ATTEMPTS}) × "
            f"RERANK_TIMEOUT_READ({RERANK_TIMEOUT_READ}s) = {budget}s "
            f"必须小于外层 RERANK_OUTER_TIMEOUT({RERANK_OUTER_TIMEOUT}s)；"
            "否则外层 wait_for 先放弃等待、底层线程仍跑到底（看似降级成功、"
            "实则线程堆积）。请调小 RERANK_MAX_ATTEMPTS 或调大 RERANK_OUTER_TIMEOUT。"
        )
    for label, value in (
        ("MAIN_IDLE_FIRST_S", MAIN_IDLE_FIRST_S),
        ("MAIN_IDLE_FIRST_THINKING_S", MAIN_IDLE_FIRST_THINKING_S),
        ("MAIN_IDLE_GAP_S", MAIN_IDLE_GAP_S),
        ("MAIN_IDLE_GAP_THINKING_S", MAIN_IDLE_GAP_THINKING_S),
    ):
        if value >= MAIN_TIMEOUT_READ:
            raise RuntimeError(
                f"idle 档位 {label}({value}s) 必须小于传输层 "
                f"MAIN_TIMEOUT_READ({MAIN_TIMEOUT_READ}s)：否则传输层 read 会先触发，"
                "该档永不负责（业务层本应是对用户负责的那一层，D15）。"
                "请调小该档或调大 MAIN_TIMEOUT_READ。"
            )


# ====== 重试策略 ======
# 外部调用（DashScope / PostgreSQL / Redis）失败时的指数退避参数
# 重试 3 次，初始间隔 1s，每次翻倍：1s → 2s → 4s
RETRY_MAX_ATTEMPTS: int = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_INITIAL_INTERVAL: float = float(os.getenv("RETRY_INITIAL_INTERVAL", "1.0"))
RETRY_BACKOFF_FACTOR: float = float(os.getenv("RETRY_BACKOFF_FACTOR", "2.0"))

# ====== 文件上传 ======
# 单文件上传大小上限（字节），默认 50MB
MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", str(10 * 1024 * 1024)))

# ====== MinIO ======
# 对象存储服务，用于持久化原始文档文件
MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "miniosecret")
MINIO_DOC_BUCKET: str = os.getenv("MINIO_DOC_BUCKET", "documents")

# ====== Auth ======
# 认证 Token 过期时间（秒），默认 30 天
AUTH_TOKEN_TTL: int = int(os.getenv("AUTH_TOKEN_TTL", "2592000"))

# ====== Langfuse ======
# 自托管后端见 docs/adr/0011-langfuse-v2-downgrade.md。
# **内置默认值不掩盖缺配**：两个 key 默认为空串，缺 .env 时不会拿一对默认 key 去连；
# HOST 默认指向 compose 服务名 langfuse-web:3000（容器内可达，宿主 / CI 不可解析）——
# 于是无 .env 的 K8s/CI/新 clone 表现为「构造真实客户端并向该地址发起网络请求」，
# 而非静默跳过。
LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
# 容器内地址为 compose 服务名 langfuse-web:3000；宿主机访问用 127.0.0.1:3000
LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "http://langfuse-web:3000")
# 全局开关：false 时完全不产出 trace（且不影响对话）
LANGFUSE_ENABLE: bool = os.getenv("LANGFUSE_ENABLE", "true").lower() == "true"

# ====== 模型定价（Langfuse 成本计算）======
# 供 seed CLI 写入 Langfuse 的模型定义；**单位是单个 token**（不是每 1M），价格字段本身
# 不含币种。例：$3 per 1M tokens 要填 0.000003。
# 本仓库为 qwen3.8-flash 部署配置的数值是**人民币**：0.8 / 2.7 元每百万 token 按数字原样
# 写入为 0.0000008 / 0.0000027（取 cache-miss 输入价）。Langfuse 界面把价格硬编码标注为
# USD，那个 `$` 只是名义币种，数值口径实为人民币，读成本列时不要按汇率换算。
# 0.0 是哨兵值，表示「未配置」：seed CLI 会跳过，不在 Langfuse 里留下会被误读成
# 「免费」的零价模型定义。
MODEL_INPUT_PRICE_PER_TOKEN: float = float(
    os.getenv("MODEL_INPUT_PRICE_PER_TOKEN", "0")
)
MODEL_OUTPUT_PRICE_PER_TOKEN: float = float(
    os.getenv("MODEL_OUTPUT_PRICE_PER_TOKEN", "0")
)

# ====== 分块质量评估 ======
# 分块质量评估开关：true 时上传文件后自动跑 3 个质量指标
# 默认关闭，不影响现有流程
CHUNK_EVAL_ENABLED: bool = os.getenv("CHUNK_EVAL_ENABLED", "true").lower() == "true"

# ── 企业微信智能机器人（多机器人长连接 / URL 回调 legacy）──
WECOM_BOT_ENABLED: bool = os.getenv("WECOM_BOT_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
# 接入模式：long_connection=多机器人长连接（默认）；callback=URL 回调（legacy 单机器人）
WECOM_BOT_MODE: str = os.getenv("WECOM_BOT_MODE", "long_connection")
# 多机器人长连接凭证：JSON 数组，形如 [{"key","bot_id","secret"}]，必须单行
# 解析见 src/config/wecom_bots.py 的 load_wecom_bots()
WECOM_BOTS: str = os.getenv("WECOM_BOTS", "")
# 以下三个仅 callback 模式使用（legacy）
WECOM_BOT_TOKEN: str = os.getenv("WECOM_BOT_TOKEN", "")
WECOM_BOT_ENCODING_AES_KEY: str = os.getenv("WECOM_BOT_ENCODING_AES_KEY", "")
WECOM_BOT_RECEIVE_ID: str = os.getenv("WECOM_BOT_RECEIVE_ID", "")
WECOM_BOT_LOG_CONTENT: bool = os.getenv("WECOM_BOT_LOG_CONTENT", "false").lower() in (
    "1",
    "true",
    "yes",
)
