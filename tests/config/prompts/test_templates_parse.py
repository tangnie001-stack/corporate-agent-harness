"""23 条模板文件可解析、字段合法、id 唯一。"""

from pathlib import Path

import pytest
import yaml

TEMPLATES_DIR = (
    Path(__file__).resolve().parents[3] / "src" / "config" / "prompts" / "templates"
)
VALID_SECTIONS = {"base", "runtime_contract", "sources", "tools", "output"}


def _all_templates() -> list[dict]:
    """汇总所有模板文件里的模板条目。"""
    items: list[dict] = []
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name} 根节点不是映射"
        assert "templates" in data, f"{path.name} 缺少 templates 根键"
        for entry in data["templates"]:
            items.append({"_file": path.name, **entry})
    return items


def test_template_count_and_migrated_ids() -> None:
    """23 条 = 12 条搬运 + 1 条通用 base + 8 条新增段模板 + 1 条跨轮历史摘要 + 1 条告警判读解释（task-rca-explain-system）。"""
    templates = _all_templates()
    assert len(templates) == 23
    required_ids = {
        "tools-delegate",
        "base-financial",
        "sources-kb-unbound",
        "sources-kb-ladder",
        "task-user-prompt",
        "task-classifier-system",
        "task-classifier-user",
        "task-rewrite-system",
        "task-rewrite-user",
        "task-entity-system",
        "task-entity-user",
        "output-citation",
        "output-presentation",
    }
    assert required_ids <= {t["id"] for t in templates}, "上述模板 id 必须齐全"


def test_ids_are_unique() -> None:
    """id 全局唯一。"""
    ids = [t["id"] for t in _all_templates()]
    assert len(ids) == len(set(ids))


def test_section_templates_have_valid_section() -> None:
    """kind=section 的模板，section 取值在允许集合内。"""
    for t in _all_templates():
        if t["kind"] == "section":
            assert t["section"] in VALID_SECTIONS, t
        else:
            assert t["kind"] == "task", t
            assert "section" not in t, f"task 模板不应有 section：{t['id']}"


def test_base_templates_have_domain() -> None:
    """section=base 的模板必须声明 domain；通用 base 用保留值 general。"""
    for t in _all_templates():
        if t.get("section") == "base":
            assert t.get("domain"), f"{t['id']} 缺少 domain"


@pytest.mark.parametrize("tid", ["base-financial"])
def test_content_is_non_empty(tid: str) -> None:
    """段模板正文非空。"""
    entry = next(t for t in _all_templates() if t["id"] == tid)
    assert entry["content"].strip()


def test_runtime_contract_segment_exists_and_is_unconditional() -> None:
    """runtime_contract 段存在，且含数据·指令边界、运行上下文（含完成条件）与默认语言三条无条件规则。"""
    from src.config.prompts import loader

    template = loader.load_all()["runtime-contract"]
    assert template.kind == "section"
    assert template.section == "runtime_contract"
    assert template.domain is None
    content = template.content
    assert "不可信的来源数据" in content, "缺数据·指令边界"
    assert "停止调用工具" in content, "缺完成条件"
    assert "仅有进度更新不算完成任务" in content, "完成条件措辞不完整"


def test_output_segment_templates_exist() -> None:
    """output 段两条模板的归属字段正确，引用编码一条为无条件。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    citation = templates["output-citation"]
    assert citation.kind == "section"
    assert citation.section == "output"
    assert "[n]" in citation.content or "[1][2]" in citation.content

    delegated = templates["output-delegate-citation"]
    assert delegated.section == "output"
    assert "基于领域经验的分析" in delegated.content, (
        "EXPERT_ANALYSIS_MARKER 短语必须逐字保留：kb_citation_guardrail 靠它豁免"
    )


def test_old_output_inline_citation_template_removed() -> None:
    """旧模板 id 已删除（一条事实一个 owner，不留并存副本）。"""
    from src.config.prompts import loader

    assert "output-inline-citation" not in loader.load_all()


def test_tools_segment_has_three_templates() -> None:
    """tools 段三条模板：通用执行（无条件）+ ask_user + delegate，归属字段正确。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    for tid in ("tools-execution", "tools-ask-user", "tools-delegate"):
        template = templates[tid]
        assert template.kind == "section", tid
        assert template.section == "tools", tid

    assert "ask_user" in templates["tools-ask-user"].content
    assert "delegate_task" in templates["tools-delegate"].content


def test_ask_user_rule_not_in_sources() -> None:
    """澄清时机不写在来源段（spec「澄清时机不写在来源段」）。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    sources_text = "\n".join(
        t.content for t in templates.values() if t.section == "sources"
    )
    assert "ask_user" not in sources_text


def test_delegate_guidance_old_template_removed() -> None:
    """旧 tools-delegate-guidance 已删除，不留并存副本。"""
    from src.config.prompts import loader

    assert "tools-delegate-guidance" not in loader.load_all()


def test_sources_segment_templates_and_ownership() -> None:
    """sources 段三条工具相关模板归属正确；检索阶梯归本段、不在 base。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    for tid in ("sources-general", "sources-kb-ladder", "sources-kb-web-rules"):
        template = templates[tid]
        assert template.kind == "section", tid
        assert template.section == "sources", tid

    ladder = templates["sources-kb-ladder"].content
    assert "换一种问法" in ladder
    assert "top_k=10" in ladder
    assert "至少一个核心实体" in ladder

    web = templates["sources-kb-web-rules"].content
    assert "该问题不在当前知识库范围内" in web, "漂移拷贝删掉的出口指引必须补回"
    assert "未在文档中找到相关数据" in web
    assert "不要调用 search_web" in web


def test_drift_copy_template_removed() -> None:
    """漂移拷贝 sources-kb-bound-discipline 已删除。"""
    from src.config.prompts import loader

    assert "sources-kb-bound-discipline" not in loader.load_all()


def test_base_financial_is_slimmed() -> None:
    """base 段只留角色 + 领域方法 + 默认检索方法 + 指针句；运行时内容已移出。"""
    from src.config.prompts import loader

    content = loader.get_content("base-financial")

    # 移出的运行时内容（各自的唯一 owner 在 sources / tools / runtime_contract）
    for moved in (
        "先调用 retrieve_kb",
        "ask_user",
        "换一种问法",
        "top_k=10",
        "不要调用 search_web",
        "未在文档中找到相关数据",
        "回答必须忠实",
    ):
        assert moved not in content, f"{moved} 应已移出 base"

    # 留下的：角色 / 领域方法 / 默认检索方法 / 指针句
    assert "财务" in content
    assert "报告期" in content
    assert "关键指标与趋势" in content, (
        "领域输出骨架归 base（spec「领域输出骨架不写进输出段」）"
    )
    assert "先遵循运行时来源选择规则" in content, "缺优先级指针句"


def test_base_general_ends_with_pointer_sentence() -> None:
    """通用 base 同样以指针句收尾（领域 base 与预设是同段位的互斥候选）。"""
    from src.config.prompts import loader

    content = loader.get_content("base-general")
    assert "先遵循运行时来源选择规则" in content
    assert content.rstrip().endswith("属于任务本身。")


def test_base_does_not_contain_generic_output_rules() -> None:
    """通用输出形态不写进 base（spec「通用输出形态不写进 base」）。"""
    from src.config.prompts import loader

    for tid in ("base-financial", "base-general"):
        content = loader.get_content(tid)
        for generic in ("不强加 Markdown", "URL 保真", "完成前自检"):
            assert generic not in content, f"{tid} 出现了通用输出形态规则：{generic}"


def test_user_template_is_data_only() -> None:
    """用户模板只传数据（参考资料 / 用户请求），不含任何策略句。"""
    from src.config.prompts import loader

    content = loader.get_content("task-user-prompt")
    assert "{context}" in content
    assert "{query}" in content
    assert "参考资料" in content
    assert "用户请求" in content
    # 策略句（出口条件）已移出 —— 它的唯一 owner 是 sources 段
    assert "若【参考文档】为空" not in content
    assert "未在文档中找到相关数据" not in content


def test_user_template_placeholders_are_satisfiable() -> None:
    """模板占位符集合 ⊆ 消费点实际传入的变量集合（防 KeyError 每请求 500）。"""
    import re

    from src.config.prompts import loader

    content = loader.get_content("task-user-prompt")
    provided = {"context", "query"}
    used = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", content))
    assert used <= provided, f"模板引用了消费点未提供的占位符：{used - provided}"


def test_kb_unbound_split_into_core_and_web() -> None:
    """未绑定提示拆两条：核心句无条件、联网句依赖 search_web。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    core = templates["sources-kb-unbound"]
    web = templates["sources-kb-unbound-web"]
    assert core.section == "sources" and web.section == "sources"

    assert "请勿调用知识库检索工具" in core.content
    assert "不得声称检索过实际未检索的内容" in core.content
    assert "search_web" not in core.content, "核心句不得提及 search_web"

    assert "search_web" in web.content
    assert "[1][2]" in web.content
