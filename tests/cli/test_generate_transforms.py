"""出题 transforms 须为官方全量转换链（含 Themes / Cosine 两件）。

原实现手搓 5 个 transforms，缺 `ThemesExtractor` 与 `CosineSimilarityBuilder`，
导致 `MultiHopAbstractQuerySynthesizer` 取不到 `summary_similarity` 聚类而被
静默丢弃。官方 `default_transforms_for_prechunked()` 含全部 7 步，且按 CHUNK
节点过滤，契合本仓「已分块」的知识图谱。

注：官方链把 `ThemesExtractor` / `CosineSimilarityBuilder` 包在 `Parallel` 组内，
故断言需递归展开（`default_transforms_for_prechunked` 顶层仅
`SummaryExtractor / CustomNodeFilter / Parallel / Parallel`）。
"""


def _collect_names(transforms: list) -> list[str]:
    """递归展开 `Parallel` 组，收集全部 transform 的类名。

    Args:
        transforms: 官方转换链（顶层元素，含 `Parallel` 组）

    Returns:
        展平后的全部 transform 类名列表
    """
    from ragas.testset.transforms.engine import Parallel

    names: list[str] = []
    for t in transforms:
        if isinstance(t, Parallel):
            names.extend(_collect_names(t.transformations))
        else:
            names.append(type(t).__name__)
    return names


def test_build_transforms_contains_themes_and_cosine():
    """非过滤模式下，链中须含 ThemesExtractor 与 CosineSimilarityBuilder。"""
    from src.cli import eval_ragas_generate

    class _Gen:
        llm = object()
        embedding_model = object()

    steps = eval_ragas_generate._build_transforms(_Gen(), use_filter=False)
    assert steps is not None
    names = _collect_names(steps)

    assert "ThemesExtractor" in names
    assert "CosineSimilarityBuilder" in names


def test_build_transforms_returns_none_when_use_filter():
    """use_filter=True 时不设 transforms（交回 ragas 默认），返回 None。"""
    from src.cli import eval_ragas_generate

    assert eval_ragas_generate._build_transforms(object(), use_filter=True) is None
