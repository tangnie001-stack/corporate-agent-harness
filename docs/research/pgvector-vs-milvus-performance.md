# pgvector vs Milvus 性能对比与「差距显现的量级门槛」

> 适用场景：`corporate_rag` —— 企业内网 RAG 助手，**最多千级文档 ≈ 2.5–4 万分块 / 1024 维**（DashScope `text-embedding-v3`）。
> 对比对象：**阿里云 RDS PostgreSQL + pgvector**（上游 pgvector，本项目选型方向）与 **Milvus**（自建 standalone / 阿里云托管版）。
> 检索链路前提：dense 向量检索 + BM25 词法检索 → **RRF 融合（k=60，只用排名不用分数）** → Rerank API → LLM 生成。
>
> **相邻文档（分工）**：
> - [comparison_milvus_vs_chroma.md](./comparison_milvus_vs_chroma.md) —— 原对比对象是 **ChromaDB vs Milvus**，回答「能力与规模维度该用哪个向量库」。
> - [vector-store-ha-options.md](./vector-store-ha-options.md) —— 回答「**高可用（HA）与运维**维度该选哪个部署形态」，结论方向是 RDS PG + pgvector。
> - **本文只回答性能维度**：pgvector 与 Milvus 的性能差异，以及「差距在什么量级/条件下才显现」。不重复上述两文的结论与数据。
>
> 标注约定：`【官】`= 官方文档 / 官方 issue / 官方 benchmark；`【二】`= 二手来源；`【推】`= 基于官方事实的推理。
> 找不到一手依据的一律进 §五「未核实项」，**不编造任何延迟/吞吐数字**。

---

## 一、一句话结论

**在 2.5–4 万 × 1024 维这个量级，pgvector 与 Milvus 之间不存在可测量、且能影响业务的性能差距。**

- 两者纯 ANN 检索都落在**亚毫秒–毫秒级**，差异被 RRF / Rerank API / LLM 生成的耗时（数百毫秒到数秒）完全淹没。【推】
- 差距真正显现的**不是「向量数」这一个变量**，而是三类条件之一：
  1. **带过滤的向量检索**——这是本文唯一被官方文档明确承认的**架构性差异**（pgvector 后过滤、Milvus 预过滤），但它是**可设计规避的**（分区 / 部分索引），且本项目现状（一 KB 一 collection）天然接近隔离；【官】
  2. **高并发吞吐**——Milvus 靠多副本线性扩展 QPS，pgvector 单实例靠核心数，且**单条 HNSW 索引扫描不做并行**（官方 issue 确认，异步/并行读要到 0.9.0）；【官】
  3. **内存地板**——Milvus HNSW 需 `2–3×` 原始向量内存且加载量受「<90% query node 内存」约束，pgvector 官方明确「索引**不必**全量驻留内存」。【官】

- **门槛数字本身多半「未核实」**：pgvector 官方**从未发布任何延迟 / 召回 / QPS 基准数字**；Milvus 官方可用的公开基准只到 **100 万 × 128 维（Milvus 2.2）**。因此 team-lead 给出的「纯 ANN ~100 万+、带过滤 ~10 万+、高并发数百 QPS、批量写入百万级」中，**只有「带过滤」和「高并发」有官方依据支持其方向**，其余几个具体数字**无法从官方来源证实**（见 §五）。

- **对选型的影响**：性能**不能**作为「选 pgvector 还是 Milvus」的理由（两边都不构成瓶颈）；结论方向（pgvector）只能由 HA 与运维支撑——这与 [vector-store-ha-options.md](./vector-store-ha-options.md) 一致。**【推】**

---

## 二、分维度门槛表

> 「门槛量级」= 该维度上两者差距开始变得**可测量 / 影响业务**的条件。**填「未核实」的行表示官方没有数据、且无法从原理给出可靠数字，不做猜测。**

| 维度 | 差距开始显现的条件/量级 | 谁占优 | 依据强度 | 依据 |
|---|---|---|---|---|
| **纯 ANN 延迟** | **未核实**（pgvector 官方无任何延迟数据；Milvus 官方基准最低 100 万 × 128 维） | 大 N 下 Milvus 方向占优，但无同口径数据【推】 | 弱 | §三.1 / §三.2 |
| **带过滤检索** | 由「**过滤选择性 × 数据集大小**」共同决定，**不是单一 N 门槛**；选择性越低、N 越大，pgvector 越吃亏 | **Milvus**（预过滤 + 标量索引） | **强（官方架构性）** | §三.5 |
| **并发 QPS** | 请求量超过单实例核心数可承载时；Milvus 官方称 QPS 随 CPU（8→32）与副本（1→8）**线性增长** | **Milvus**（水平扩展） | 中 | §三.6 |
| **批量写入** | 一次性灌入 4 万无差异；**持续高写入**（流式）才显现 | **Milvus**（WAL + segment 设计） | 中 | §三.8 |
| **索引构建** | 图结构超出 `maintenance_work_mem` 时 pgvector 构建显著变慢（官方 NOTICE 举例提到 10 万 tuple 量级，**非通用阈值**） | 视内存配置而定 | 弱-中 | §三.4 |
| **内存** | 与「差距」无关，是**地板差异**：Milvus HNSW `2–3×` 原始向量 + 加载量上限 | pgvector 更省、更宽容 | 强 | §三.7 |
| **固定开销**（SQL 解析/计划 vs gRPC 往返） | **未核实**（只有 pgvector 侧有官方 issue 中的实测样例） | 无法比较 | 弱 | §五.3 |

---

## 三、逐项详实（附一手来源）

### A. 基线性能事实

#### 1. pgvector 官方有没有发布 10 万 / 100 万 / 1000 万量级的延迟与召回？——**没有。**

- **pgvector README 没有「Performance」数据章节**，只有名为 `Performance` 的**调优建议**章节（`Tuning / Storing / Loading / Indexing / Querying / Vacuuming`），全部是操作方法，**无任何数字**。【官：README「Performance」节】
- 官方 README 唯一给出的「数字」是内存公式与容量上限，而非性能：
  - `vector` 存储：`4 * dimensions + 8` 字节/条；`halfvec`：`2 * dimensions + 8` 字节/条。【官：README「Vector Type」「Halfvec Type」】
  - 5 号 FAQ：「**Do indexes need to fit into memory?** —— **No**, but like other index types, you'll likely see better performance if they do.」【官：README FAQ】
- **CHANGELOG 也只描述相对改进**（「Improved performance of HNSW index scans」等），无绝对数字。【官：CHANGELOG 0.8.0 / 0.7.1 / 0.6.0】

> **结论**：任何声称「pgvector 在 X 万向量下 latency 为 Y ms」的数字，**若来自官方以外，一律是二手**；官方无此数据。这一条是本文最重要的「祛魅」事实。

#### 2. Milvus 官方发布过什么单机基线？

**唯一可直接引用的是 `Milvus 2.2 Benchmark Test Report`（官方文档页，非博客）。**【官：[milvus.io/docs/benchmark.md](https://milvus.io/docs/benchmark.md)】

| 项 | 官方原文设定 |
|---|---|
| 数据集 | 开源 **SIFT（128 维）**，**100 万条** |
| 索引 | **HNSW，`M=8`，`efConstruction=200`** |
| 检索参数 | `nq=1, topk=1, ef=64` |
| 单机配置 | standalone：**12 核 / 16 GB** |
| 集群配置 | queryNode：**12 核 / 8 GB**，副本 1 |
| 压测时长 | 每个并发档位至少 1 小时 |

官方给出的结论与吞吐（延迟列在本次抓取中格式错位，故**不引用具体延迟值**）：

- 单机 **QPS 7522**；集群 **QPS 10248**。（同一页对比：Milvus 2.1 单机 4287、集群 6904）
- 原文：**"QPS increases linearly when expanding CPU cores from 8 to 32."**
- 原文：**"QPS increases linearly when expanding Querynode replicas from 1 to 8."**

> **量级解读（【推】）**：这是 **100 万 × 128 维**、`ef=64`、`topk=1` 的结果。本项目的向量**维度更高**（1024 vs 128，单次距离计算贵 8 倍），但**条数少一个数量级以上**（4 万 vs 100 万）。两者不在同一口径，**不能把 7522 QPS 直接套到本项目**。
> 版本注意：该报告是 **Milvus 2.2**（2023）；当前为 2.6/3.0，官方未提供同口径的新公开基准。

#### 3. pgvector `iterative index scans`（0.8.0）：官方怎么说、解决什么、代价是什么？

- **引入版本**：`0.8.0 (2024-10-30)` — **"Added support for iterative index scans"**，同版还有 **"Improved cost estimation for better index selection when filtering"**。【官：CHANGELOG】
- **解决什么问题**（官方原文）：**"With approximate indexes, queries with filtering can return less results since filtering is applied *after* the index is scanned."**【官：README「Iterative Index Scans」】
- **怎么工作**：**"Starting with 0.8.0, you can enable iterative index scans, which will automatically scan more of the index until enough results are found (or it reaches `hnsw.max_scan_tuples` or `ivfflat.max_probes`)."**【官：同上】
- **代价（官方明确承认）**：**"Since scanning a large portion of an approximate index is expensive, there are options to control when a scan ends."**【官：同上】可用边界（官方默认值）：
  - `hnsw.max_scan_tuples` **默认 20,000**；`hnsw.scan_mem_multiplier` **默认 1**（可按 `work_mem` 倍数放大）。
  - 两种排序模式：`strict_order`（严格按距离序）与 `relaxed_order`（**允许轻微乱序但召回更好**）。
  - **默认不开启**，必须显式 `SET hnsw.iterative_scan = ...`。【官：README「Iterative Scan Options」】
- **实现细节（官方 issue #678，作者即 pgvector 维护者）**：HNSW 会记录 layer 0 被丢弃的候选；需要更多结果时以最近的丢弃候选为入口再跑 `HnswSearchLayer`；**扫描在「结果足够 / 访问数达 `hnsw.ef_stream` / 超出 `work_mem`」时终止**。【官：[pgvector#678](https://github.com/pgvector/pgvector/issues/678)】

**它能否消除「后过滤丢召回」？——能缓解，但官方列了明确边界，不能说「消除」：**

1. 扫描是**有上限的**（`max_scan_tuples` / `max_probes` / `scan_mem_multiplier`），到达上限即停，召回仍可能不足。【官：README】
2. **子查询过滤不适用**——官方 issue #776 结论：**"A subquery can never be applied to the `filter` of an index scan"**；要让迭代扫描生效，`WHERE` 条件必须由 planner 下推到 index scan 的 `filter`。`IN (SELECT ...)` / `= ANY(SELECT ...)` 这类**不能被下推**，此时迭代扫描无能为力。【官：[pgvector#776](https://github.com/pgvector/pgvector/issues/776)】
3. 官方对「多租户共享一个索引」的态度是**劝你分区**而非依赖迭代扫描（见 §三.5）。【官：README「Multitenancy」】

#### 4. HNSW 索引构建时间，10 万 / 100 万量级有官方数据吗？——**两边都没有可直接引用的数字。**

**pgvector**（README 只有机制，无时间）：

- **"Indexes build significantly faster when the graph fits into `maintenance_work_mem`"**，并给出官方 NOTICE 样例：
  `NOTICE: hnsw graph no longer fits into maintenance_work_mem after 100000 tuples ... Building will take significantly more time.`【官：README「Index Build Time」】
  → 注意：**10 万 tuple 是官方举例时的取值，属于「该配置下的拐点」，不是 pgvector 的通用规模阈值。**
- 并行构建：`max_parallel_maintenance_workers` **默认 2**（可调高）；并行 HNSW **索引构建**自 `0.6.0` 起支持。【官：README + CHANGELOG 0.6.0】
- `0.8.6 (2026-07-29)` 修复了 `maintenance_work_mem` 被 IVFFlat 构建超出的问题 → 说明构建期内存管理是持续维护中的风险点。【官：CHANGELOG】

**Milvus**：官方「Data Processing」页说明索引由 Data Node **逐 segment 构建**、**计算与内存密集**、需 SIMD 加速，但**未给构建时间**。【官：[milvus.io/docs/data_processing.md](https://milvus.io/docs/data_processing.md)】

> **结论：索引构建时间的量级对比 → 未核实**（两边都无官方时间数字）。

#### 5. 过滤检索：双方官方策略原文

这是**唯一一条官方文档明确承认的架构性差异**，也是 team-lead 初步判断第 3 条中**成立**的部分。

**pgvector —— 后过滤（post-filtering）+ 多种补救手段**

原文（README「Filtering」）：

> **"With approximate indexes, filtering is applied *after* the index is scanned. If a condition matches 10% of rows, with HNSW and the default `hnsw.ef_search` of 40, only 4 rows will match on average."**

官方给出的四条路径，优先级大致如下（原文顺序）：

| 官方建议 | 原文要点 |
|---|---|
| 过滤列建 B-tree 等精确索引 | **"Exact indexes work well for conditions that match a low percentage of rows."** |
| 启用迭代扫描 | 见 §三.3；**默认关闭**，且只对能下推到 index scan `filter` 的条件生效 |
| 部分索引（partial index） | **"If filtering by only a few distinct values, consider partial indexing."** |
| 分区 / 独立表 | **"If filtering by many different values, consider partitioning."** |

**多租户/多 KB 的关键原文（README「Multitenancy」）**：

> **"For applications with multiple tenants, sharing an approximate index between tenants means vectors from one tenant can affect recall (and speed) for other tenants. For tenant isolation, use list partitioning or separate tables."**

→ 官方在此**明确否认「共享一个近似索引 + 租户过滤」是好的隔离方式**，给出的正解是**分区或独立表**。

**Milvus —— 预过滤（bitset）+ 标量索引**

原文（`scalar_index.md`）：

> **"Milvus then applies the physical plan in each segment to generate a bitset as the filtering result and includes the result as a vector search parameter to narrow down the search scope. In this case, the speed of vector searches relies heavily on the speed of attribute filtering."**

- 标量索引实现：`AUTOINDEX` 或 **inverted index（由 Tantivy 驱动）**，把标量字段排序以加速过滤。【官：[milvus.io/docs/scalar_index.md](https://milvus.io/docs/scalar_index.md)】
- **官方实验数字**：**"For a dataset of 1 million records, using an inverted index can provide up to a 30x performance improvement for point queries."**（对比 brute-force）【官：同上「Test results」】
- bitset 机制：过滤结果写成 0/1 位图，**作为向量搜索参数收窄搜索范围**（即预过滤）。【官：[milvus.io/docs/bitset.md](https://milvus.io/docs/bitset.md)】

**官方还有一个反直觉的补充**（Index Explained 决策矩阵）：**"High filter ratio (>95%) → Brute-Force (FLAT) — Avoids index overhead for tiny candidate sets."** → 当过滤后候选极少（>95% 被过滤掉）时，Milvus 官方也建议**别用索引、直接暴力搜**。【官：[milvus.io/docs/index-explained.md](https://milvus.io/docs/index-explained.md)】

> **对本项目的意义**：本项目「每个 KB 的分块数极少」（现状最大 KB 仅 121 chunk）。照 Milvus 自己的官方建议，这种「过滤后候选极小」的场景**连它自己的官方推荐都是 brute-force**，而不是 ANN——即**这里根本不存在「Milvus 因标量索引而占优」的空间**。【推，官方决策矩阵支持】

### B. 架构性差异

#### 6. 并发模型

| | pgvector（PostgreSQL） | Milvus |
|---|---|---|
| 单条 ANN 查询是否并行 | **否**。官方 issue #906 确认索引页读取走**同步 `ReadBuffer`**；维护者称异步/并行读的分支「在数据放不进 shared_buffers 时 `io_method=worker` **出现显著性能回退**」，**计划在 0.9.0 才引入**（依赖 PG 19）。社区提交的 **parallel IVFFlat scan PR #897 已关闭且未合并**。【官：#906 / #897】 | **是**。请求由 proxy 广播到相关 Streaming Node **并发**搜索，再由 Query Node 处理 sealed segment；官方基准称 CPU 8→32、副本 1→8 时 **QPS 线性增长**。【官：data_processing.md + benchmark.md】 |
| 并行能力的落点 | PostgreSQL 的并行查询作用于**精确检索的顺序扫描路径**（`max_parallel_workers_per_gather`，README「Exact Search」）；**近似索引扫描路径不在其中**。【官：README + #906/#897】 | Query Node 横向扩展 + 多副本，面向吞吐设计。【官：benchmark.md】 |
| 结论 | 单查询延迟**不随核心数下降**；QPS 随连接/后端数增长 | 单查询可跨 segment 并行；QPS 可水平扩展 |

> 净判断【推】：**「高并发吞吐」是 Milvus 相对 pgvector 的一个真实架构优势**；**「单查询延迟」在两者都远低于业务噪声时不是差异来源**。本项目定位企业内网助手、单 worker 部署，QPS 极低 → **该优势用不上**。

#### 7. 内存模型

**pgvector（官方原文，README FAQ）**：

> **"Do indexes need to fit into memory? **No**, but like other index types, you'll likely see better performance if they do."**

→ **不要求全量驻留**，可部分落在 OS page cache / 磁盘。README「Tuning」另建议 `shared_buffers` 取内存约 **25%**。【官】

**Milvus（官方原文，`index-explained.md`）**：

> **"graph-based indices typically have a higher memory footprint due to the graph's structure (e.g., **HNSW**), which usually implies a noticeable per-vector space overhead."**
> **"DiskANN allow parts of the index, like the graph or the refiner, to reside on disk, reducing memory load"**

**官方内存口径**（Sizing Tool 博客）：**"HNSW typically requires 2-3x the memory of the raw vector data."**，且 HNSW **"Requires the most memory per vector (highest cost)"**。【官：[blog.milvus.io sizing tool](https://blog.milvus.io/zh/blog/introducing-the-milvus-sizing-tool-calculating-and-optimizing-your-milvus-deployment-resources.md)】
Milvus 还有**硬性加载约束**：**"data to be load must be under 90% of the total memory resources of all query nodes"**。【官：[milvus.io/docs/limitations.md](https://milvus.io/docs/limitations.md)】

> 本项目数据量：40,000 × 1024 × 4 B = **163.8 MB** 原始向量（计算过程见 [vector-store-ha-options.md](./vector-store-ha-options.md) §四.3）。按 Milvus 官方 `2–3×` 口径，HNSW 图+向量约 **0.31–0.47 GB**——但 Milvus 官方最低 **8 GB** 内存地板（[prerequisite-docker](https://milvus.io/docs/zh/prerequisite-docker.md)），本量级下 94%+ 是固定系统开销。【推】

#### 8. 写入路径

**pgvector**：

- 官方建议**批量用 `COPY`**、**先灌数据后建索引**；生产用 `CREATE INDEX CONCURRENTLY` 避免阻塞写。【官：README「Loading」「Indexing」】
- **MVCC 膨胀 / VACUUM**：官方原文 **"Vacuuming can take a while for HNSW indexes. Speed it up by reindexing first."**（先 `REINDEX INDEX CONCURRENTLY` 再 `VACUUM`）。【官：README「Vacuuming」】
- 构建期内存：`maintenance_work_mem`；并行构建 workers 默认 2。【官：README】

**Milvus**：写入进 **WAL → growing segment → flush 为 sealed segment → 逐 segment 建索引 → compaction / handoff**；官方明确 **"Index building is ... computation- and memory-intensive"**，且 segment 未达建索引阈值前**退化为 brute-force**（`rootCoord.minSegmentSizeToEnableindex` 默认 **1024 行**）。【官：data_processing.md + [performance_faq.md](https://milvus.io/docs/performance_faq.md)】

> 净判断【推】：**一次灌 4 万分块**（本项目典型）两边都轻松；差异只在**持续高写入 / 流式写入**场景才可能显现——**该场景本项目没有**。具体写入吞吐数字 → **未核实**。

#### 9. 维度与索引限制（含与阿里云 RDS 的一致性核实）

**pgvector 上游自身限制（README 原文）**：

| 类型 | 存储上限 | 可建 HNSW/IVFFlat 索引上限 |
|---|---|---|
| `vector` | **16,000** 维（"Vectors can have up to 16,000 dimensions."） | **2,000** 维（"vector - up to 2,000 dimensions"） |
| `halfvec` | **16,000** 维 | **4,000** 维 |
| `bit` | — | **64,000** 维 |
| `sparsevec` | 16,000 非零元素 | **1,000** 非零元素（HNSW 节口径） |

【官：README「HNSW → Supported types」「Vector Type」「Halfvec Type」「Sparsevec Type」】

**CHANGELOG 佐证**：`0.4.0` — **"Increased max dimensions for vector from 1024 to 16000"**、**"Increased max dimensions for index from 1024 to 2000"**。【官：CHANGELOG】

**与阿里云 RDS 的说法是否一致？——一致。** RDS PG 官方指南称「最大支持创建 **16000 维度**的向量，最大支持对 **2000 维度**的向量建立索引」【官：[RDS pgvector 使用指南](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/pgvector-use-guide)】——**与上游 vanilla pgvector 的 `vector` 口径完全相同**（16000/2000），**未见 RDS 额外收紧**。本项目 **1024 维**在存储与索引两侧都有充足余量。

**Milvus 上游限制**：向量维度上限 **32,768**；Collection 上限 65,536；Field 64；`topk` 16,384。【官：[milvus.io/docs/limitations.md](https://milvus.io/docs/limitations.md)】

---

## 四、针对本项目的判断（4 万 × 1024 维）

### 4.1 直接回答「千级文档下有无性能差距」：**没有可测量、且影响业务的差距。**

理由是三条可核实的量级事实叠加：

1. **数据量小**：164 MB 原始向量。Milvus 官方公开基准的最小规模是 **100 万 × 128 维**（数据量约 512 MB），本项目条数只有它的 **4%**。【官：benchmark.md + README 内存公式】
2. **检索链路的耗时结构**：dense ANN + BM25 → RRF → **Rerank API → LLM 生成**。后两段是**网络 + 大模型**耗时（数百 ms 到数秒量级），ANN 段的毫秒级差异无法穿透。【推】
3. **RRF 只用排名不用分数**：即使两边返回的**分数分布**不同，RRF 的 `1/(k+rank+1)` 只看名次。真正影响 RRF 结果的不是「延迟」或「分数尺度」，而是**候选集是否召回了正确的分块**（即 recall）——这又回到 §三.5 的过滤问题，而非纯延迟问题。【推，项目链路事实】

### 4.2 带过滤检索：本项目该怎么看？

**现状**：`src/infra/db/vector_store/__init__.py:34` 是**一 KB 一 collection**（`get_or_create_collection(kb_id)`），检索按 KB 分别进行（`search.py:92`）。这等于**天然的租户隔离**。

**落到 pgvector 的两种数据模型，结论不同**：

| 模型 | pgvector 行为 | 评价 |
|---|---|---|
| **一 KB 一表 / 分区**（贴近现状） | 每个 KB 的表极小，**不存在跨租户后过滤丢召回** | ✅ 官方在 README「Multitenancy」里推荐的正是这个方向（"separate tables" / "list partitioning"） |
| **单表 + `kb_id` 过滤**（集中存储） | 后过滤；共享索引下他人向量会影响本 KB 的 recall 与 speed | ⚠️ 官方明确劝阻；若必须，需开迭代扫描 + 调 `max_scan_tuples`，并注意子查询过滤**不可下推**（#776） |

**关键事实**：现状最大 KB 只有 121 chunk。按 Milvus 自己的官方决策矩阵，「过滤后候选极小（>95% 过滤率）」应**用 brute-force 而非 ANN**——即这个场景两端都不靠 ANN 索引取胜，**Milvus 的标量索引优势在此无从发挥**。【官：index-explained.md 决策矩阵】

### 4.3 结论

- **性能不能作为选型理由**，无论支持还是反对 pgvector。【推】
- 唯一需要**在设计上处理**的性能相关项是「**带过滤检索**」：只需**沿用「一 KB 一表/分区」的数据模型**即可规避，无需引入 Milvus。【官 + 推】
- 因此选型应完全由 **HA 与运维**决定 → 与 [vector-store-ha-options.md](./vector-store-ha-options.md) 的推荐（RDS PG + pgvector）一致，两文不冲突。

---

## 五、未核实项 / 存疑项（明确列出）

### 5.1 team-lead 初步判断的逐条裁定

| # | 初步判断 | 裁定 | 依据 |
|---|---|---|---|
| 1 | 4 万 × 1024 维下两者 ANN 都是亚毫秒–毫秒级，差距被 Rerank/LLM 淹没 | **方向成立，但「亚毫秒–毫秒级」无一手数据支撑**；「被淹没」是【推】 | §三.1 官方无 pgvector 延迟数据 |
| 2 | 门槛大致：纯 ANN ~100 万+ / 带过滤 ~10 万+ / 高并发数百 QPS / 批量写入百万级 | **仅「带过滤」「高并发」有官方依据支持方向；四个具体数字均无法从官方来源证实** | §三.5 / §三.6；纯 ANN 与写入门槛无官方数据 |
| 3 | 唯一可能相关的差异是带过滤的向量检索（PG 后过滤丢召回；Milvus 有标量索引/bitmask） | **成立，且有官方原文**（pgvector「filtering is applied after」；Milvus「bitset ... narrow down the search scope」） | §三.5 |
| 4 | pgvector 0.8.0 引入 iterative index scans 缓解后过滤（CHANGELOG 已核实） | **CHANGELOG 属实**；但官方说「缓解」有明确边界，**不能等同于「消除」**（有扫描上限、默认关闭、子查询不可下推） | §三.3 |

### 5.2 未核实项

1. **pgvector 在 10 万 / 100 万 / 1000 万量级的真实延迟与召回**：**官方从未发布**（README/CHANGELOG 均无）。任何此类数字需注明二手来源。本文不引用。
2. **Milvus 官方基准的延迟数值**：`benchmark.md` 的延迟列在本次抓取中格式错位（QPS/TP99/TP50/fail 拼接无法可靠切分），故**只引用 QPS 与线性扩展结论，不引用延迟数字**。需要时应在浏览器中逐字核对原始表格。
3. **pgvector SQL 解析 / 计划的固定开销 vs Milvus gRPC 往返开销**：
   - pgvector 侧**只有官方 issue 中的实测样例（非基准）**：`Planning Time: 0.416 ms`（#186，9 万行 × 516 维）、`Planning Time: 0.548 ms`（#187，100 万行）。样例受硬件与查询影响，**不能当作通用常数**。【官：issue #186 / #187】
   - Milvus 侧 **gRPC 往返开销无官方数字** → **未核实**，无法比较。
4. **HNSW 索引构建时间**（10 万 / 100 万量级）：两边均**无官方时间数字** → **未核实**。
5. **批量写入吞吐**（pgvector COPY / Milvus WAL+segment）：均**无官方数字** → **未核实**。
6. **Milvus 2.6 / 3.0 的同口径公开基准**：官方公开 `benchmark.md` 仍是 **Milvus 2.2**；当前版本无同口径数据 → **未核实**。跨版本引用需谨慎。
7. **pgvector HNSW 索引的精确内存占用公式**：README 只给向量存储公式与「索引最好驻留内存」的建议，**未给图结构的 per-node 开销公式**；Milvus 侧 `index-explained.md` 给了 HNSW 图结构示例算法（度数 × 4 B/节点）。**pgvector 对应公式未核实**。
8. **Milvus 异步/并行索引读（hnsw-read-stream）落地时间**：维护者在 #906 称「计划进 **0.9.0**，依赖 **PG 19 beta**（2026-06-13 评论）」。**0.9.0 尚未发布前，本文所有关于 pgvector 无并行索引扫描的判断以当前 0.8.6 为准**；发布后需复核。
9. **阿里云 RDS PG 的实际版本与 pgvector 版本**：RDS 文档确认支持 16000/2000 维（与上游一致），但**实例默认搭载的 pgvector 具体小版本未核实**（`iterative_scan` 要求 ≥0.8.0；`halfvec` 要求 ≥0.7.0）。动手前应 `SELECT extversion FROM pg_extension WHERE extname='vector';` 核实。

---

## 六、附：本文关键一手来源

| 主题 | 来源 | 标注 |
|---|---|---|
| pgvector「Filtering」：后过滤、ef_search=40 下 10% 选择性只剩 4 行 | https://github.com/pgvector/pgvector （README「Filtering」） | 【官】 |
| pgvector「Iterative Index Scans」：0.8.0、扫描上限、strict/relaxed、默认值 20000 / ×1 | 同上（README「Iterative Index Scans」「Iterative Scan Options」） | 【官】 |
| pgvector「Multitenancy」：共享索引影响他人 recall/speed，建议分区或独立表 | 同上（README「Multitenancy」） | 【官】 |
| pgvector 索引不必全量驻留内存 | 同上（README FAQ "Do indexes need to fit into memory?"） | 【官】 |
| pgvector 索引构建：`maintenance_work_mem`、10 万 tuple 的 NOTICE、并行 workers 默认 2 | 同上（README「Index Build Time」） | 【官】 |
| pgvector VACUUM 慢、建议先 REINDEX | 同上（README「Vacuuming」） | 【官】 |
| pgvector 维度上限：vector 16000/索引 2000，halfvec 4000，bit 64000 | 同上（README「HNSW → Supported types」「Vector Type」） | 【官】 |
| pgvector 默认参数：m=16、ef_construction=64、ef_search=40 | 同上（README「Index Options」「Query Options」） | 【官】 |
| pgvector 0.8.0 与 0.4.0 / 0.6.0 版本变更 | https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md | 【官】 |
| pgvector 迭代扫描实现细节（候选追踪、ef_stream、work_mem） | https://github.com/pgvector/pgvector/issues/678 | 【官】 |
| pgvector 迭代扫描边界：子查询过滤不可下推 | https://github.com/pgvector/pgvector/issues/776 | 【官】 |
| pgvector 索引页读取为同步 ReadBuffer、并行读计划进 0.9.0 | https://github.com/pgvector/pgvector/issues/906 | 【官】 |
| pgvector parallel IVFFlat scan PR **关闭且未合并** | https://github.com/pgvector/pgvector/pull/897 | 【官】 |
| pgvector SQL Planning Time 实测样例（0.416 / 0.548 ms） | https://github.com/pgvector/pgvector/issues/186 、 /issues/187 | 【官】（样例，非基准） |
| Milvus 官方基线：100 万 SIFT 128 维、HNSW(M=8,efC=200)、ef=64、单机 QPS 7522 / 集群 10248、线性扩展 | https://milvus.io/docs/benchmark.md （Milvus 2.2） | 【官】 |
| Milvus HNSW：内存开销大、M/efConstruction/ef 默认值与调参建议 | https://milvus.io/docs/hnsw.md | 【官】 |
| Milvus 标量索引：bitset 预过滤、inverted index(Tantivy)、100 万条点查最高 30× | https://milvus.io/docs/scalar_index.md | 【官】 |
| Milvus bitset 机制：过滤结果作为向量搜索参数收窄范围 | https://milvus.io/docs/bitset.md | 【官】 |
| Milvus Index Explained：图索引内存更高、DiskANN 落盘、决策矩阵（>95% 过滤率用 FLAT） | https://milvus.io/docs/index-explained.md | 【官】 |
| Milvus DiskANN：图存盘、与内存索引的对比 | https://milvus.io/docs/disk_index.md | 【官】 |
| Milvus 写入路径：WAL → growing/sealed segment → 逐 segment 建索引 → compaction | https://milvus.io/docs/data_processing.md | 【官】 |
| Milvus 限制：维度 32768、Collection 65536、加载量 <90% query node 内存 | https://milvus.io/docs/limitations.md | 【官】 |
| Milvus 性能 FAQ：查询按 segment、未建索引则 brute-force、建索引阈值 1024 行 | https://milvus.io/docs/performance_faq.md | 【官】 |
| Milvus HNSW 内存 `2–3×` 原始向量、HNSW 每向量最贵 | https://blog.milvus.io/zh/blog/introducing-the-milvus-sizing-tool-calculating-and-optimizing-your-milvus-deployment-resources.md | 【官】 |
| Milvus 最低 8 GB / 4 核 | https://milvus.io/docs/zh/prerequisite-docker.md | 【官】 |
| 阿里云 RDS PG pgvector：16000 维存储 / 2000 维索引（与上游一致） | https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/pgvector-use-guide | 【官】 |
| 项目：一 KB 一 collection、按 KB 检索 | `src/infra/db/vector_store/__init__.py:34,81,126`；`search.py:92` | 【项】 |
| 相邻文档（能力/规模、HA/运维） | [comparison_milvus_vs_chroma.md](./comparison_milvus_vs_chroma.md)、[vector-store-ha-options.md](./vector-store-ha-options.md) | — |
