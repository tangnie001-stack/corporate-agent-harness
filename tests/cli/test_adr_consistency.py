"""测试 ADR 头部一致性检查（src/cli/check_adr.py）—— 防取代关系走偏。

防腐哲学与 `test_doc_consistency.py` 一致：把规则变成机械信号。历史上本目录同一
件事出现过四种写法、且被局部推翻的旧 ADR 一条反向指针都没有 —— 本测试既断言
**真实目录当前一致**（error 档为空），也用合成样本断言**漂移确实会被抓出来**
（否则检查器会退化成永不报错的空壳）。

A 系列（头部/索引自洽）与 R2（调研引用不落草稿区）在 `_run_all` 里一并跑；R2 还额外
断言**历史豁免集合恰好等于真实违规集合**，防两个方向的漂移：多一条是遮蔽新违规，
少一条是让存量踩红线。
"""

from pathlib import Path

from src.cli import check_adr as ca

_REAL_INDEX = ca._PROJECT_ROOT / "docs" / "adr" / "README.md"


def _write_adr(directory: Path, number: str, name: str, header: str) -> None:
    """在指定目录写一条最小 ADR（只含头部与一个正文小节）。

    Args:
        directory: ADR 目录
        number: 四位编号（用于文件名）
        name: 文件名短标题（kebab-case）
        header: 头部字段块（不含标题行与正文小节）
    """
    body = f"# ADR-{number}：示例\n\n{header}\n\n## 决策\n\n选 A。\n"
    (directory / f"{number}-{name}.md").write_text(body, encoding="utf-8")


def _write_index(directory: Path, rows: list[str]) -> None:
    """在指定目录写最小索引 README。

    Args:
        directory: ADR 目录
        rows: 索引数据行（完整 markdown 表格行）
    """
    table = "\n".join(rows)
    (directory / "README.md").write_text(
        f"# 架构决策记录\n\n## 索引\n\n| # | 决策 | Status | 取代关系 |\n|---|---|---|---|\n{table}\n",
        encoding="utf-8",
    )


def _run_all(adr_dir: Path) -> list[ca.AdrFinding]:
    """对给定目录跑全部检查项，返回 findings 列表。

    Args:
        adr_dir: ADR 目录

    Returns:
        全部 error 档
    """
    adrs = ca._load_adrs(adr_dir)
    by_number = {a.number: a for a in adrs if a.number}
    findings: list[ca.AdrFinding] = []
    findings.extend(ca._check_filenames_and_numbering(adrs))
    findings.extend(ca._check_required_fields(adrs))
    findings.extend(ca._check_pointer_symmetry(adrs, by_number))
    findings.extend(ca._check_index(adrs, adr_dir / "README.md"))
    findings.extend(ca._check_research_refs(adrs))
    return findings


def test_real_adr_directory_is_consistent():
    """真实 docs/adr/ 全部检查项 0 error（规则已落地，不得回退）。"""
    findings = _run_all(ca._ADR_DIR)
    assert findings == [], "docs/adr/ 头部规则已漂移:\n" + "\n".join(
        f"  ADR-{f.adr}:{f.line} {f.message}" for f in findings
    )


def test_missing_reverse_pointer_is_detected(tmp_path):
    """被取代的旧 ADR 缺反向指针 → error（这是本次要根治的失效方式）。"""
    _write_adr(
        tmp_path,
        "0001",
        "old-decision",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户",
    )
    _write_adr(
        tmp_path,
        "0002",
        "new-decision",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户\n"
        "- **Supersedes**：ADR-0001 的「决策 1」（局部）",
    )
    _write_index(
        tmp_path,
        [
            "| [0001](0001-old-decision.md) | 旧 | Accepted | — |",
            "| [0002](0002-new-decision.md) | 新 | Accepted | 局部取代 0001 |",
        ],
    )

    findings = _run_all(tmp_path)
    assert any("没有反向指针" in f.message for f in findings), findings


def test_reverse_pointer_satisfies_check(tmp_path):
    """旧 ADR 的 Status 写明取代者编号 → 不报。"""
    _write_adr(
        tmp_path,
        "0001",
        "old-decision",
        "- **Status**：Accepted（**决策 1 已被 ADR-0002 取代**，其余仍有效）\n"
        "- **Date**：2026-09-22\n- **Deciders**：用户",
    )
    _write_adr(
        tmp_path,
        "0002",
        "new-decision",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户\n"
        "- **Supersedes**：ADR-0001 的「决策 1」（局部）",
    )
    _write_index(
        tmp_path,
        [
            "| [0001](0001-old-decision.md) | 旧 | Accepted | 决策 1 已被 0002 取代 |",
            "| [0002](0002-new-decision.md) | 新 | Accepted | 局部取代 0001 |",
        ],
    )

    findings = _run_all(tmp_path)
    assert findings == [], findings


def test_local_supersede_waived_when_old_fully_superseded(tmp_path):
    """旧 ADR 已被第三条完全取代 → 不再要求局部反向指针（局部关系入索引备注）。"""
    _write_adr(
        tmp_path,
        "0001",
        "old-decision",
        "- **Status**：Superseded by 0003\n- **Date**：2026-09-22\n- **Deciders**：用户",
    )
    _write_adr(
        tmp_path,
        "0002",
        "partial-decision",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户\n"
        "- **Supersedes**：ADR-0001 的「决策 1」（局部）",
    )
    _write_adr(
        tmp_path,
        "0003",
        "full-decision",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户\n"
        "- **Supersedes**：ADR-0001",
    )
    _write_index(
        tmp_path,
        [
            "| [0001](0001-old-decision.md) | 旧 | Superseded by 0003 | 整条被 0003 取代 |",
            "| [0002](0002-partial-decision.md) | 局部 | Accepted | 局部取代 0001 |",
            "| [0003](0003-full-decision.md) | 新 | Accepted | 完全取代 0001 |",
        ],
    )

    findings = _run_all(tmp_path)
    assert findings == [], findings


def test_pointer_to_missing_adr_is_detected(tmp_path):
    """Supersedes 指向不存在的 ADR → error。"""
    _write_adr(
        tmp_path,
        "0001",
        "only-one",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户\n"
        "- **Supersedes**：ADR-0099 的「某条」（局部）",
    )
    _write_index(tmp_path, ["| [0001](0001-only-one.md) | 唯一 | Accepted | — |"])

    findings = _run_all(tmp_path)
    assert any("该 ADR 不存在" in f.message for f in findings), findings


def test_numbering_gap_is_detected(tmp_path):
    """编号跳号 → error。"""
    for number, name in (("0001", "first"), ("0003", "third")):
        _write_adr(
            tmp_path,
            number,
            name,
            "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户",
        )
    _write_index(
        tmp_path,
        [
            "| [0001](0001-first.md) | 一 | Accepted | — |",
            "| [0003](0003-third.md) | 三 | Accepted | — |",
        ],
    )

    findings = _run_all(tmp_path)
    assert any("编号不连续或跳号" in f.message for f in findings), findings


def test_missing_required_field_is_detected(tmp_path):
    """头部缺 Deciders → error。"""
    _write_adr(
        tmp_path,
        "0001",
        "no-deciders",
        "- **Status**：Accepted\n- **Date**：2026-09-22",
    )
    _write_index(tmp_path, ["| [0001](0001-no-deciders.md) | 一 | Accepted | — |"])

    findings = _run_all(tmp_path)
    assert any("缺少必填字段 `Deciders`" in f.message for f in findings), findings


def test_index_missing_row_is_detected(tmp_path):
    """ADR 未登记进索引表 → error。"""
    _write_adr(
        tmp_path,
        "0001",
        "first",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户",
    )
    _write_adr(
        tmp_path,
        "0002",
        "second",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户",
    )
    _write_index(tmp_path, ["| [0001](0001-first.md) | 一 | Accepted | — |"])

    findings = _run_all(tmp_path)
    assert any("未登记进 README 索引表" in f.message for f in findings), findings


def test_index_status_mismatch_is_detected(tmp_path):
    """索引 Status 列与文件头部不一致 → error。"""
    _write_adr(
        tmp_path,
        "0001",
        "first",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户",
    )
    _write_index(tmp_path, ["| [0001](0001-first.md) | 一 | Proposed | — |"])

    findings = _run_all(tmp_path)
    assert any("不一致" in f.message for f in findings), findings


def test_full_supersede_pointer_to_missing_adr_is_detected(tmp_path):
    """完全取代：Status 写 `Superseded by NNNN` 但 NNNN 不存在 → error。"""
    _write_adr(
        tmp_path,
        "0001",
        "retired",
        "- **Status**：Superseded by 0099\n- **Date**：2026-09-22\n- **Deciders**：用户",
    )
    _write_index(
        tmp_path, ["| [0001](0001-retired.md) | 旧 | Superseded by 0099 | — |"]
    )

    findings = _run_all(tmp_path)
    assert any("但该 ADR 不存在" in f.message for f in findings), findings


def test_full_supersede_symmetric_pair_passes(tmp_path):
    """完全取代：新 ADR 写 Supersedes，旧 ADR Status 写 Superseded by → 不报。"""
    _write_adr(
        tmp_path,
        "0001",
        "retired",
        "- **Status**：Superseded by 0002\n- **Date**：2026-09-22\n- **Deciders**：用户",
    )
    _write_adr(
        tmp_path,
        "0002",
        "replacement",
        "- **Status**：Accepted\n- **Date**：2026-09-22\n- **Deciders**：用户\n"
        "- **Supersedes**：ADR-0001",
    )
    _write_index(
        tmp_path,
        [
            "| [0001](0001-retired.md) | 旧 | Superseded by 0002 | — |",
            "| [0002](0002-replacement.md) | 新 | Accepted | 取代 0001 |",
        ],
    )

    findings = _run_all(tmp_path)
    assert findings == [], findings


def test_real_index_parses():
    """真实 README 索引表可解析且覆盖全部 ADR。"""
    order, statuses = ca._parse_index_statuses(_REAL_INDEX)
    assert order == sorted(order)
    assert set(order) == {a.number for a in ca._load_adrs(ca._ADR_DIR)}
    assert statuses["0003"] == "Accepted"


def _write_adr_citing(directory: Path, number: str, cited: str) -> None:
    """写一条正文引用某调研文档的最小 ADR。

    引用写进「背景与问题」小节，即头部之外 —— 这正是 R2 必须扫全文、不能只读头部
    的原因（`_parse_header` 遇到第一个 `##` 就停）。

    Args:
        directory: ADR 目录
        number: 四位编号
        cited: 被引用的路径原文
    """
    (directory / f"{number}-cites-research.md").write_text(
        f"# ADR-{number}：示例\n\n"
        "- **Status**：Accepted\n- **Date**：2026-10-13\n- **Deciders**：用户\n\n"
        "## 背景与问题\n\n"
        f"一手证据见 `{cited}`。\n",
        encoding="utf-8",
    )


def test_draft_area_research_citation_is_detected(tmp_path):
    """ADR 引用 docs/tmp/ 下的调研 → error（证据链不得挂在随时会被清理的目录上）。"""
    _write_adr_citing(tmp_path, "0001", "docs/tmp/deep-research-example.md")

    findings = ca._check_research_refs(ca._load_adrs(tmp_path), exempt=frozenset())

    assert any("草稿区" in f.message for f in findings), findings
    assert any("docs/tmp/deep-research-example.md" in f.message for f in findings), (
        findings
    )


def test_research_dir_citation_passes(tmp_path):
    """ADR 引用 docs/research/ 下的调研 → 不报。"""
    _write_adr_citing(tmp_path, "0001", "docs/research/2026-10-13-example.md")

    assert ca._check_research_refs(ca._load_adrs(tmp_path), exempt=frozenset()) == []


def test_legacy_exempt_number_is_honored(tmp_path):
    """豁免编号引用草稿区 → 默认不报；去掉豁免则报（证明豁免确实在起作用）。"""
    _write_adr_citing(tmp_path, "0002", "docs/tmp/deep-research-legacy.md")
    adrs = ca._load_adrs(tmp_path)

    assert ca._check_research_refs(adrs) == []
    assert ca._check_research_refs(adrs, exempt=frozenset()) != []


def test_legacy_exempt_set_is_exactly_real_offenders():
    """豁免集合 == 真实目录里引用草稿区的 ADR 集合。

    防两个方向的漂移：多一条是遮蔽新违规，少一条是让存量踩红线。
    """
    adrs = ca._load_adrs(ca._ADR_DIR)
    offenders = {f.adr for f in ca._check_research_refs(adrs, exempt=frozenset())}

    assert offenders == set(ca._LEGACY_DRAFT_CITATION_EXEMPT), offenders
