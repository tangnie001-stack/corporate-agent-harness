#!/usr/bin/env python3
"""OpenSpec change 的「三处一致性」机检（草稿工具，未入闸门）。

背景：`docs/openspec/changes/timeout-and-degradation-hardening` 连过三轮架构评审，
共 23 条发现里有约 5 条属同一类 —— **修正只改了一半 / 只改了一处**
（design 改了、spec 没改；spec 改了、tasks 没改）。这类缺陷靠人眼复核会重复漏，
但绝大多数是**可机检的文本矛盾**。本脚本把那几条检查固化下来。

用法：
    python docs/tmp/check-change-consistency.py <change 目录>

退出码：0 = 无命中；1 = 有命中（供人工 triage，不是硬闸门）。

检查项：
    C1 决策编号引用完整（`D\\d+` 有引用必有定义；tasks 头部的 "D1–Dxx" 区间与实际最大编号一致）
    C2 需求池 / ADR 编号引用存在（`[A-H]-\\d\\d` 能在 requirements_pool.md 找到）
    C3 降级 reason 取值集合：design 里的 reason 取值必须都出现在 spec 的取值表中
    C4 排除项一致：spec 里声明"排除 X"的符号，不得出现在"按**角色**（…）"的枚举里
    C5 数值一致：spec / tasks 里出现的 `<n>s` 常量必须在 design.md 出现过（design 是取值唯一真源）
    C6 否定-肯定对：同一符号不得一处写"复用 X"、另一处写"不复用 X"

调参与验证记录（2026-10-16）
----------------------------
首跑 21 条命中，**逐条核完全是误报**。四类误报的成因与修法：

1. `DECISION_DEF` 原锚 `^### D9[:：]`，而 D9 的标题是 `### D9（2026-10-16 修订）：…`
   ⇒ 被当成"未定义"。已放宽为 `^### D<n>\b`。
2. C3 原把任何带下划线的反引号标识符都当 reason ⇒ `log_event_specs` 也会中。
   已按**后缀 + 依赖前缀白名单**收窄，并显式排除配置键（`request_timeout` 等）。
3. C4 原把任何含"排除/不在本变更"的行都当**角色**排除 ⇒ design 里
   「`embedding` **复用**不纳入本变更」被误读成"角色 embedding 被排除"。已要求该行同时含"角色"。
4. C6 的 `(不)?复用` 漏了 `不得复用` / `SHALL NOT 复用`（只有"不复用"算否定）⇒ 同一句被
   同时当成肯定与否定。已把否定前缀扩为 `不得|不再|不|SHALL NOT|NOT`，并先去掉 `**` 加粗。

**灵敏度已实测**：往副本注入 7 处人造违规（C1 引用不存在编号 / C1 头部区间不符 / C2 引用不存在
需求池条目 / C3 未登记 reason / C4 已排除角色被塞回枚举 / C5 design 未出现的秒数 / C6 复用矛盾），
**7/7 命中**；真实目录 **0 命中**。

归宿：本文件是**草稿工具**（`docs/tmp/` 是草稿区）。若要长期用，应作为**独立 change** 提升为
`src/cli/check_change.py` 并接进 pre-commit —— 直接落 `src/cli/` 会形成"未进规格的闸门"，
且跑通 `documentation-anti-rot` 的规格与测试是独立工作量。

已知盲区
--------
只读**单个 change 目录** ⇒ 抓不到**跨 change 的口径矛盾**。真实一例（2026-10-16 拉 dev-wsl 时
撞到）：生产侧写「429 可重试」，评测链侧写「429 不重试」—— 两条**各自成立**（评测链有 ragas
框架层重试 ≤10 次，生产没有），但**都必须写明适用范围**，否则被读成矛盾。⇒ 跨 change 对账
需另建检查：把待比对的两个 change 目录一起传进来、或先 `git merge` 拉平后人工核口径边界。
（注：在途的 `deepeval-bridge-eval` 也有一个对账脚本，但查的是"**主规格 ↔ delta 的场景名**"，
与本文件查的"**change 内跨工件**"互补、不重叠。）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DECISION_REF = re.compile(r"\bD(\d{1,2})\b")
# 标题允许 `### D9：` / `### D9（2026-10-16 修订）：` 等形态 ⇒ 只锚 "### D<数字>" + 词边界
DECISION_DEF = re.compile(r"^### D(\d{1,2})\b", re.MULTILINE)
RANGE_HINT = re.compile(r"D1\s*[–\-]\s*D(\d{1,2})")
POOL_ID = re.compile(r"\b([A-H])-(\d{2})\b")
SECONDS = re.compile(r"\b(\d{1,3})s\b")
BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
# reason 取值的形态：至少一个下划线的小写标识符
REASON_SHAPED = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")
EXCLUDE_SENT = re.compile(r"排除|不在本变更|不在本次范围")
ROLE_ENUM = re.compile(r"按\*{0,2}角色\*{0,2}（([^）]*)）")
REUSE = re.compile(
    r"(不得|不再|不|SHALL NOT\s+|NOT\s+)?复用\s*`([A-Za-z_][A-Za-z0-9_]*)`",
    re.IGNORECASE,
)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    change = Path(sys.argv[1]).resolve()
    if not change.is_dir():
        print(f"不是目录：{change}")
        return 2
    root = Path(__file__).resolve().parents[2]

    files = {
        "proposal": read(change / "proposal.md"),
        "design": read(change / "design.md"),
        "tasks": read(change / "tasks.md"),
    }
    for spec in sorted((change / "specs").glob("*/spec.md")):
        files[f"specs/{spec.parent.name}"] = read(spec)
    pool = read(root / "docs" / "agents" / "requirements_pool.md")

    hits: list[str] = []

    # ── C1 决策编号引用完整 ──
    defined = {int(m) for m in DECISION_DEF.findall(files["design"])}
    max_defined = max(defined) if defined else 0
    for name, text in files.items():
        for ref in DECISION_REF.findall(text):
            if int(ref) not in defined:
                hits.append(
                    f"[C1] {name}: 引用了未定义的决策 D{ref}（design 只定义到 D{max_defined}）"
                )
    if defined and not set(range(1, max_defined + 1)) <= defined:
        missing = sorted(set(range(1, max_defined + 1)) - defined)
        hits.append(f"[C1] design: 决策编号不连续，缺 {missing}")
    for name in ("proposal", "tasks"):
        for m in RANGE_HINT.finditer(files[name]):
            if int(m.group(1)) != max_defined:
                hits.append(
                    f"[C1] {name}: 头部写的决策区间是 D1–D{m.group(1)}，"
                    f"而 design 实际定义到 D{max_defined}"
                )

    # ── C2 需求池 / ADR 编号引用存在 ──
    if pool:
        for name, text in files.items():
            for letter, num in set(POOL_ID.findall(text)):
                if f"| {letter}-{num} " not in pool:
                    hits.append(
                        f"[C2] {name}: 引用了需求池里不存在的条目 {letter}-{num}"
                    )

    # ── C3 reason 取值集合：design / tasks 出现的 reason 都要在 spec 取值表里 ──
    # 只认"看起来像降级原因"的标识符（按下表后缀收窄），否则 `log_event_specs`
    # 这类普通标识符会被误判成 reason。
    REASON_SUFFIXES = (
        "_failed",
        "_timeout",
        "_quota",
        "_auth",
        "_idle",
        "_interrupted",
        "_context",
        "_exhausted",
        "_missing",
    )
    # reason 取值的"姓氏"白名单（真正的降级原因都带依赖前缀）
    REASON_PREFIXES = (
        "rerank_",
        "provider_",
        "llm_",
        "upstream_",
        "generation_",
        "embedding_",
        "vector_",
    )
    # 明确不是 reason 的配置键
    NOT_A_REASON = {
        "request_timeout",
        "proxy_read_timeout",
        "connect_timeout",
        "read_timeout",
        "write_timeout",
        "pool_timeout",
    }
    spec_reasons = set(REASON_SHAPED.findall(files.get("specs/degradation-policy", "")))

    def looks_like_reason(tok: str) -> bool:
        # 排除"配置键"一类同样以 _timeout 结尾但并非降级原因的标识符
        if tok in NOT_A_REASON:
            return False
        if tok.endswith("_timeout") and not tok.startswith(REASON_PREFIXES):
            return False
        return tok.endswith(REASON_SUFFIXES)

    for name in ("design", "tasks", "proposal"):
        for tok in sorted(set(REASON_SHAPED.findall(files[name]))):
            if looks_like_reason(tok) and tok not in spec_reasons:
                hits.append(
                    f"[C3] {name}: reason 取值 `{tok}` 未出现在 "
                    f"specs/degradation-policy 的取值集合里"
                )

    # ── C4 排除项不得出现在"按角色（…）"枚举里 ──
    excluded: set[str] = set()
    for text in files.values():
        for line in text.splitlines():
            # 只认"角色级"排除声明；否则「某功能不纳入本变更」会被误当成角色排除
            if EXCLUDE_SENT.search(line) and "角色" in line:
                excluded |= set(BACKTICKED.findall(line))
    for name, text in files.items():
        for enum in ROLE_ENUM.findall(text):
            for tok in BACKTICKED.findall(enum):
                if tok in excluded:
                    hits.append(
                        f"[C4] {name}: `{tok}` 已被声明排除，却仍出现在“按角色（…）”枚举里"
                    )

    # ── C5 数值：spec / tasks 里的 `<n>s` 必须在 design 出现过 ──
    design_seconds = set(SECONDS.findall(files["design"]))
    for name in ("proposal", "tasks") + tuple(
        k for k in files if k.startswith("specs/")
    ):
        for sec in set(SECONDS.findall(files[name])):
            if sec not in design_seconds:
                hits.append(
                    f"[C5] {name}: 秒数 {sec}s 未在 design.md 的取值表里出现（design 是唯一真源）"
                )

    # ── C6 "复用 X" 与 "不复用 X" 不得并存 ──
    reuse: dict[str, set[str]] = {}
    for name, text in files.items():
        plain = text.replace("**", "")  # 去 markdown 加粗，否则 `不复用 **X**` 会漏判
        for neg, sym in REUSE.findall(plain):
            reuse.setdefault(sym, set()).add("no" if neg else "yes")
    for sym, modes in sorted(reuse.items()):
        if len(modes) > 1:
            hits.append(f"[C6] `{sym}` 同时出现“复用”与“不复用”两种声明，须统一")

    for h in hits:
        print(h)
    print(f"\n{change.name}：{len(hits)} 条命中")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
