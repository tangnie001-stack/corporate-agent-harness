#!/usr/bin/env python3
"""ADR 头部一致性检查 CLI — 防止取代关系与索引走偏。

背景（规则防腐）：`docs/adr/README.md` 规定了取代关系、反向指针与索引登记三件事，
但规则靠人记会漂 —— 历史上同一件事在本目录出现过四种写法（`Supersedes` 字段、
塞进 `Status` 的说明、`关系` 字段、原地修订），且被局部推翻的旧 ADR 一条反向指针
都没有。本脚本把规则变成机械校验。

检查项（全部 error 档，退出码 1）：

  - A1 编号连续：`NNNN` 四位递增、不跳号、不复用
  - A2 文件名格式：`NNNN-<kebab-case>.md`
  - A3 必填字段：`Status` / `Date` / `Deciders` 均在头部
  - A4 指针有效：`Supersedes` 与完全取代的 `Status` 指向的 ADR 必须存在
  - A5 反向指针对称：被取代的旧 ADR，其 `Status` 必须提到取代者的编号
  - A6 索引覆盖一致：README 索引表覆盖全部 ADR，且 `Status` 列与文件头部一致
  - R2 调研引用不落草稿区：引用的调研文档不得位于 `docs/tmp/`（见 README「证据与引用」）

A 系列管头部与索引的自洽；R 系列管**证据链**（规则见 README「证据与引用」）。R 系列
只覆盖可机械判定的那条 —— R1（证据三选一）与 R3（偏离须记明）靠语义判断，留在规范里。

用法：
    python -m src.cli.check_adr
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ADR_DIR = _PROJECT_ROOT / "docs" / "adr"
_INDEX_FILE = _ADR_DIR / "README.md"

# 头部字段行：`- **Status**：...`；中英文冒号都容忍
_FIELD_RE = re.compile(
    r"^- \*\*(Status|Date|Deciders|Supersedes|Superseded by|关系)\*\*[：:]\s*(.*)$"
)
# 文件名：NNNN-<kebab>.md
_FILENAME_RE = re.compile(r"^(\d{4})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
# 文中任意位置出现的 ADR 编号引用（仅用于解析取代字段的指向）
_ADR_REF_RE = re.compile(r"ADR-(\d{4})")
# 索引表数据行：`| [0003](file.md) | 一句话 | Accepted | 取代关系 |`
_INDEX_ROW_RE = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|")

_REQUIRED_FIELDS = ("Status", "Date", "Deciders")

# R2：草稿区路径（`docs/tmp/` 下的文件随时可被清理，证据链不得挂在那里）
_DRAFT_AREA_RE = re.compile(r"docs/tmp/[\w.-]+(?:/[\w.-]+)*")

# R2 历史豁免：规则落地（2026-10-13）前既存的草稿区引用。这些 ADR 的正文引用写于
# 规则之前，且正文不可变（均已落地代码，不适用「落地前原地修订」例外）——迁移调研
# 文件也改不掉正文里的旧路径，故只能豁免，不能追溯。豁免是**封闭集合**：不新增条目，
# 新 ADR 引草稿区一律 error。集合内容由 tests/cli/test_adr_consistency.py 的
# `test_legacy_exempt_set_is_exactly_real_offenders` 锁死（多一条遮蔽新违规、少一条
# 让存量踩红线，两个方向都报错）。
_LEGACY_DRAFT_CITATION_EXEMPT = frozenset(
    {"0002", "0004", "0005", "0006", "0009", "0011"}
)


@dataclass
class AdrFinding:
    """一条 ADR 校验结果。

    Attributes:
        adr: 涉及的 ADR 编号（四位字符串），索引级问题为 "index"
        line: 问题所在行号
        message: 人类可读的说明
    """

    adr: str
    line: int
    message: str


@dataclass
class Adr:
    """一条 ADR 的头部解析结果。

    Attributes:
        number: 四位编号字符串，如 "0003"
        path: 文件路径
        fields: 头部字段名 → 原文值（不含前缀）
        field_lines: 头部字段名 → 行号
    """

    number: str
    path: Path
    fields: dict[str, str]
    field_lines: dict[str, int]


def _parse_header(path: Path) -> tuple[Adr, str]:
    """解析单个 ADR 文件头部字段。

    头部指 H1 标题之后、第一个 `##` 小节之前的字段行（`- **X**：...`）。字段值
    允许换行：空行不影响归属，缩进续行并入当前字段。遇到 `##` 即停止，避免正文
    里的 `- **X**：` 被误当头部。

    Args:
        path: ADR 文件路径

    Returns:
        (Adr, 文件名中的编号字符串)；文件名不合法时编号返回 ""
    """
    number = ""
    match = _FILENAME_RE.match(path.name)
    if match:
        number = match.group(1)
    fields: dict[str, str] = {}
    field_lines: dict[str, int] = {}
    current_key = ""
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if line.startswith("## "):
            break
        fm = _FIELD_RE.match(line)
        if fm:
            current_key = fm.group(1)
            fields[current_key] = fm.group(2).strip()
            field_lines[current_key] = lineno
            continue
        # 续行：字段值可换行，缩进继续属同一字段
        if current_key and line.startswith("  ") and line.strip():
            fields[current_key] += " " + line.strip()
    return Adr(number=number, path=path, fields=fields, field_lines=field_lines), number


def _load_adrs(adr_dir: Path) -> list[Adr]:
    """加载指定目录下全部 ADR（跳过 README）。

    Args:
        adr_dir: ADR 目录

    Returns:
        按文件名升序排列的 Adr 列表
    """
    adrs: list[Adr] = []
    for path in sorted(adr_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        adr, _ = _parse_header(path)
        adrs.append(adr)
    return adrs


def _status_head(raw_status: str) -> str:
    """取 Status 的状态词（去掉反向指针等括号补充说明）。

    Args:
        raw_status: Status 字段原文，如 `Accepted（**决策 3 已被 ADR-0009 取代**，其余仍有效）`

    Returns:
        状态词，如 `Accepted`；`Superseded by 0009` 原样返回
    """
    head = raw_status.split("（", 1)[0].split("(", 1)[0].strip()
    return head


def _check_filenames_and_numbering(adrs: list[Adr]) -> list[AdrFinding]:
    """A1/A2：文件名格式合法、编号四位递增不跳号。

    Args:
        adrs: 全部 ADR

    Returns:
        error 档列表
    """
    findings: list[AdrFinding] = []
    numbers: list[str] = []
    for adr in adrs:
        if not adr.number:
            findings.append(
                AdrFinding(
                    adr=adr.path.stem,
                    line=0,
                    message=f"文件名 {adr.path.name} 不符合 `NNNN-<kebab-case>.md`",
                )
            )
            continue
        numbers.append(adr.number)
    expected = [f"{i:04d}" for i in range(1, len(numbers) + 1)]
    if numbers != expected:
        findings.append(
            AdrFinding(
                adr="index",
                line=0,
                message=f"编号不连续或跳号：实际 {numbers}，应为 {expected}",
            )
        )
    return findings


def _check_required_fields(adrs: list[Adr]) -> list[AdrFinding]:
    """A3：Status / Date / Deciders 必须存在于头部。

    Args:
        adrs: 全部 ADR

    Returns:
        error 档列表
    """
    findings: list[AdrFinding] = []
    for adr in adrs:
        for field in _REQUIRED_FIELDS:
            if field not in adr.fields:
                findings.append(
                    AdrFinding(
                        adr=adr.number,
                        line=0,
                        message=f"头部缺少必填字段 `{field}`（见 README「取代关系与头部字段」）",
                    )
                )
    return findings


def _supersede_targets(raw: str) -> list[str]:
    """从 Supersedes 字段值提取被取代的 ADR 编号。

    Args:
        raw: 字段原文

    Returns:
        去重后的四位编号列表（保持出现顺序）；「无 ADR」等无指向时返回空列表
    """
    targets: list[str] = []
    for num in _ADR_REF_RE.findall(raw or ""):
        if num not in targets:
            targets.append(num)
    return targets


def _full_supersede_target(raw_status: str) -> str:
    """从 Status 提取完全取代的反向指针编号（`Superseded by NNNN`）。

    Args:
        raw_status: Status 字段原文

    Returns:
        四位编号；非完全取代形态时返回 ""
    """
    match = re.match(r"^Superseded by\s+(\d{4})", raw_status.strip())
    if not match:
        return ""
    return match.group(1)


def _check_pointer_symmetry(
    adrs: list[Adr], by_number: dict[str, Adr]
) -> list[AdrFinding]:
    """A4/A5：指针指向存在、且被取代方必须带反向指针。

    A4：`Supersedes` 与完全取代的 `Status`（`Superseded by NNNN`）指向的编号必须
        存在（防指向已删除的 ADR）。
    A5：新 ADR 声明 `Supersedes` A，则 A 的 `Status` 必须提到新 ADR 的编号 ——
        这是本次要根治的失效方式（缺反向指针会让后人把已被推翻的那条当成仍成立）。
        例外：A 已被**第三条**完全取代（`Status` 为 `Superseded by X` 且 X ≠ 本 ADR）
        时不再要求局部反向指针 —— 整条决策已成历史，局部取代关系记录在索引备注。

    Args:
        adrs: 全部 ADR
        by_number: 编号 → Adr 索引

    Returns:
        error 档列表
    """
    findings: list[AdrFinding] = []
    for adr in adrs:
        declared = _supersede_targets(adr.fields.get("Supersedes", ""))
        lineno = adr.field_lines.get("Supersedes", 0)
        full = _full_supersede_target(adr.fields.get("Status", ""))
        if full:
            declared.append(full)
            lineno = adr.field_lines.get("Status", lineno)
        for target in declared:
            if target not in by_number:
                findings.append(
                    AdrFinding(
                        adr=adr.number,
                        line=lineno,
                        message=f"取代指针指向 ADR-{target}，但该 ADR 不存在",
                    )
                )
    for adr in adrs:
        for target in _supersede_targets(adr.fields.get("Supersedes", "")):
            old = by_number.get(target)
            if old is None:
                continue
            old_status = old.fields.get("Status", "")
            full_old = _full_supersede_target(old_status)
            if full_old and full_old != adr.number:
                # 旧 ADR 已被第三条完全取代：局部取代成为历史，索引备注承载该关系。
                continue
            if f"ADR-{adr.number}" not in old_status and adr.number not in old_status:
                findings.append(
                    AdrFinding(
                        adr=adr.number,
                        line=adr.field_lines.get("Supersedes", 0),
                        message=(
                            f"本 ADR 声明取代 ADR-{target}，但 ADR-{target} 的 `Status` "
                            f"没有反向指针（应写明「已被 ADR-{adr.number} 取代」）"
                        ),
                    )
                )
    return findings


def _parse_index_statuses(index_file: Path) -> tuple[list[str], dict[str, str]]:
    """解析 README 索引表的编号顺序与 Status 列。

    数据行形如 `| [0003](0003-xxx.md) | 一句话 | Accepted | 取代关系 |`，
    按 `|` 切分后第 1 段是链接、第 3 段是 Status。

    Args:
        index_file: 索引所在的 README 路径

    Returns:
        (索引中出现的编号列表, 编号 → Status 列原文)
    """
    order: list[str] = []
    statuses: dict[str, str] = {}
    if not index_file.exists():
        return order, statuses
    for line in index_file.read_text(encoding="utf-8").splitlines():
        row = _INDEX_ROW_RE.match(line)
        if not row:
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        order.append(row.group(1))
        statuses[row.group(1)] = parts[3].strip()
    return order, statuses


def _check_index(adrs: list[Adr], index_file: Path) -> list[AdrFinding]:
    """A6：README 索引表覆盖全部 ADR，且 Status 列与文件头部一致。

    Args:
        adrs: 全部 ADR
        index_file: 索引所在的 README 路径

    Returns:
        error 档列表
    """
    findings: list[AdrFinding] = []
    order, statuses = _parse_index_statuses(index_file)
    if not order:
        if adrs:
            findings.append(
                AdrFinding(
                    adr="index",
                    line=0,
                    message="README 索引表为空或解析失败（应列出全部 ADR）",
                )
            )
        return findings
    for adr in adrs:
        if adr.number not in statuses:
            findings.append(
                AdrFinding(
                    adr=adr.number,
                    line=0,
                    message=f"ADR-{adr.number} 未登记进 README 索引表",
                )
            )
            continue
        file_head = _status_head(adr.fields.get("Status", ""))
        index_head = _status_head(statuses[adr.number])
        if file_head != index_head:
            findings.append(
                AdrFinding(
                    adr=adr.number,
                    line=0,
                    message=(
                        f"README 索引 Status 列（{index_head}）与文件头部"
                        f"（{file_head}）不一致"
                    ),
                )
            )
    if order != sorted(order):
        findings.append(
            AdrFinding(adr="index", line=0, message=f"索引表编号未升序：{order}")
        )
    return findings


def _check_research_refs(
    adrs: list[Adr], exempt: frozenset[str] | None = None
) -> list[AdrFinding]:
    """R2：ADR 引用的证据材料不得位于草稿区（`docs/tmp/`）。

    拦的是**任何** `docs/tmp/` 路径引用，不限于标题里写的"调研" —— 实测记录、探针
    输出、临时清单同样属证据，同样不该挂在会被随时清理的地方。

    判据是「证据链不能挂在会被随时清理的地方」：ADR 不可变，一旦它引用的材料被从
    草稿区清掉，这条决策就永久失去可复核的依据。

    扫的是**全文**而非头部 —— 引用通常出现在「背景与问题」「候选方案」「理由」等
    正文小节里，`_parse_header` 只解析到第一个 `##` 就停，读不到。

    Args:
        adrs: 全部 ADR
        exempt: 豁免编号集合；None 时用 `_LEGACY_DRAFT_CITATION_EXEMPT`

    Returns:
        error 档列表（每行最多报一次，避免同一行多命中刷屏）
    """
    exempt_numbers = exempt
    if exempt_numbers is None:
        exempt_numbers = _LEGACY_DRAFT_CITATION_EXEMPT
    findings: list[AdrFinding] = []
    for adr in adrs:
        if adr.number in exempt_numbers:
            continue
        for lineno, line in enumerate(
            adr.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            hits = _DRAFT_AREA_RE.findall(line)
            if not hits:
                continue
            findings.append(
                AdrFinding(
                    adr=adr.number,
                    line=lineno,
                    message=(
                        f"引用的证据材料位于草稿区（`{hits[0]}`）——ADR 不可变，"
                        "证据链不得挂在会被随时清理的目录上；请把调研落进 "
                        "`docs/research/` 再引用（见 README「证据与引用」R2）"
                    ),
                )
            )
    return findings


def main() -> None:
    """CLI 入口 — 执行全部 ADR 头部检查并按 error 数决定退出码。"""
    adrs = _load_adrs(_ADR_DIR)
    by_number = {a.number: a for a in adrs if a.number}

    findings: list[AdrFinding] = []
    findings.extend(_check_filenames_and_numbering(adrs))
    findings.extend(_check_required_fields(adrs))
    findings.extend(_check_pointer_symmetry(adrs, by_number))
    findings.extend(_check_index(adrs, _INDEX_FILE))
    findings.extend(_check_research_refs(adrs))

    for f in findings:
        location = f"ADR-{f.adr}" if f.adr != "index" else "docs/adr/README.md"
        print(f"[error] {location}:{f.line} {f.message}")
    print(f"\n{len(adrs)} 条 ADR，{len(findings)} error")
    if findings:
        sys.exit(1)


if __name__ == "__main__":
    main()
