# GPU 私有化推理后端实测（T4 / A10 × ollama / vLLM）

> **成文**：2026-10-16　**测量提供**：2026-10-09
> **性质**：外部实测记录。该组压测**不在本项目链路内**（此前记为在 Dify 上接私有化模型所做），
> 本项目是 LangChain / OpenAI 兼容直连云模型（百炼 / DeepSeek）——**原数字不能当作本项目的性能预期**。
> **冻结语义**：以下数字是「那次测量」的记录，不随代码或结论演进而回写。来源为设备 `usage` payload × 3 + 用户正述口径。

## 结论摘要

三组均为**单流（单请求）**，模型同为 `qwen3.5:9b`，量化 4bit（第三组为 AWQ）：

| # | 硬件 + 引擎 | prompt_tokens | TTFT(s) | completion_tokens | time_to_generate(s) | latency(s) | 解码(tok/s) |
|---|---|---|---|---|---|---|---|
| 1 | T4 + ollama（4bit） | **1336** | 7.12 | 169 | 6.033 | 13.152 | **28.0** |
| 2 | A10 + ollama（4bit） | 1379 | 3.636 | 191 | 3.413 | 7.049 | **56.0** |
| 3 | A10 + vLLM（AWQ） | 3786 | 0.515 | 476 | 3.821 | 4.336 | **124.6** |

- 解码速度 = `completion_tokens ÷ time_to_generate`（**按 payload 计算**，与正文口述不一致见下）。
- `latency = time_to_first_token + time_to_generate`，三组均自洽。

要点：

1. **T4 → A10**（同引擎 ollama、prompt 量级接近 1336 vs 1379）≈ **2× 全面加速**（TTFT 1.96×、解码 2.00×）——纯硬件差。
2. **A10 同卡 ollama → vLLM** 解码 **2.23×**，roofline 验证差距来自**量化格式 + kernel**，非引擎本身。
3. vLLM 的 **0.515s TTFT 不可信**（见「反面与风险」）。
4. **三组不可横向对比**：prompt 长度 1336/1379/3786 差 2.7×，且均为单流。

## 详述

### 1. T4 + ollama + qwen3.5:9b 4bit

- 正文口径：prompt_token 1379、多次对话首 token ≈ **7s**、生成速度 ≈ **35.7 tok/s**
- payload 实值：prompt_tokens **1336**、TTFT **7.12s**、completion 169、generate 6.033s、latency 13.152s ⇒ 解码 **28.0 tok/s**

> ⚠️ 正文与 payload 两处不符（见「反面与风险」）。

### 2. A10 + ollama + qwen3.5:9b 4bit

- 正文口径：prompt_token 1379、多次对话首 token ≈ **3.5–4s**、生成速度 ≈ **56 tok/s**
- payload 实值：prompt_tokens 1379、TTFT **3.636s**、completion 191、generate 3.413s、latency 7.049s ⇒ 解码 **56.0 tok/s**（自洽）

### 3. A10 + vLLM + qwen3.5:9b-AWQ

- 正文口径：prompt_token 3786、多次对话首 token **0.5s**、生成速度 ≈ **125 tok/s**
- payload 实值：prompt_tokens 3786、TTFT **0.515s**、completion 476、generate 3.821s、latency 4.336s ⇒ 解码 **124.6 tok/s**（自洽）

### 归因（可迁移的分析）

1. **T4 → A10**（同引擎 ollama、同 prompt 量级）：TTFT 1.96×、解码 2.00× ⇒ 纯硬件差
   （T4 320GB/s / 65TFLOPS / Turing vs A10 600GB/s / 125TFLOPS / Ampere）。
2. **A10 ollama → A10 vLLM** 解码 2.23×，roofline 验证：ollama(GGUF) 仅 ~50% 带宽、
   vLLM(AWQ) ~94% ⇒ 差距来自**量化格式 + kernel**，非引擎本身。
3. **vLLM TTFT 0.515s 不可信**：3786 tok / 0.515s 需 ~132 TFLOPS ≈ A10 满算力（106%），
   **几乎必然 prefix cache 命中**；冷 prefill 未测到。
4. 三组 prompt 长度不一致（1336/1379/3786）×2.7，**横比不成立**；且均为单流，
   vLLM 的连续批处理优势未体现。

## 原始 payload

### T4 + ollama

```json
{
  "completion_price": "0",
  "completion_price_unit": "0",
  "completion_tokens": 169,
  "completion_unit_price": "0",
  "currency": "USD",
  "latency": 13.152,
  "prompt_price": "0",
  "prompt_price_unit": "0",
  "prompt_tokens": 1336,
  "prompt_unit_price": "0",
  "time_to_first_token": 7.12,
  "time_to_generate": 6.033,
  "total_price": "0",
  "total_tokens": 1505
}
```

### A10 + ollama

```json
{
  "completion_price": "0",
  "completion_price_unit": "0",
  "completion_tokens": 191,
  "completion_unit_price": "0",
  "currency": "USD",
  "latency": 7.049,
  "prompt_price": "0",
  "prompt_price_unit": "0",
  "prompt_tokens": 1379,
  "prompt_unit_price": "0",
  "time_to_first_token": 3.636,
  "time_to_generate": 3.413,
  "total_price": "0",
  "total_tokens": 1570
}
```

### A10 + vLLM

```json
{
  "completion_price": "0",
  "completion_price_unit": "0",
  "completion_tokens": 476,
  "completion_unit_price": "0",
  "currency": "USD",
  "latency": 4.336,
  "prompt_price": "0",
  "prompt_price_unit": "0",
  "prompt_tokens": 3786,
  "prompt_unit_price": "0",
  "time_to_first_token": 0.515,
  "time_to_generate": 3.821,
  "total_price": "0",
  "total_tokens": 4262
}
```

## 反面与风险（引用前必读）

- **正文口径与 payload 有两处矛盾（一律以 payload 为准）**：
  - T4 的 `prompt_tokens`：正文写 1379，payload 实为 **1336**
  - T4 的解码：正文写 35.7 tok/s，payload 算得 **28.0**（169 ÷ 6.033）
  - 其余两组正文与 payload 一致。
- **vLLM 0.515s TTFT 不可信**（近乎必然 prefix cache），不可作为冷启动首 token 指标引用。
- **三组横比不成立**：prompt 长度差 2.7×、均为单流，vLLM 批处理优势未体现。
- **不可迁移为本项目性能预期**：本项目不跑本地卡推理；原数字可能含上层编排开销（非本项目链路），
  且量化口径、KV dtype、引擎版本均未固定。

## 待确认项（待复测 / 待补）

1. vLLM **冷上下文** TTFT（换 prompt / 清 cache 后重测）
2. **对齐 prompt 长度**后再比 TTFT 与解码
3. **并发**（1 / 5 / 10）下的吞吐与 TTFT
4. ollama 的 flash-attn / KV dtype / `keep_alive`
5. **量化口径**确认（GGUF Q4_K_M vs AWQ）

## 来源

- 用户 2026-10-09 提供：设备 `usage` payload × 3（上文原样收录）+ 正述口径。
- 无 URL（私有部署内部测量）。
