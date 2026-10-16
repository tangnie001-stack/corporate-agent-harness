"""检索层的失败标记异常。

归属：change `timeout-and-degradation-hardening` 的 P2（降级动作矩阵 / D6/D7）。
`search()` 两路皆失败时抛本异常，供上层归类为 `vector_store_failed`
（取值集合见 `specs/degradation-policy/spec.md`），按"知识库暂不可用"告知用户。

独立模块的理由：判定原语 `src/infra/retry_policy.py` 需要引用它，而
`retrieval.py` 已经 import `retry_policy` —— 放在 `retrieval.py` 里会成环。
"""


class RetrievalUnavailableError(RuntimeError):
    """混合检索两路皆失败 —— 向量库不可用（降级交上层处置）。"""
