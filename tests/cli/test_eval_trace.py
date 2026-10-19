from types import SimpleNamespace

from src.cli.eval_trace import parse_trace


def _obs(**kwargs):
    base = {
        "type": "SPAN",
        "name": "",
        "input": None,
        "output": None,
        "latency": None,
        "model": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _trace(observations):
    return SimpleNamespace(
        id="trace_1",
        session_id="s1",
        timestamp="2026-10-19T00:00:00Z",
        tags=["chat", "kb"],
        metadata={"channel": "web"},
        input={"query": "腾讯2024年营收"},
        output="腾讯2024年营收为6603亿元。",
        observations=observations,
    )


def test_parse_trace_splits_contexts_and_dedupes():
    content = (
        "[1] 来源: a.pdf (第1页, 内部文档)\n内容: 腾讯2024年营收6603亿元。\n\n"
        "[2] 来源: b.pdf (第2页, 内部文档)\n内容: 东软2025Q1营收18.5亿元。\n\n"
        "[3] 来源: a.pdf (第1页, 内部文档)\n内容: 腾讯2024年营收6603亿元。"
    )
    trace = _trace(
        [
            _obs(
                type="SPAN",
                name="retrieve_kb",
                input={"query": "腾讯2024年营收", "top_k": 5},
                output={"name": "retrieve_kb", "content": content},
                latency=0.826,
            ),
            _obs(
                type="GENERATION",
                name="agent_turn",
                input=[{"role": "user"}],
                output={"tool_calls": []},
                latency=1.528,
                model="qwen3.7-flash",
            ),
        ]
    )

    parsed = parse_trace(trace)

    assert parsed["query"] == "腾讯2024年营收"
    assert parsed["answer"].startswith("腾讯2024年营收为")
    assert len(parsed["contexts"]) == 2
    assert parsed["contexts"][0] == {
        "source": "a.pdf",
        "page": 1,
        "text": "腾讯2024年营收6603亿元。",
    }
    assert parsed["tool_calls"] == [
        {
            "name": "retrieve_kb",
            "args": {"query": "腾讯2024年营收", "top_k": 5},
            "result_chars": len(content),
            "latency_ms": 826,
        }
    ]
    assert parsed["generations"][0]["model"] == "qwen3.7-flash"
    assert parsed["generations"][0]["latency_ms"] == 1528
