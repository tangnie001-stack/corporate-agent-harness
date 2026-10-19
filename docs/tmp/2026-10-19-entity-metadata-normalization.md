# 实体元数据盘点与规范化方案（草稿）

> 状态：2026-10-19 盘点完成，方案待评审；未改代码、未写库。
> 现象来源：`eval_trace` 回放 trace `trace_b211ac09-7d4e-4b48-948c-bb267d5cb0b9`
> （query 问腾讯，`retrieve_kb` 返回 4 条东软 + 1 条腾讯）。

## 1. 盘点结果

当前实例共 6 个知识库、337 个 chunk：

| KB | 名称 | chunk 数 | 实体分布 |
|---|---|---:|---|
| `4a1dcb8…` | `test` | 161 | 灿坤 96（`200512` / `2019年年度`）；`tencent` 38（无代码/期间）；东软 27（`600718`，2024Q3 14 + 2025Q1 13） |
| `ea84fb7…` | `p2-equiv-ea84fb72` | 121 | 灿坤 85（`000512` / `2019年`）；腾讯控股有限公司 23（`00700, 80700` / `二零二四年年度及第四季`）；`neusoft` 13（`600718` / 2025Q1） |
| `b9e74e8…` | `p2-equiv-b9e74e82` | 51 | 腾讯控股有限公司 38（`00700` / `二零二四年年度`）；东软 13 |
| `095efa1…` | `p2-equiv-095efa16` | 2 | 腾讯控股 2（一条 `sec_code` 空串，期间 `2024年全年`） |
| `2f85ec3…` | `p2-equiv-2f85ec3c` | 1 | company / sec_code / period 全空 |
| `9498856…` | `p2-equiv-9498856e` | 1 | company / sec_code 空，期间 `2024年第一季度` |

### 发现的问题

1. **同一公司多种写法**：`tencent` / `腾讯控股` / `腾讯控股有限公司`；`neusoft` / `东软集团股份有限公司`。
2. **证券代码不统一**：腾讯 `None` / `00700` / `"00700, 80700"`；灿坤 `200512` / `000512`；东软 `600718` 一致。
3. **报告期格式不统一**：`None` / `2024年全年` / `二零二四年年度` / `二零二四年年度及第四季` / `2024年第三季度` / `2025年第一季度` / `2019年年度` / `2019年`。
4. **存在空实体**：2 个 chunk 的 company 为空，若干 chunk 的 sec_code / period 为空。
5. **多个 KB 是混装的**：`test`、`p2-equiv-ea84fb72`、`p2-equiv-b9e74e82` 都同时含 2–3 家公司。
6. **检索完全不用实体**：`chunk_repo.search_dense` / `search_lexical` 的 WHERE 只有 `kb_id`；`company` / `sec_code` / `report_period` 只用于 prompt 实体锚点。

## 2. 规范化目标

每个 chunk 的 metadata 至少包含：

| 键 | 示例 | 说明 |
|---|---|---|
| `company_id` | `HK:00700` | **稳定 canonical id**，检索过滤与去重用 |
| `company` | `腾讯控股有限公司` | canonical 展示名 |
| `company_raw` | `tencent` | 原始抽取值，保留可追溯 |
| `sec_code` | `00700` | 规范化后的主代码（多代码时用 `tickers`） |
| `tickers` | `["00700","80700"]` | 可选，多市场/多柜台 |
| `report_period` | `2024FY` | `YYYYQn` / `YYYYFY`，用于过滤与展示 |
| `period_raw` | `二零二四年年度` | 原始抽取值 |
| `entity_source` | `metadata` / `filename` / `manual` / `unresolved` | 置信来源；`unresolved` 进人工队列 |

`company_id` 用「市场前缀 + 代码」：`CN:600718`、`CN:200512`、`HK:00700`。没有代码的公司用 `slug:<拼音/英文>` 兜底，并标记 `entity_source=manual`。

## 3. 别名表：手写一次，不是逐 chunk 手改

做法是维护一份**公司主数据 + 别名表**，由确定性 resolver 批量转换：

```yaml
# config/entities/companies.yaml（示意，实际路径待定）
companies:
  - id: HK:00700
    canonical: 腾讯控股有限公司
    aliases: [腾讯, 腾讯控股, Tencent, tencent]
    tickers: ["00700", "80700"]
  - id: CN:600718
    canonical: 东软集团股份有限公司
    aliases: [东软, 东软集团, Neusoft, neusoft]
    tickers: ["600718"]
  - id: CN:200512
    canonical: 厦门灿坤实业股份有限公司
    aliases: [灿坤, 闽灿坤, 厦灿坤, 灿坤实业]
    tickers: ["200512", "000512"]
```

- 当前公司数量很少（3 家），**手写 + 人工复核**成本极低。
- 初始候选可从文件名、文档标题、`document.meta_info.entities` 自动聚合，生成「待确认别名」清单；LLM 只用于**离线提议**，最终映射人工确认后入表。
- 运行时/入库时只做确定性匹配（大小写、全半角、空白/标点归一 + 别名精确匹配 + ticker 匹配），不调 LLM。
- 未命中的公司：`company_id=null`、`company_raw` 保留、`entity_source=unresolved`，记 warning 并进人工队列；不要猜。

## 4. 落地步骤

1. **别名表 + resolver**（新增模块，建议 `src/infra/search/entity_normalizer.py`）
   - `normalize_text(value)`：NFKC、小写、去空白/常见标点。
   - `resolve_company(raw_company, raw_sec_code) -> CanonicalCompany | None`：ticker 优先，其次别名；返回 `company_id` / `company` / `tickers`。
   - `normalize_period(raw) -> str | None`：中文数字/季度/年度 → `YYYYQn` / `YYYYFY`；无法解析返回 None。
2. **入库时规范化**（`src/services/document_service.py::_inject_document_entities`）
   - 在 `DocumentEntityExtractor.extract()` 之后、写入 chunk metadata 之前调用 resolver。
   - 同时写 canonical 与 raw 字段；`document.meta_info.entities` 也写 canonical。
   - 解析失败不阻断入库，只记 warning + `unresolved`。
3. **存量回填 CLI**（`src/cli/normalize_entity_metadata.py`）
   - `--dry-run`：扫描全部 chunk，输出「原始值 → canonical / unresolved」统计。
   - `--apply`：按 `chunk.id` 批量更新 JSONB metadata，不重建 embedding、不改正文。
   - 先跑 dry-run，人工确认映射后再 apply。
4. **检索侧使用**
   - 从 query / 历史解析 company + period，resolver 成 canonical id。
   - `search_dense` / `search_lexical` 增加可选 `company_id` / `report_period` 过滤（SQL JSONB `->>`），或在 RRF 后加权。
   - 无实体时不过滤，保持现状；follow-up 从历史继承实体。
5. **验收**
   - 数据：所有已知文档 `company_id` 覆盖 100%；`unresolved` 列表为空或已人工确认。
   - 行为：问「腾讯」top-k 不含东软/灿坤；问「东软」top-k 不含腾讯；问「2024年」+腾讯时期间过滤正确。
   - 回归：现有 `tests/rag`、`tests/services` 全绿；新增解析/回填/过滤测试。

## 5. 风险与待定

- **别名误合并**：不同公司同名/简称冲突；用 `company_id` + ticker 双键，人工复核。
- **期间语义**：`二零二四年年度及第四季` 是「FY + Q4」；需决定归一到 `2024FY` 还是保留 `2024Q4` 标签。建议 `report_period=2024FY`，另存 `period_scope`。
- **多代码**：腾讯 `00700` / `80700` 是不同柜台；`company_id` 用主代码，`tickers` 保留全部。
- **KB 治理**：最省事的长期方案仍是一 KB 一公司/一期间；实体过滤是混装 KB 的兜底，不应替代数据治理。
- **回填范围**：先只处理当前 6 个 KB；生产如有更多 KB，先跑 dry-run 评估规模。

## 6. 建议的 change 拆分

1. `entity-metadata-normalization`：别名表 + resolver + 入库规范化 + 回填 CLI + 测试。
2. `entity-aware-retrieval`：query/history 实体解析 + 检索过滤/加权 + 回归测试。
3. 数据治理（可选）：把混装 KB 拆成单公司 KB。
