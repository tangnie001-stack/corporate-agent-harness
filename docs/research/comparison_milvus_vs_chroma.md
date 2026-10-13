# Milvus vs ChromaDB 选型对比（企业 RAG · 2.5–4 万分块量级）

> 适用场景：`corporate_rag` 项目，单机 docker compose、企业内网、目标规模「最多千级文档 ≈ 2.5 万–4 万分块 / 1024 维」。
> 对比对象：**当前在用的 chromadb 1.5.9（嵌入式 PersistentClient）** vs **Milvus 2.6（standalone 单容器嵌入模式）**。
> 本文所有结论均标注来源；找不到一手来源的写在「六、未核实项」。

---

## 一、一句话结论

> **本文的适用范围**：只回答「**规模与能力维度**——这个量级该用哪个向量库」。
> **高可用（HA）/ 机器故障单点**是另一个维度，结论不同且已单独成档，见 [vector-store-ha-options.md](./vector-store-ha-options.md)。两文不冲突：本文说「规模上不必换」，该文说「**若要求不成单点，则用「换归属」而非「换库」**——把向量与中文检索挂到已是 HA 的托管数据库（RDS PG + pgvector + pg_jieba）上」。阅读时请勿把本文结论误读为"什么都不用做"。

**这个量级推荐继续用 ChromaDB，不要引入 Milvus。**

理由是量化的、而非直觉的：

- 官方压测口径下，40,000 条 1024 维向量在 Chroma 里**只占约 0.16 GB 内存**（1 GB RAM 可支撑约 24.5 万条，见 §四.1）。Chroma 官方明确说单机可支撑到「接近千万级 embedding」。
- Milvus Standalone 官方**最低 8 GB / 推荐 16 GB** 内存（[官方安装要求](https://milvus.io/docs/zh/prerequisite-docker.md)）。在本量级，它的内存几乎全部花在 etcd / WAL / 协调器 / 对象存储等**固定系统开销**上，与数据量无关。**4 万向量对 Milvus 是明显的偏小量级。**
- 当前项目已有的能力（Python BM25 + RRF 融合、一 KB 一 collection、全局锁串行化）在这个量级都够用。

**会让我们改判为 Milvus 的条件**（满足其一才值得付出 8–16 GB + 运维复杂度）：

1. 需要**原生、带排序的中文全文检索**来替代自研 Python BM25（Milvus 内置 jieba 中文 analyzer + BM25；Chroma 内置 BM25 对中文不可用，见 §三.2）；
2. 需要**多副本 / 高可用 / 水平扩展**，或需要**多进程共享**同一向量库（Chroma 嵌入式做不到，见 §三.4）；
3. 单个集合要上**百万级**向量，或需要 DiskANN / 量化等内存压缩索引；
4. 明确要做**服务化**（Chroma 亦可切 `chroma run` server 模式，成本远低于 Milvus）。

---

## 二、能力对比总表

| 维度 | ChromaDB 1.5.9（现状） | Milvus 2.6（standalone） |
|---|---|---|
| License | Apache 2.0（[官方](https://docs.trychroma.com/docs/overview/introduction)） | Apache 2.0（[LICENSE](https://github.com/milvus-io/milvus/blob/master/LICENSE)） |
| 商用限制 | 无 | 无 |
| 部署形态 | 进程内嵌（`PersistentClient`，一个目录） | 独立容器 + etcd + 对象存储（可嵌入 etcd/MinIO） |
| 官方最低资源 | **不建议 < 2 GB RAM** | **8 GB RAM / 4 核**（推荐 16 GB / 8 核） |
| 向量索引 | HNSW（`chroma-hnswlib` fork / Rust 实现） | HNSW、IVF、DiskANN、量化、GPU 系列等 |
| 索引内存模型 | **必须全量驻留内存**，超内存即 swap、系统不可用 | **必须全量驻留内存**（HNSW），另支持 MMap/DiskANN 降内存 |
| 同数据量内存口径 | `N(百万) = R(GB) × 0.245`（1024 维实测） | 官方口径 **HNSW 为原始向量的 2–3 倍** |
| 原生全文检索 | ❌ 只有**无排序的子串过滤**（`$contains`/`$regex`） | ✅ **原生 BM25（带排序）**，2.5 起 |
| 中文全文检索 | ❌ 内置 BM25 分词器对中文无效（实测） | ✅ 内置 `chinese` analyzer（jieba + cnalphanumonly） |
| 需要额外组件做全文检索 | 否（但也不给排序） | 否（2.5 起内置 tantivy tokenizer，无需 ES） |
| 标量/元数据过滤 | ✅ | ✅（含 BitMap / 倒排 / JSON 索引，更强） |
| 混合检索 | 需自建（sparse EF + RRF 自行融合） | 原生 dense + sparse 同集合 |
| 并发模型 | 嵌入式单进程；官方称查询并行到 vCPU 数后排队 | 服务端并发，client/server |
| 多进程/多 worker | ❌ 单进程持有（本项目用全局 RLock 串行化） | ✅ 天然支持 |
| 备份/恢复 | 拷目录即可 | 独立 Go 工具 `milvus-backup`，有版本兼容矩阵 |
| 版本升级 | pip 升包 + 迁移 | 改镜像 tag；**有回滚不可逆风险** |
| 维护活跃度 | 高（2026 年 1.5.x 连续发版） | 高（2.6.x → 3.0 已发布） |
| 本量级适配 | ✅ 富余 | ⚠️ 过度工程（资源花在固定开销） |

---

## 三、ChromaDB 1.5.9 能力边界（逐项核实）

### 1. `$contains` 到底是怎么实现的？是过滤还是排序？——**是过滤，无排序**

**实现路径（一手源码）**：

- 迁移脚本建了一张 FTS5 虚表：
  `CREATE VIRTUAL TABLE embedding_fulltext_search USING fts5(string_value, tokenize='trigram')`
  （`.venv/.../chromadb/migrations/metadb/00003-full-text-tokenize.sqlite.sql:1`）
- 查询时**并不用 FTS5 的 `MATCH`**，而是退化成 SQL `LIKE`，再作为 `IN / NOT IN` 子查询做过滤：
  ```python
  # chromadb/segment/impl/metadata/sqlite.py:574-588
  elif k in ("$contains", "$not_contains"):
      search_term = f"%{v}%"
      sq = (self._db.querybuilder().from_(fulltext_t)
            .select(fulltext_t.rowid)
            .where(fulltext_t.string_value.like(ParameterValue(search_term))))
      return embeddings_t.id.isin(sq) if k == "$contains" else embeddings_t.id.notin(sq)
  ```

**实测（本项目持久化库 + 1.5.9）**：

| 验证 | 命令/操作 | 结果 |
|---|---|---|
| 是过滤不是排序 | `get(where_document={"$contains": "增值税"})` | 返回 `['a','b','c']`，**按插入序，无相关度分数** |
| 走的是 LIKE 索引（非 MATCH） | `EXPLAIN QUERY PLAN ... WHERE string_value LIKE '%增值税发票%'` | `VIRTUAL TABLE INDEX 0:L0`（LIKE 优化）；对照 `MATCH` 为 `0:M1` |
| 大小写敏感 | 文档 `"Hello World ABC"` 分别查 `Hello/hello/HELLO/ABC/abc` | `Hello→命中`、`hello→空`、`HELLO→空`、`ABC→命中`、`abc→空` |
| 大小写敏感的实现 | `PRAGMA case_sensitive_like = ON` | `chromadb/db/impl/sqlite.py:40,103` |
| FTS 表确有数据 | `select count(*) from embedding_fulltext_search` | 176（= embeddings 176，完全同步） |

官方文档亦只描述为「过滤」：[Full Text Search](https://docs.trychroma.com/docs/querying-collections/full-text-search) 原文用词是 *"used to **filter** records based on their document content"*，并明确 *"Full-text search is case-sensitive."*

**结论：Chroma 的 `$contains` 是「大小写敏感的子串过滤」，无 BM25、无相关度排序。**
额外发现：`$regex` / `$not_regex` 在 1.5.9 **实测可用**（`get(where_document={"$regex": "增值税"})` 正常返回），尽管 `sqlite.py` 的 `_where_doc_criterion` 未列该分支——说明 1.5.9 存在另一条执行路径（Rust bindings），未逐行核实。

### 2. Chroma 是否对外暴露 BM25 / 带排序的全文检索？——**有，但对中文不可用**

1.5.9 **确实内置**了 BM25 稀疏向量函数 `ChromaBm25EmbeddingFunction`（`chroma_bm25`，[官方文档](https://docs.trychroma.com/integrations/embedding-models/chroma-bm25)），但：

| 事实 | 来源 |
|---|---|
| 它是**稀疏向量 embedding function**，不是查询期的 ranked FTS；要自己建索引、自己做 dense+sparse 融合 | 官方文档同上 |
| 分词 = 去非字母数字 → **按空格切分** → 英文停用词 → **英文 Snowball 词干** → 丢弃长度 > 40 的 token | `chromadb/utils/embedding_functions/schemas/bm25_tokenizer.py:220-257` |
| 依赖可选的 `snowballstemmer`；本项目 venv **未安装**，直接抛 `ValueError: The snowballstemmer python package is not installed` | 实测 + `chroma_bm25_embedding_function.py:9-14,215-217` |
| 旧版 `Bm25EmbeddingFunction`（fastembed）已标记 **deprecated** | `bm25_embedding_function.py:49-53` |

**中文实测（用哑 stemmer 绕过缺依赖，隔离分词逻辑本身）**：

```
'增值税发票的开具流程'      -> tokens=['增值税发票的开具流程']     # 整句 1 个 token
'企业增值税专用发票认证'    -> tokens=['企业增值税专用发票认证']   # 整句 1 个 token
'hello world tax invoice'  -> tokens=['hello','world','tax','invoice']
```

即：**中文没有分词，整段文本塌缩成一个 token；而 token 长度上限是 40，一个正常 300–500 字的中文 chunk 会直接被丢弃 → 产出空稀疏向量。**（本项目现有 chunk 实测长度 344–506 字，见 §四.3）

> **对选型的影响**：项目现在自己实现的 Python BM25 **不能**用 Chroma 内置 BM25 替换。这是 Chroma 相对 Milvus 的一个真实短板，但在 4 万 chunk 量级下，Python BM25 的成本可以忽略（见 §五）。

> ⚠ **项目现状更正（2026-09-18 复核）**：本节初稿称"项目已用 `rank_bm25 + jieba`"，**与代码不符**。实测：
> - `src/infra/search/bm25_index.py:36` 建语料用 `list(chunk.content)`、`:92` 查询用 `list(query)` —— **字符级切分，未调用 jieba**（文件 docstring 亦自述"按字符级分词"）。
> - `jieba` 在 `pyproject.toml:38` **已声明为依赖**且**已安装**（`.venv/lib/python3.12/site-packages/jieba` 存在），但全仓库 `.py` 文件中 **零处调用**（`Grep pattern="jieba" glob="*.py"` 无命中）。
>
> **这使 Chroma 与 Milvus 的差距比初稿判断的更小**：Milvus 内置 `chinese` analyzer 的 tokenizer 正是 **jieba**（见 §四.2）。因此"接上 jieba"这一步就能让现有 Python BM25 的分词口径与 Milvus 原生中文 BM25 **基本对齐**——差距从"换库才能解决"降级为"改一行分词函数"，且**无需新增依赖**（jieba 已装）。

历史脉络（一手 issue）：[chroma-core/chroma#1686](https://github.com/chroma-core/chroma/issues/1686)（2024-01 提「加 BM25」）被维护者以 *"Chroma already supports full text search using the `where_document` feature"* 回复并关闭，社区立即反驳 *"where_document filtering is NOT full text search"*；后转向 #1330，最终以 sparse BM25 EF 落地。**#1330 的最终结论未核实。**

### 3. FTS5 用 `trigram` 分词器，对中文意味着什么？

- trigram 是**子串匹配导向**，不是中文分词：它按 3 字符滑窗建索引，用来加速 `LIKE '%子串%'`。已用 `EXPLAIN QUERY PLAN` 证实 Chroma 的 `$contains` 走的就是这条 `L0`（LIKE）路径。
- 对中文的实际含义：
  - ✅ **能做**「包含某段文字」的精确子串过滤（≥3 个字符时能吃到索引）；
  - ❌ **不能**做分词、不能做词级召回、**没有相关度排序**，也没有同义词/停用词/词干等文本处理；
  - 因此它**替代不了**「中文 BM25 关键词召回 + RRF 融合」这条链路，只能当作一个精确子串的辅助过滤条件。
- 来源：Chroma 迁移脚本与 `sqlite.py`（同 §三.1）；SQLite FTS5 trigram 支持 LIKE/GLOB 的说法本次**未能抓到 sqlite.org 官方原文**（见 §六）。

### 4. 嵌入式 `PersistentClient` 的资源模型与线程安全

**内存**（官方一手，[Single-Node Performance](https://docs.trychroma.com/guides/performance/single-node)）：

> *"The HNSW algorithm requires that the embedding index **reside in system RAM** to query or update... If a collection grows larger than available memory, insert and query latency spike rapidly as the operating system begins swapping... the system quickly becomes unusable."*
> 公式：*"For 1024 dimensional embeddings, with three metadata records and a small document per embedding, this works out to `N = R * 0.245` where N is the max collection size **in millions**, and R is the amount of system RAM **in gigabytes**."*
> *"Deploying Chroma on a system with **less than 2GB of RAM is not recommended**."*
> *"Splitting collections into multiple smaller collections doesn't help, but it doesn't hurt, either, as long as they all fit in memory at once."*
> *"Users should feel comfortable relying on Chroma for use cases approaching **tens of millions of embeddings**..."*

**线程安全 / 并发**：

- 1.5.9 本地路径实际由 Rust 绑定承担：site-packages 中存在 `chromadb_rust_bindings`（实测目录列表）。
- 项目因此对 Chroma 的全部访问加了一把全局 `threading.RLock`（`src/infra/db/vector_store/client.py:33-35`），把多 KB 并行检索串行化。
- **官方没有一句「PersistentClient 非线程安全」的原文** —— 我查了官方文档与 issue 均未找到该措辞（见 §六）。这是**项目侧的工程决策**，不是官方声明。
- 官方在并发上的原文只说：*"The queries parallelize up to the number of vCPUs available in the instance, after which point they begin queueing."* 另外 *"Infrequently used collections are moved to cold storage. The first time a collection is queried, it will be slower than average"*（[General Performance](https://docs.trychroma.com/guides/performance/general)）——**这条与 691 个 collection 的现状直接相关**。

**嵌入 vs server 模式**：切 server 只需把 `PersistentClient` 换成 `HttpClient`（`chroma run --path ...`），行为与方法签名一致（[Client-Server Mode](https://docs.trychroma.com/docs/run-chroma/client-server)）。这是比换 Milvus 便宜得多的并发/多进程方案。

### 5. 版本与维护活跃度

PyPI 一手发布记录（[pypi.org/project/chromadb](https://pypi.org/project/chromadb/)）：`1.5.9 = 2026-05-05`，此前 `1.5.0 = 2026-02-09`、`1.0.0 = 2025-04-03`，2025–2026 基本按月/双周发版。**项目当前已是最新 1.5.9，维护活跃，无停更迹象。**

已知的大规模问题：官方主动给了「单机 < 2 GB 不可用」「超内存即 swap 且不可逆」的边界说明（§三.4），这本身就是最大的性能风险点。**未在官方 issue 中核实到 4 万量级的已知性能缺陷。**

---

## 四、Milvus 2.6 能力与成本（逐项核实）

### 1. 官方最低资源与「单容器嵌入模式」

- **官方硬件要求**（[prerequisite-docker](https://milvus.io/docs/zh/prerequisite-docker.md)）：

  | 组件 | 最低 | 推荐 |
  |---|---|---|
  | CPU | 4 核（需支持 SSE4.2 / AVX / AVX2 / AVX-512 之一） | 8 核 |
  | RAM | **8 GB** | **16 GB** |
  | 磁盘 | SATA 3.0 SSD 起 | NVMe SSD |

  该页同时给出：依赖组件为 **etcd 3.5.0、MinIO、Woodpecker**（Woodpecker 为**内置默认消息队列，无需单独部署**；Pulsar 2.8.2 仅在切换 MQ 时才需要）。etcd 所在磁盘要求 **> 500 IOPS、p99 fsync < 10 ms**。

- **精简单容器模式是一等公民**：Milvus 官方脚本 `scripts/standalone_embed.sh` 就是用 `ETCD_USE_EMBED=true` + `COMMON_STORAGETYPE=local` 起一个容器，暴露 `19530/9091/2379`（[脚本原文](https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh)；注：master 当前指向 `v3.0.1`，2.6.x 时期同一脚本指向 2.6.x 镜像）。
  本地 WeKnora 项目的 compose 正是同一套环境变量、镜像 `milvusdb/milvus:v2.6.11`（`../github/WeKnora/docker-compose.yml:661-687`）。
- **嵌入 etcd 是官方配置项**：`etcd.use.embed` — *"Whether to enable embedded Etcd (an in-process EtcdServer). Default: **false**"*，配套 `etcd.data.dir`（[configure_etcd](https://milvus.io/docs/configure_etcd.md)）。即官方 compose 默认用外部 etcd，嵌入模式需显式开启。

### 2. 原生 BM25 与中文分词

- **引入版本**：Milvus **2.5**（2024-12-17 官方博客 [Introducing Milvus 2.5](https://milvus.io/blog/introduce-milvus-2-5-full-text-search-powerful-metadata-filtering-and-more.md)）。实现为 **Sparse-BM25**：tokenizer 基于 **tantivy**，BM25 打分在服务端内部完成。
- **是否需要额外组件**：**不需要** Elasticsearch 之类。2.5 之前的做法（PyMilvus 的 BM25EmbeddingFunction 在客户端算）正是被它取代的。
- **Schema 要素**（一手源码参考 `../github/WeKnora/internal/application/repository/retriever/milvus/repository.go:158-212`）：
  1. 文本字段：`VARCHAR + WithEnableAnalyzer(true) + WithMultiAnalyzerParams(...)`；
  2. 一个 **SparseVector** 字段（BM25 输出目标）；
  3. 一个 **Function**：`WithType(entity.FunctionTypeBM25)`，输入=文本字段，输出=稀疏字段；
  4. 索引：稀疏字段建 `NewAutoIndex(entity.BM25)`（等价 `index_type=AUTOINDEX, metric_type=BM25`，默认 WAND）。
- **查询侧**：对稀疏字段检索，带 `metric_type=BM25` 与 `analyzer_name`（`repository.go:844-853`）。
- **中文分词**：内置 `chinese` analyzer = **jieba tokenizer + cnalphanumonly filter**，配置 `analyzer_params = {"type": "chinese"}`；等价自定义配置 `{"tokenizer": "jieba", "filter": ["cnalphanumonly"]}`；并支持按字段/按行的多语言 analyzer 路由（`analyzer.go:55-73`，`by_field: language`，`en/zh` 别名 + `icu` 兜底）。`run_analyzer` 校验工具需 **2.5.11+**（[Chinese analyzer 官方文档](https://milvus.io/docs/chinese-analyzer.md)）。
- **官方给出的中文分词效果示例**：`"Milvus 是一个高性能、可扩展的向量数据库！"` → `['Milvus','是','一个','高性','性能','高性能','可','扩展','的','向量','数据','据库','数据库']`。

> 对照 §三.2：**这是 Milvus 在本场景下唯一一项 Chroma 明确做不到的能力**（原生、带排序、可中文分词的全文检索）。

### 3. 索引内存模型与本量级占用

- **HNSW 必须驻留内存**（[HNSW 文档](https://milvus.io/docs/v2.5.x/hnsw.md)）：*"...it requires **high memory overhead** to maintain its hierarchical graph structure."*；`M` 越大内存越高。
- **官方量级口径**（[Milvus Sizing Tool 博客](https://blog.milvus.io/zh/blog/introducing-the-milvus-sizing-tool-calculating-and-optimizing-your-milvus-deployment-resources.md)）：*"**HNSW typically requires 2-3x the memory of the raw vector data.**"*；HNSW 在各类索引里 *"Requires the most memory per vector (highest cost)"*。
- 降内存手段：MMap、DiskANN、HNSW-PQ/SQ/PRQ、2.6 的 cache layer（*"Process datasets larger than memory"*，见 [v2.6.0 release](https://github.com/milvus-io/milvus/releases/tag/v2.6.0)）。

**本量级内存估算（含计算过程）**：

| 项 | 25,000 向量 | 40,000 向量 |
|---|---|---|
| 原始向量 `n × 1024 × 4 B` | 102.4 MB | **163.8 MB** |
| Milvus HNSW（×2–3，官方口径） | 0.20–0.29 GB | **0.31–0.47 GB** |
| Chroma 实测（`n / 0.245M`） | 0.102 GB | **0.163 GB** |
| **官方最低内存要求** | Milvus 8 GB / Chroma「不建议 <2 GB」 | 同左 |
| **数据占最低要求的比例** | Milvus ≈ 3–6% | 同左 |

即：**Milvus 剩余的 7.5–15.5 GB（≥94%）是固定系统开销**，与数据量无关。这是判断「是否过度工程」的核心证据。

### 4. 运维复杂度：备份 / 升级 / 故障恢复

- **备份/恢复**：需额外使用独立工具 **milvus-backup**（Go，最新 v0.5.16），有**版本兼容矩阵**（[官方文档](https://milvus.io/docs/milvus_backup_overview.md)）：

  | 备份来源 ↓ / 恢复到 → | 2.2.x | 2.3.x | 2.4.x | 2.5.x | 2.6.x |
  |---|---|---|---|---|---|
  | 2.2.x | 否 | 否 | 是 | 是 | **否** |
  | 2.3.x | 否 | 否 | 是 | 是 | **否** |
  | 2.4.x | 否 | 否 | 是 | 是 | **否** |
  | 2.5.x | 否 | 否 | 否 | 是 | 是 |
  | 2.6.x | 否 | 否 | 否 | 否 | 是 |

  即**跨版本恢复受限**，低版本备份不能直接恢复到 2.6。对照：Chroma 的备份就是拷 `data/chroma_persist/` 目录。

- **升级**（[官方升级指南](https://milvus.io/docs/upgrade_milvus_standalone-docker.md)）：
  - 过程本身简单：**只改镜像 tag**，保留 etcd / 对象存储 / 卷；
  - 但官方明确警告：**降级/回滚不可保证** —— *"This procedure does not validate a downgrade or rollback... After v3.0.1 writes data, an image-only rollback can fail to read the updated state."*
  - **消息队列不可在升级中切换**；且 **2.6.x 把 standalone 默认 MQ 从 RocksMQ 改成 Woodpecker**，想保留 RocksMQ 必须在升级前于 `user.yaml` 固定 `mq.type: rocksmq`（[mq_rocksmq 文档](https://milvus.io/docs/mq_rocksmq.md)）。
  - 2.6.0 本身是**大改动**：Storage Format V2、Streaming Node GA、原生 WAL(Woodpecker) 移除 Kafka/Pulsar 依赖、MixCoord 合并、IndexNode 合并 DataNode；且 *"Direct upgrade from 2.6.0-RC1 versions is not supported due to architectural changes"*（[v2.6.0 release](https://github.com/milvus-io/milvus/releases/tag/v2.6.0)）。
- **整体风险**：Milvus 已发布 **3.0**（2026-08，Zilliz 公告），意味着未来一年会面对一次大版本升级，且 3.0 回滚代价高（[官方升级文档](https://milvus.io/docs/upgrade_milvus_standalone-docker.md)同段警告）。对「能跑就行、别折腾」的企业内网小规模场景，这是**实打实的长期维护成本**。

### 5. License

| | License | 商用限制 |
|---|---|---|
| Milvus | Apache 2.0（[LICENSE](https://github.com/milvus-io/milvus/blob/master/LICENSE)，LF AI & Data 基金会治理） | 无 |
| ChromaDB | Apache 2.0（[官方文档 Open Source 段](https://docs.trychroma.com/docs/overview/introduction)） | 无 |

两者均为宽松许可，**license 不构成选型差异**。

---

## 五、2.5–4 万分块量级的专项分析

### 1. 内存：两者都富余，但一个「地板上」一个「踩地板」

- 数据本身只需 **0.10–0.16 GB**（原始向量）～ **0.31–0.47 GB**（Milvus HNSW 含图开销）。
- Chroma：官方建议预留 ≥1 GB 给系统，**2 GB 机器即可跑**，4 GB 舒适。
- Milvus：**8 GB 是硬地板**，且这 8 GB 里 94%+ 与数据无关。
- 结论：**内存维度上 Milvus 在本量级是纯浪费**，不构成任何收益。

### 2. 并发：Chroma 的短板真实存在，但不需要 Milvus 来解决

- 现状：Chroma 嵌入式 + 项目全局 `RLock` → **所有向量检索串行**（`client.py:33-35`）。
- 官方口径：Chroma 查询并行到 vCPU 数后排队；单进程嵌入模式下多 worker 不成立（项目已按单 worker 部署，见 `CLAUDE.md` 部署形态规则）。
- **正确解法排序**：
  1. 保持嵌入式 + 单 worker（当前量级下检索本身 <10 ms 级，官方压测同规模 mean ≈ 5 ms）；
  2. 若真有并发压力 → 切 **Chroma server 模式**（`chroma run` + `HttpClient`），改动量小于换库；
  3. 只有在需要多副本/HA/百万级时，才轮到 Milvus。
- 另需注意官方「冷集合首次查询更慢」的行为（`General Performance`）：当前 **691 个 collection** 的现状会放大冷启动抖动——但根因是空 collection 副作用缺陷（`get_or_create_collection` 不存在即创建），**与换不换 Milvus 无关，应单独修**。

### 3. 磁盘：可忽略

- 现状：`chroma.sqlite3` = **10.24 MiB**（实测 10,739,712 B），其中 691 collections / 1382 segments 的元数据+176 条 embedding 全文索引。
- 官方数据点：40,000 篇 × 1000 词的文档，其 sqlite 约 **1.7 GB**（[Single-Node Performance](https://docs.trychroma.com/guides/performance/single-node)）。本项目 chunk 远小于 1000 词（实测 344–506 字），且 4 万 chunk ≠ 4 万篇文档，实际磁盘占用会显著低于该值。
- 官方磁盘规则：**存储 ≥ RAM + 数 GB**。两边都轻松满足。

### 4. 迁移成本（Chroma → Milvus 不是「无痛」）

| 成本项 | 说明 |
|---|---|
| 适配层重写 | 需重写 `src/infra/db/vector_store/`（一 KB 一 collection → 集合/schema 设计），去掉全局锁 |
| 检索链路改造 | 现在自研的 Python BM25 + RRF 应改为 Milvus 原生 dense+sparse+RRF，**才能兑现换库收益**（否则只是换了个存储） |
| 数据迁移 | 向量可原样导出，但 id/元数据/collection 命名需重建；Milvus 备份工具**不支持**从 Chroma 导入，需自写脚本 |
| 依赖与部署 | 新增 etcd/Woodpecker/MinIO 或嵌入模式 + 卷管理、镜像固定、备份工具；compose 内存预算需从各服务 `mem_limit` 中再挤出 8 GB |
| 回滚 | 一旦 2.6+ 写入，回滚镜像不可保证（官方警告） |

### 5. 是否「过度工程」？——**是，有证据支持**

**支持「过度工程」的证据**：

1. 量级证据：4 万 × 1024 维 = 163.8 MB 数据；Milvus 最低要求 8 GB，**数据占比 ≤ 6%**。
2. 官方定位证据：Milvus 面向「十亿级向量 / 分布式」，2.6 的核心卖点是 Storage V2、Streaming Node、量化索引——**都是为大数据量设计的**；本项目一个都用不上。
3. 对照证据：Chroma 官方明确单机可撑「接近千万级 embedding」，4 万是其确认能力的 **0.4%**。
4. 成本证据：新增独立 Go 备份工具、跨版本恢复受限、MQ 默认变更、3.0 大版本升级与不可回滚风险——**纯负担，无收益**。

**反对（即值得换）的证据**：

1. 唯一硬需求是**原生中文带排序 BM25**。项目已有 `rank_bm25`，且 **jieba 已在依赖中但未接线**（见 §三.2 更正框）——补上分词后，分词口径与 Milvus 内置 `chinese` analyzer 基本一致。在 4 万 chunk 量级下 Python BM25 的内存/耗时都在可接受范围，**收益不抵 8 GB + 运维成本**。
2. 若未来要**服务化、多副本、百万级单集合**，则 Milvus（或 Chroma server / 其他库）才重新进入候选。

**判定：在给定约束（单机、内网、2.5–4 万 chunk、最多千级文档）下，引入 Milvus 属于过度工程。推荐保留 ChromaDB；把精力放在修空 collection 缺陷、按需切 Chroma server 模式、以及中文 BM25 质量上。**

---

## 六、未核实项 / 存疑项（明确列出）

1. **Chroma 线程安全性的官方立场**：官方 Cookbook 明确写「**Chroma is thread-safe**」，同时写「**not process-safe for concurrent writers sharing the same local persistence path**」（[System Constraints](https://cookbook.chromadb.dev/core/system_constraints/)）。即官方**从未**说过「非线程安全」——项目注释（`client.py:33-35`）称「PersistentClient 底层 Rust 绑定非线程安全」与官方立场**相反**。官方记录的失败模式是**多进程**（见 [vector-store-ha-options.md](./vector-store-ha-options.md) §B1：#7040 实测两进程打开同一 `persist_dir` 挂起约 16 分钟），而项目那把 `RLock` 是**进程内**的——既可能多余（官方称线程安全），又**无法覆盖**官方记录的多进程风险。此条值得单独验证。
2. **SQLite FTS5 trigram 支持 LIKE/GLOB 的一手原文**：`sqlite.org/fts5.html` 本次抓取始终返回空/失败，未能引到官方原句。但我在本项目库上用 `EXPLAIN QUERY PLAN` 实测到 `LIKE` 走 `VIRTUAL TABLE INDEX 0:L0`、`MATCH` 走 `0:M1`，**间接证明 trigram 索引可服务 LIKE**。二手佐证：某第三方 tokenizer 仓库 README 称「不支持 LIKE & GLOB 是 FTS5 的限制」——属二手。
3. **Milvus 2.6.x 完整破坏性变更清单**：`milvus.io/docs/v2.6.x/release_notes.md` 本次多次抓取失败（403 与空返回），只能从 GitHub `v2.6.0` release 页、官方升级指南、MQ 文档中获得**部分**结论；不排除还有未列出的破坏性变更。
4. **Milvus standalone 稳态实测内存数字**：未找到官方 idle 基准。只能给「官方最低 8 GB / 推荐 16 GB」与「数据本身 0.3–0.47 GB」两端，中间的系统开销未实测。
5. **Milvus 2.6.11 是否原生支持嵌入模式**：官方 `standalone_embed.sh`（master，指向 v3.0.1）与 `etcd.use.embed` 配置项证明该模式是官方能力；WeKnora 用 `v2.6.11` + 同套 env 亦为佐证，但**未取得 v2.6.11 tag 下的脚本原文**（GitHub raw 直连返回 403）。
6. **Chroma issue #1330 的最终状态**：未核实（#1686 被关闭并指向它）。
7. **Milvus 官方 HNSW「2–3×」的适用边界**：该句来自 Milvus 博客（官方口径），但未给出维度/`M` 的具体取值前提，故本文按区间使用，未当作精确值。
8. **本项目 chroma 现状数字**：已用 `sqlite3` 逐条复核（collections=691、segments=1382、embeddings=176、fulltext=176、非空 collection 121+51+2+1+1=176、库大小 10.24 MiB），与任务给定一致。

---

## 附：本文用到的关键一手来源清单

| 主题 | 来源 |
|---|---|
| Chroma FTS 语义（过滤、大小写敏感） | https://docs.trychroma.com/docs/querying-collections/full-text-search |
| Chroma 内置 BM25 EF | https://docs.trychroma.com/integrations/embedding-models/chroma-bm25 |
| Chroma 单机性能与内存公式 `N=R×0.245` | https://docs.trychroma.com/guides/performance/single-node |
| Chroma server 模式 | https://docs.trychroma.com/docs/run-chroma/client-server |
| Chroma 版本历史（1.5.9 = 2026-05-05） | https://pypi.org/project/chromadb/ |
| Chroma License | https://docs.trychroma.com/docs/overview/introduction |
| Chroma 源码（FTS5/LIKE/大小写） | `.venv/.../chromadb/migrations/metadb/00003-full-text-tokenize.sqlite.sql:1`；`.../segment/impl/metadata/sqlite.py:574-588`；`.../db/impl/sqlite.py:40,103`；`.../embedding_functions/schemas/bm25_tokenizer.py:220-257` |
| Milvus 最低硬件要求 / 依赖 | https://milvus.io/docs/zh/prerequisite-docker.md |
| Milvus BM25 引入版本与技术实现 | https://milvus.io/blog/introduce-milvus-2-5-full-text-search-powerful-metadata-filtering-and-more.md |
| Milvus 中文 analyzer（jieba） | https://milvus.io/docs/chinese-analyzer.md |
| Milvus HNSW 内存特性 | https://milvus.io/docs/v2.5.x/hnsw.md |
| Milvus HNSW 2–3× 口径 | https://blog.milvus.io/zh/blog/introducing-the-milvus-sizing-tool-calculating-and-optimizing-your-milvus-deployment-resources.md |
| Milvus 备份/恢复与兼容矩阵 | https://milvus.io/docs/milvus_backup_overview.md |
| Milvus 升级与回滚风险 | https://milvus.io/docs/upgrade_milvus_standalone-docker.md |
| Milvus 嵌入 etcd 配置 | https://milvus.io/docs/configure_etcd.md |
| Milvus MQ 默认变更 | https://milvus.io/docs/mq_rocksmq.md |
| Milvus 2.6.0 架构变更 | https://github.com/milvus-io/milvus/releases/tag/v2.6.0 |
| Milvus 单容器嵌入脚本 | https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh |
| Milvus License | https://github.com/milvus-io/milvus/blob/master/LICENSE |
| 项目侧证据 | `src/infra/db/vector_store/client.py:33-35,84-92`；`../github/WeKnora/docker-compose.yml:661-687`；`../github/WeKnora/internal/application/repository/retriever/milvus/repository.go:158-212,844-853`；`.../milvus/analyzer.go:55-73` |
