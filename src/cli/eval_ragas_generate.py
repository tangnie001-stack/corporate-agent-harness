"""RAGAS 测试集生成模块 — 从知识库文档自动生成 QA 测试集.

本模块被 eval_ragas.py 的 --generate 模式调用，包含：
  - vertexai stub 自动修复（ragas 兼容性）
  - 从分块存储读取已有分块数据
  - 分步构建 KG + DiskCacheBackend 缓存（支持中断恢复）
  - TestsetGenerator 编排
  - 测试集版本管理与 JSON 写入

运行方式：
  python -m src.cli.eval_ragas --kb-name "xxx" --generate --size 20
"""

import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from loguru import logger

from src.config import RAGAS_DATA_DIR, RAGAS_DOC_WHITELIST, RAGAS_TESTSET_DIR
from src.config.const import RAGAS_TEST_PERSONAS
from src.core import logging as core_logging
from src.core.log_events import Event


def _ensure_vertexai_stub() -> None:
    """检查 langchain_community.chat_models.vertexai 模块是否存在，
    不存在则创建空 stub 以满足 ragas 的硬导入需求。

    ragas 0.4.x 在 llms/base.py 中:
      from langchain_community.chat_models.vertexai import ChatVertexAI
    但 langchain-community>=0.4 已移除该子模块，
    且 ragas 仅用于 __all__ re-export，不涉及实际逻辑。
    """
    try:
        from langchain_community.chat_models import vertexai  # noqa: F401
    except ImportError:
        # 确定 langchain_community.chat_models 的物理路径
        import langchain_community.chat_models as chat_models_pkg

        pkg_path = Path(chat_models_pkg.__file__).parent
        stub_path = pkg_path / "vertexai.py"
        stub_path.write_text(
            "# Auto-generated stub for ragas compatibility\n"
            "class ChatVertexAI:\n"
            "    pass\n"
            "\n"
            "class VertexAI:\n"
            "    pass\n"
        )
        core_logging.log_event(Event.VERTEXAI_STUB_CREATED, file=str(stub_path))

    # 测试集 JSON 结构


# {
#   "metadata": {
#     "kb_name": str,
#     "version": int,
#     "generated_at": str,        # ISO 8601
#     "llm_model": str,
#     "testset_size": int,
#     "ragas_version": str,
#     "doc_ids": list[str]
#   },
#   "samples": [
#     {
#       "user_input": str,
#       "reference": str,
#       "reference_contexts": list[str],
#       "synthesizer_name": str
#     }
#   ]
# }


def _find_next_version(kb_id: str) -> int:
    """扫描 testsets 目录下 testset_{kb_id}_v*.json，返回下一个版本号。

    Args:
        kb_id: 知识库 UUID

    Returns:
        下一个版本号（从 1 开始）
    """
    pattern = re.compile(rf"^testset_{re.escape(kb_id)}_v(\d+)\.json$")
    max_version = 0
    testset_dir = Path(RAGAS_TESTSET_DIR)
    if testset_dir.exists():
        for f in testset_dir.iterdir():
            m = pattern.match(f.name)
            if m:
                ver = int(m.group(1))
                max_version = max(max_version, ver)
    return max_version + 1


def _proofread_question(question: str, llm) -> str:
    """用 LLM 校对单条问题，修正错别字，保持原意、数字与专名不变。

    ragas synthesizer 生成问题时偶尔产出同音错别字（如「股漂」→「股票」、
    「正券」→「证券」），导致检索命中率下降。生成后统一过一遍校对，
    用纠错 prompt 清洗，输出失败时保留原文（best-effort）。

    Args:
        question: 原始问题文本
        llm: langchain ChatOpenAI 实例（同步调用）

    Returns:
        校对后的问题文本；LLM 调用失败时返回原文
    """
    if not question or not question.strip():
        return question
    prompt = (
        "你是一名中文校对专家。下面是一句可能含有错别字的中文问题，"
        "请修正其中的错别字和不规范表达（例如「股漂」应为「股票」）。"
        "必须保持原意、数字、专有名词（公司名/人名/股票代码）不变，"
        "不要改写句式。只输出修正后的问题文本，不要任何解释或前缀。\n\n"
        f"问题：{question}\n修正后："
    )
    try:
        result = llm.invoke(prompt)
        cleaned = result.content.strip()
        # 防御：LLM 偶发返回空或整句反问，此时保留原文
        if not cleaned:
            return question
        return cleaned
    except Exception as e:  # noqa: BLE001
        core_logging.log_event(
            Event.QUESTION_PROOFREAD_FAILED, question=question, err=str(e)
        )
        return question


def _clean_garbled_questions(samples_list: list[dict], llm) -> list[dict]:
    """批量校对测试集样本的问题文本，修正乱码/错别字。

    Args:
        samples_list: testset.to_list() 输出的样本 dict 列表
        llm: langchain ChatOpenAI 实例（同步调用）

    Returns:
        校对后的样本 dict 列表（仅替换 user_input，其余字段不变）
    """
    cleaned_count = 0
    for sample in samples_list:
        original = sample.get("user_input", "")
        if not original:
            continue
        corrected = _proofread_question(original, llm)
        if corrected != original:
            sample["user_input"] = corrected
            cleaned_count += 1
    if cleaned_count:
        core_logging.log_event(
            Event.QUESTION_PROOFREAD_DONE,
            cleaned=cleaned_count,
            total=len(samples_list),
        )
    return samples_list


def _load_latest_testset(
    kb_id: str, version: int | None = None
) -> tuple[list[str], list[str]]:
    """加载指定知识库的测试集，支持指定版本或自动取最新。

    Args:
        kb_id: 知识库 UUID
        version: 指定版本号（None 表示自动取最新版本）

    Returns:
        (questions, ground_truth) 元组，分别对应问题和参考答案列表

    Raises:
        FileNotFoundError: 没有找到该知识库的测试集文件
    """
    pattern = re.compile(rf"^testset_{re.escape(kb_id)}_v(\d+)\.json$")
    testset_dir = Path(RAGAS_TESTSET_DIR)

    if version is not None:
        # 指定版本
        target_file = testset_dir / f"testset_{kb_id}_v{version}.json"
        if not target_file.exists():
            raise FileNotFoundError(f"测试集文件不存在: {target_file}")
        latest_file = target_file
    else:
        # 自动取最新
        max_version = 0
        latest_file = None
        if testset_dir.exists():
            for f in testset_dir.iterdir():
                m = pattern.match(f.name)
                if m:
                    ver = int(m.group(1))
                    if ver > max_version:
                        max_version = ver
                        latest_file = f

        if latest_file is None:
            raise FileNotFoundError(
                f"No testset found for kb_id={kb_id}. 请先运行 --generate 生成测试集"
            )

    with open(latest_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    questions = [s["user_input"] for s in data["samples"]]
    ground_truth = [s["reference"] for s in data["samples"]]
    return questions, ground_truth


def _context_header(meta: dict) -> str:
    """从分块元数据构造【公司名 · 报告期】锚点前缀（design D8 的 P1）。

    RAGAS 出题 prompt 只收到 `page_content` + `themes`，而中文年报正文常自称
    「本公司」、正文不出现公司全名 ⇒ 模型无实体可消解，产出「本报告期…」
    这类题面不自足的题。故在送入出题前，把分块元数据里的公司名与报告期
    前置进正文，给出题模型一个明确的实体锚点。（只影响出题用的 KG，
    不动生产索引与评估检索栈。）

    取值优先级：`company` → `source`（文件名兜底）；`report_period` →
    `year` + `quarter`。两者皆无可用值时返回空串（不产生半截锚点）。

    Args:
        meta: 分块元数据字典（`document_metadata`）

    Returns:
        形如 `"【东软集团股份有限公司 · 2025年第一季度】\\n"` 的前缀；无可用字段时 ""
    """
    company = str(meta.get("company") or "").strip()
    period = str(meta.get("report_period") or "").strip()
    if not period:
        year = str(meta.get("year") or "").strip()
        quarter = str(meta.get("quarter") or "").strip()
        period = f"{year}{quarter}"
    if not company:
        company = str(meta.get("source") or "").strip()

    fields = [f for f in (company, period) if f]
    if not fields:
        return ""
    return "【" + " · ".join(fields) + "】\n"


def _make_generator(llm, embeddings):
    """构造 TestsetGenerator，并带上自足性约束（design D8）。"""
    from ragas.testset.synthesizers.generate import TestsetGenerator

    from src.config.const import RAGAS_SELF_CONTAINED_HINT

    return TestsetGenerator(
        llm=llm, embedding_model=embeddings, llm_context=RAGAS_SELF_CONTAINED_HINT
    )


def _build_transforms(generator, use_filter: bool) -> list | None:
    """出题 transforms：默认走官方全量转换链（已分块版）。

    原实现手搓 5 个 transforms，**缺 `ThemesExtractor` 与
    `CosineSimilarityBuilder`**，导致 `MultiHopAbstractQuerySynthesizer` 取不到
    `summary_similarity` 聚类而被静默丢弃（design D8）。官方
    `default_transforms_for_prechunked()` 含全部 7 步，且按 CHUNK 节点过滤，
    契合本仓由分块构建的知识图谱（节点全为 `NodeType.CHUNK`）。

    Args:
        generator: TestsetGenerator 实例（提供 llm / embedding_model）
        use_filter: True 时不设 transforms（交回 ragas 默认）

    Returns:
        transforms 列表；use_filter 为真时返回 None
    """
    if use_filter:
        return None

    from ragas.testset.transforms.default import default_transforms_for_prechunked

    # ragas 的返回注解是 `Transforms`（含 `Parallel` 的联合类型），运行时确为列表
    transforms = cast(
        list,
        default_transforms_for_prechunked(
            llm=generator.llm, embedding_model=generator.embedding_model
        ),
    )
    core_logging.log_event(
        Event.TRANSFORMS_SETUP, steps=[type(t).__name__ for t in transforms]
    )
    return transforms


def run_generate(
    kb_id: str,
    size: int,
    model: str = "",
    use_filter: bool = False,
) -> None:
    """运行测试集生成流程：从分块存储取 chunk → 构建 KG → 生成 → 保存 JSON.

    流程：
      0. 从数据库查询 kb_name 和 doc_names
      1. 从白名单获取 doc_ids
      2. 从分块存储按 doc_id 取出已有分块
      3. 脱敏后构建 KnowledgeGraph
      4. 应用 transforms（SummaryExtractor / NERExtractor 等）
      5. 保存 KG 到磁盘（支持中断恢复时跳过 transforms）
      6. 生成 Personas → Scenarios → Samples
      7. 写入 data/ragas/testset_{kb_id}_vN.json

    Args:
        kb_id: 知识库 UUID
        size: 生成的 QA 对数
        model: 生成用的 LLM 模型名（空字符串则使用 RAGAS_LLM_MODEL 或 LLM_MODEL）
        use_filter: 是否启用 LLM 节点过滤（关闭可节省约 70 次 LLM 调用）

    Raises:
        SystemExit: 分块存储中无数据 / 生成失败时退出进程
    """
    _ensure_vertexai_stub()

    from src.infra.db.engine import session_factory
    from src.infra.db.repos import DocumentRepo, KbRepo
    from src.infra.db.vector_store import VectorStore

    # ---- 0. 查询 kb_name / doc_names，并按白名单读取分块 ----
    # 两件事合并到一次 asyncio.run：引擎连接池不可跨事件循环复用
    async def _load_meta_and_chunks() -> tuple[str, dict[str, str], list[tuple]]:
        """读 kb 元信息与白名单文档的全部分块。

        Returns:
            (kb_name, doc_names, loaded)：loaded 为 (doc_id, 分块列表) 序列，
            与 RAGAS_DOC_WHITELIST 同序。
        """
        repo = KbRepo(session_factory)
        name = await repo.get_kb_name_by_id(kb_id)
        if not name:
            raise ValueError(f"知识库 {kb_id} 不存在")
        doc_repo = DocumentRepo(session_factory)
        doc_names = await doc_repo.get_doc_names(RAGAS_DOC_WHITELIST)
        store = VectorStore()
        loaded: list[tuple] = []
        for whitelist_doc_id in RAGAS_DOC_WHITELIST:
            chunks = await store.get_chunks_by_doc_id(whitelist_doc_id, kb_id)
            loaded.append((whitelist_doc_id, chunks))
        return name, doc_names, loaded

    try:
        kb_name, doc_names_map, loaded_chunks = asyncio.run(_load_meta_and_chunks())
    except ValueError as e:
        core_logging.log_event(Event.KB_META_FAILED, err=str(e))
        print(f"✗ {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        core_logging.log_event(Event.KB_META_FAILED, err=str(e))
        print(f"✗ 查询知识库元信息失败: {e}")
        sys.exit(1)

    import ragas
    from langchain_core.documents import Document as LCDocument

    from src.config import settings
    from src.models import get_embeddings
    from src.utils.desensitize import desensitize

    # ---- 1. 装载白名单文档的分块（已在上面的单次事件循环内读出）----
    core_logging.log_event(
        Event.CHUNK_LOAD_START, kb_id=kb_id, whitelist=RAGAS_DOC_WHITELIST
    )
    langchain_chunks: list[LCDocument] = []
    doc_ids: list[str] = []
    success_count = 0

    for doc_id, chunks_data in loaded_chunks:
        if not chunks_data:
            core_logging.log_event(Event.CHUNKS_NOT_FOUND, doc_id=doc_id)
            print(f"  ⚠ doc_id={doc_id} 在知识库中无数据，已跳过")
            continue

        for c in chunks_data:
            safe_content = desensitize(c.content)
            meta = dict(c.metadata)
            meta["parent_content"] = ""  # 清空原文，避免敏感信息泄漏
            langchain_chunks.append(
                LCDocument(
                    # 前置【公司·报告期】实体锚点，使出题模型点名公司（design D8 的 P1）
                    page_content=_context_header(meta) + safe_content,
                    metadata=meta,
                )
            )
        doc_ids.append(doc_id)
        success_count += 1
        print(f"  ✓ doc_id={doc_id} ({len(chunks_data)} 个 chunk)")

    if success_count == 0:
        core_logging.log_event(Event.NO_CHUNK_DATA)
        print("✗ 白名单中所有文档在分块存储中均无数据")
        sys.exit(1)

    core_logging.log_event(
        Event.CHUNK_LOAD_DONE,
        docs=success_count,
        chunks=len(langchain_chunks),
    )

    # ---- 3. 初始化 RAGAS 组件（带 DiskCacheBackend 缓存） ----
    # 生成用 LLM 走 judge 角色绑定：--model 显式指定 > RAGAS_LLM_MODEL env > registry
    # 默认；两者均空时由 registry 兜底，故不再需要前置空值守卫。
    from src.models import get_judge_llm

    judge_model: str | None = model
    if not judge_model:
        judge_model = None
    _langchain_llm = get_judge_llm(model=judge_model)
    eval_model = _langchain_llm.model_name
    core_logging.log_event(
        Event.GENERATOR_INIT,
        model=eval_model,
        size=size,
        chunks=len(langchain_chunks),
    )
    print(
        f"\n初始化 RAGAS 组件 ({len(langchain_chunks)} 个 chunk, model={eval_model})..."
    )

    from ragas.cache import DiskCacheBackend
    from ragas.embeddings import LangchainEmbeddingsWrapper as _EmbeddingsWrapper
    from ragas.llms.base import LangchainLLMWrapper as _LLMWrapper
    from ragas.testset import Testset
    from ragas.testset.persona import Persona

    _cache = DiskCacheBackend(cache_dir=settings.RAGAS_LLM_CACHE_DIR)
    ragas_llm = _LLMWrapper(_langchain_llm, cache=_cache, bypass_n=True)
    embeddings_wrapper = _EmbeddingsWrapper(get_embeddings())

    generator = _make_generator(llm=ragas_llm, embeddings=embeddings_wrapper)
    # 固定人设：persona_list 非空时 ragas 会跳过其内置的随机 persona 生成步
    # （见 const.RAGAS_TEST_PERSONAS 注释说明的 KeyError 成因）。
    generator.persona_list = [
        Persona(name=name, role_description=desc) for name, desc in RAGAS_TEST_PERSONAS
    ]

    # ---- 4. 构建 transforms ----
    # 默认走官方全量链（7 步，含 ThemesExtractor / CosineSimilarityBuilder）；
    # use_filter 时交回 ragas 默认（不设 transforms）。
    transforms: list | None = _build_transforms(generator, use_filter)

    # ---- 5. 构建知识图谱（支持中断恢复） ----
    from ragas.run_config import RunConfig
    from ragas.testset.graph import KnowledgeGraph, Node
    from ragas.testset.graph import NodeType as _NodeType
    from ragas.testset.transforms import apply_transforms

    kg_file = os.path.join(RAGAS_DATA_DIR, f"kg_{kb_id}.json")

    if os.path.exists(kg_file):
        core_logging.log_event(Event.KNOWLEDGE_GRAPH_FOUND, file=kg_file)
        print("  ↻ 加载已有知识图谱，跳过 transforms...")
        kg = KnowledgeGraph.load(kg_file)
        generator.knowledge_graph = kg
    else:
        core_logging.log_event(
            Event.KNOWLEDGE_GRAPH_BUILD, chunks=len(langchain_chunks)
        )
        print(f"  → 构建知识图谱 ({len(langchain_chunks)} 个 chunk)...")

        nodes = []
        for chunk in langchain_chunks:
            if chunk.page_content is not None and chunk.page_content.strip() != "":
                node = Node(
                    type=_NodeType.CHUNK,
                    properties={
                        "page_content": chunk.page_content,
                        "document_metadata": chunk.metadata,
                    },
                )
                nodes.append(node)

        kg = KnowledgeGraph(nodes=nodes)

        # 应用 transforms
        if transforms is not None:
            print("  → 应用 transforms...")
            apply_transforms(kg, transforms, run_config=RunConfig())
        generator.knowledge_graph = kg

        # 保存 KG 到磁盘
        os.makedirs(RAGAS_DATA_DIR, exist_ok=True)
        kg.save(kg_file)
        core_logging.log_event(Event.KNOWLEDGE_GRAPH_SAVED, file=kg_file)
        print(f"  ✓ 知识图谱已保存 ({len(kg.nodes)} 个节点)")

    # ---- 6. 生成测试集 ----
    core_logging.log_event(Event.TESTSET_GENERATION_START, size=size)
    print(f"正在生成测试集 ({size} 条)...")

    try:
        testset = cast(
            Testset,
            generator.generate(
                testset_size=size,
                num_personas=len(RAGAS_TEST_PERSONAS),
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("[cli] testset generator call failed err={}", str(e))
        print(f"✗ 测试集生成失败: {e}")
        print(f"✗ 异常类型: {type(e).__name__}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # ---- 7. 序列化为 JSON ----
    samples_list = testset.to_list()
    # 校对乱码/错别字问题（同音字导致检索命中率下降）
    samples_list = _clean_garbled_questions(samples_list, _langchain_llm)
    version = _find_next_version(kb_id)

    output = {
        "metadata": {
            "kb_id": kb_id,
            "kb_name": kb_name,
            "doc_names": [doc_names_map.get(d, "") for d in doc_ids],
            "version": version,
            "generated_at": datetime.now(UTC).isoformat(),
            "llm_model": eval_model,
            "testset_size": len(samples_list),
            "ragas_version": ragas.__version__,
            "doc_ids": doc_ids,
        },
        "samples": samples_list,
    }

    # ---- 8. 原子写入 ----
    os.makedirs(RAGAS_TESTSET_DIR, exist_ok=True)
    output_path = os.path.join(RAGAS_TESTSET_DIR, f"testset_{kb_id}_v{version}.json")
    tmp_path = output_path + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, output_path)

    core_logging.log_event(
        Event.TESTSET_SAVED,
        file=output_path,
        count=len(samples_list),
        version=version,
    )
    print(f"\n测试集已保存: {output_path} (v{version}, {len(samples_list)} 条)")
