# 生产部署形态与向量库 HA 选型（含阿里云托管方案）

> 适用场景：`corporate_rag` —— 企业内网 RAG 助手，**最多千级文档 ≈ 2.5–4 万分块 / 1024 维**（DashScope `text-embedding-v3`）。
> 现状（测试环境）：chromadb 1.5.9 嵌入式 `PersistentClient`（`data/chroma_persist`），FastAPI 单 worker，单机 docker compose。
> **生产环境新前提（2026-09-18 用户确认）**：可部署 **2 台机器**；nginx 换成**阿里云负载均衡（SLB）**；**Redis 与 MySQL 已用阿里云高可用版托管**；文件存储后续走**阿里云 OSS**。
> 用户因此判断「**向量库是仅剩的单点故障点**」——本文以此为前提展开。
>
> 来源标注：`【官】`= 官方文档/官方 issue；`【项】`= 项目源码/项目文档；`【推】`= 本文推理。
> 相邻文档：[comparison_milvus_vs_chroma.md](./comparison_milvus_vs_chroma.md)（向量库能力与量级对比，本文不复制其结论）。
> 找不到一手依据的一律进 §九「未核实项」。

---

## 一、一句话结论

**在「2 台机 + 阿里云托管 MySQL/Redis + SLB」的前提下，不要在自建 Chroma 和自建 Milvus 之间选，而要把向量与中文全文检索一起搬进已有的阿里云托管数据库能力**：

1. **首选 `阿里云 RDS PostgreSQL + pgvector + pg_jieba/zhparser`**。这是唯一能**同时**解决「向量检索 + 中文检索 + HA」三件事、且三者都买托管的方案——HA 跟随 RDS 主备，中文分词是官方支持的扩展，pgvector 官方支持到 2000 维索引（我们只要 1024）【官：RDS 插件列表 / pgvector 使用指南 / 高可用与容灾设计】。
   > 措辞校准（依据 §6.3(f)）：PG 内置全文检索排序是 `ts_rank`/`ts_rank_cd`、**不是 BM25**；要「严格 BM25」需 RDS 列表里的 **`pg_textsearch`**（仅 PG 17/18，其官方样例直接组合 `zhparser` 做中文 BM25）。该扩展能否在 RDS 上加入 `shared_preload_libraries` **尚待核实**（§九.14），未确认前应表述为「中文全文检索」而非「中文 BM25」。
2. **次选 `阿里云 DashVector`**（+ DashText 稀疏向量做中文关键词）。HA 靠「副本数 ≥2」买到，成本量级可算（S.small ×2 副本 ≈ **¥0.5/小时**），且 DashText 内置编码器**用 jieba 做中文分词**、本身就是 BM25 稀疏向量【官：产品规格 / 产品计费 / DashText】。**但付费实例最多 32 个 Collection**，与本项目「一 KB 一 collection」的设计冲突（见 §六.2）。
3. **不要用自建 Milvus Cluster**：官方集群形态需要 K8s + etcd×3 + MinIO×4 + Woodpecker×4，**2 台机器装不下且运维成本极高**；而 **Milvus Standalone / 阿里云 Milvus 入门版（单机版）本身就等于单点**，换过去不解决任何问题。
4. **Chroma 在「不允许成为单点」下没有可行方案**：开源版无复制能力，把持久化目录放 NAS/OSS 共享存储的做法被 **SQLite 官方文档明确否定**（见 §五）。

> 一句话：**向量库那一格不用「换库」，用「换归属」——把它挂到已经是 HA 的托管数据库上。**

---

## 二、前提与威胁模型

### 2.1 用户判断成立的部分

| 组件 | 生产形态 | 是否仍是单点 |
|---|---|---|
| nginx | 阿里云 SLB | ❌ 托管，由阿里云兜底 |
| MySQL | 阿里云 RDS 高可用版 | ❌ 一主一备、秒级切换【官：[RDS 高可用和容灾](https://help.aliyun.com/zh/rds/product-overview/high-availability-and-disaster-recovery)】 |
| Redis | 阿里云托管 | ❌ 托管 |
| 原始文件 | 现为自建 MinIO；**计划迁 OSS** | ⚠️ 迁移前是单点（见 §七） |
| **向量库** | **自建 Chroma 嵌入式，在 app 进程内** | ✅ **是** |
| app | 2 台机器（计划） | ⚠️ 见下 |

【推】**「向量库是仅剩的单点」这个判断在生产形态下成立**——前提是 OSS 迁移完成、且 app 真的做到了双副本。

### 2.2 一个必须同时说清的约束：app 的多副本还有第二重门槛

即使把向量库换成托管服务、app 也可以起 N 个副本，本项目还有一条**与向量库无关**的限制：**流式生成状态（任务注册表 / 事件缓冲）在进程内，生产部署规定单 worker**【项：`CLAUDE.md`「规则 → 部署形态」】。

【推】因此在设计生产 HA 时要把两件事分开：
- **向量库**：换成托管服务即可解除单点（本文主题）；
- **app 层**：要么继续保持单实例（那 2 台机器里另一台是热备/冷备），要么先解决流式状态的跨进程共享（那是另一个变更，本文不展开）。

---

## 三、「横向扩展」与「高可用」仍然是两件事

| | 横向扩展（scale-out） | 高可用（HA） |
|---|---|---|
| 解决什么 | 读写吞吐 / 容量不够，加节点分担 | 一台机器坏了，服务不中断 |
| 需要什么 | 分片 | **数据第二份（复制）** ＋ **能接管（failover）** |
| 本量级是否必要 | ❌ 不必要 | ⚠️ 取决于停机成本 |

【推】4 万 × 1024 维 ≈ **163.8 MB** 原始向量（计算过程见邻档 §四.3）；Chroma 官方单机可撑「接近千万级 embedding」【官：[Single-Node Performance](https://docs.trychroma.com/guides/performance/single-node)】。**吞吐维度完全不需要横向扩展**，所以本文只讨论 HA。

---

## 四、自建 vs 托管：总对照表

「能否扛单机故障」= 该机器永久下线后服务是否仍可用。

| 方案 | 能否扛单机故障 | 最少组件 | 需几台机器 | 是否同时解决中文 BM25 | 成本量级 | 一手来源 |
|---|---|---|---|---|---|---|
| **Chroma 嵌入式（现状）** | ❌ | app 进程内 | 1 | ❌（内置 BM25 对中文无效，见邻档 §三.2） | 0 | 【官】[Cookbook](https://cookbook.chromadb.dev/core/system_constraints/) |
| Chroma server 模式 | ❌（DB 仍单节点） | +1 容器 | 1 | ❌ | 0 | 【官】同上（app tier 可 scale out，DB node 主要纵向） |
| **RDS PG + pgvector + pg_jieba/zhparser** | ✅（RDS 主备/多可用区） | 1 个 RDS 实例 | 0（托管） | ⚠️ 中文分词 ✅ 官方扩展；严格 BM25 需 `pg_textsearch`（仅 PG17/18，可预加载性待核实，见 §6.3(f)） | 与 RDS PG 实例同价位（未查价，见 §九.4） | 【官】[RDS 插件列表](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/extensions-supported-by-apsaradb-rds-for-postgresql)、[pgvector 使用指南](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/pgvector-use-guide)、[zhparser](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/use-the-zhparser-extension-to-segment-chinese-text) |
| **DashVector + DashText** | ✅（副本数 ≥2） | 1 个 Cluster | 0（托管） | ✅（BM25 稀疏向量 + jieba） | S.small ×2 副本 ≈ **¥0.5/时** | 【官】[产品规格](https://help.aliyun.com/document_detail/2636861.html)、[产品计费](https://help.aliyun.com/document_detail/2510232.html)、[DashText](https://www.alibabacloud.com/help/zh/vrs/latest/dashtext) |
| 阿里云 Milvus **入门版（单机版）** | ❌ | 1 实例 | 0 | ✅（Milvus 原生中文 analyzer） | 未查到单价 | 【官】[FAQ](https://help.aliyun.com/zh/milvus/support/faq) |
| 阿里云 Milvus **标准版（集群版）+ 高可用配置 / 多可用区高可用版** | ✅ | 1 实例（内部多组件多副本） | 0 | ✅ | 官方计费示例 **¥7144/月**（标准版一档），高可用资源 ×2 | 【官】[计费项](https://help.aliyun.com/zh/milvus/product-overview/billing-item)、[多可用区对比](https://help.aliyun.com/zh/milvus/product-overview/multi-zone-basic-edition-vs-high-availability-edition)、[资源估算](https://help.aliyun.com/zh/milvus/user-guide/milvus-resource-estimation-and-configuration-recommendations) |
| 阿里云 Elasticsearch（dense_vector + IK） | ✅（分片副本） | 1 个 ES 实例 | 0 | ✅（IK 分词插件内置） | 未查价 | 【官】[dense_vector 用法](https://help.aliyun.com/en/es/user-guide/vectorization-of-text-data-through-alibaba-cloud)、[IK 分词插件](https://www.alibabacloud.com/help/zh/es/user-guide/use-the-analysis-ik-plug-in) |
| Zilliz Cloud（Milvus 原厂托管） | ✅（Enterprise 起有 SLA） | 1 个 Dedicated 集群 | 0 | ✅ | Enterprise **$197/月起** | 【官】[Pricing](https://zilliz.com/pricing) |
| **自建 Milvus Cluster（K8s）** | ✅（集群内） | K8s + etcd×3 + MinIO×4 + Woodpecker×4 + 5 类 Milvus 组件 | **≥3** | ✅ | 高（机器 + 运维） | 【官】[Helm 安装](https://milvus.io/docs/install_cluster-helm.md)、[install-overview](https://milvus.io/docs/install-overview.md) |
| 共享存储 + Chroma 双机接管 | ❌ **被官方否定** | — | — | — | — | 【官】[SQLite Over a Network](https://www.sqlite.org/useovernet.html)（见 §五） |
| Chroma + 备份 + 重建（无 HA） | ❌（有数据，无连续性） | 备份脚本 | 1 | ❌ | 0 | 【推】 |

---

## 五、【证伪】能不能把 Chroma 的持久化目录放共享网络存储？

**结论：不能。这是一条被 SQLite 官方明确否定的路，无需再考虑。**

`data/chroma_persist` 的核心是 **SQLite 数据库文件** `chroma.sqlite3`（外加若干 HNSW 索引的 UUID 目录）。SQLite 官方专门有一页讲网络文件系统，原文关键句【官：[SQLite Over a Network, Caveats and Considerations](https://www.sqlite.org/useovernet.html)】：

> 「SQLite relies on **exclusive locks** for write operations, and those have been **known to operate incorrectly for some network filesystems. This has led to database corruption.**」
>
> 「…if the network link is inserted into the File I/O channel, transactions may fail (as for the API Call insertion) but with the additional effect that **the remote database is corrupted**.」
>
> 「These network unreliability issues can be mitigated… However, the SQLite library is **not tested in across-a-network scenarios**, nor is that reasonably possible. Hence, use of a remote database is done **at the user's risk**.」
>
> 「**Rely upon it at your (and your customers') peril.**」

官方还直接给出了三条替代建议，**第 1 条就是 PostgreSQL**：

> 1. Use a client/server database engine. **PostgreSQL** is an excellent choice.
> 2. Host an SQLite database in **WAL mode**, but do all reads and writes from processes on the **same machine** that stores the database file; implement a proxy on that machine.
> 3. Use SQLite in rollback mode（多读或单写，不能同时读写）。

**逐条回应用户的两个怀疑**：

| 怀疑 | 结论 | 依据 |
|---|---|---|
| a) OSS 是对象存储、非 POSIX，不可用 | ✅ **成立** | OSS 提供的是对象 API；官方文档描述的是 Bucket/Object/Multipart 操作，不是文件系统语义【官：[OSS 兼容的 S3 API](https://help.aliyun.com/zh/oss/developer-reference/compatibility-with-amazon-s3)】。通过 ossfs 挂载会引入同样的网络文件系统问题 |
| b) SQLite 跑在 NFS/NAS 上有已知锁与一致性风险 | ✅ **成立，且是官方措辞** | 见上引 SQLite 官方页；另外 Chroma 官方 issue 也提到 WAL 在 NFS/SMB/CIFS 上不安全【官：[chroma#7040](https://github.com/chroma-core/chroma/issues/7040)】 |

【推】即使抛开损坏风险，这条路也**不构成 HA**：NAS 上仍只能有一个写入者，第二台机器「接管」需要人为切换 + 挂载 + 重启进程，没有 failover 机制；而且 NAS 自身又引入一个新的共享存储单点与性能瓶颈。

> **这一条的结论是确定的：Chroma 在本项目「2 台机 + 不允许成为单点」的约束下没有可行解。**

---

## 六、候选方案逐项核实

### 6.1 阿里云 Milvus 托管版

**产品确实存在，叫「向量检索服务 Milvus 版」**，官方定位：云原生、全托管、100% 兼容开源 Milvus，基于 Serverless 架构，官方宣称 **99.9% 可用性保证**【官：[产品页](https://www.aliyun.com/product/milvus)】。

**关键：它分两个实例系列，HA 能力差别巨大。**

| | 入门版（**单机版**） | 标准版（集群版） |
|---|---|---|
| 官方定位 | 「适合于初次接触 Milvus 或进行测试的用户，让您能够快速体验 Milvus 的功能」 | 「针对生产环境设计，稳定性更高」 |
| 适用规模 | 官方公测说明：向量数据规模 **500 万以下** | 超过 500 万或需要集群能力 |
| HA | 【推】**单机版 = 单点**，与自建 Standalone 同构 | 可开「高可用配置」或选多可用区版 |

来源【官】：[FAQ](https://help.aliyun.com/zh/milvus/support/faq)、[公测说明](https://help.aliyun.com/zh/milvus/product-overview/vector-retrieval-service-milvus-version-free-public-test-instructions)、[资源估算与配置建议](https://help.aliyun.com/zh/milvus/user-guide/milvus-resource-estimation-and-configuration-recommendations)。

**HA 的两种实现与官方数字**：

1. **高可用配置**（实例内）：官方原文「高可用配置通过**节点双副本**机制确保线上集群的稳定性，并默认支持**数据双副本加载**…启用高可用配置所需的资源是**非高可用配置的两倍**」【官：资源估算页】。
2. **多可用区部署**（跨机房），官方给了明确的 RPO/RTO/SLA/成本【官：[多可用区基础版与高可用版对比](https://help.aliyun.com/zh/milvus/product-overview/multi-zone-basic-edition-vs-high-availability-edition)】：

| 对比项 | 单可用区 | 双可用区基础版 | 双可用区高可用版 | 跨地域高可用版 |
|---|---|---|---|---|
| 机房故障 RPO/RTO | 不具备机房级容灾 | RPO=0，RTO<1 小时 | RPO=0，**RTO<3 分钟** | RPO<10 秒，RTO<3 分钟 |
| SLA | 99.9% | 99.9% | **99.95%** | 99.99% |
| 成本 | 1× | 1–1.2× | **2×**（计算 100%，严格主备） | 3× 起 |

> ⚠️ 该页原文亦说明：多可用区高可用版「节点数必须为 **2 的倍数**」，且「不支持 2.4 版本内核」。

**成本**：计费 = CU（计算）+ 存储。1 CU = 1 核 + 4 GiB；华东1 服务节点 **¥157/CU/月**、计算节点 **¥211/CU/月**；存储 ¥0.0003125/GB/时【官：[计费项](https://help.aliyun.com/zh/milvus/product-overview/billing-item)】。
官方给的**标准版计费示例**（元数据/Proxy/Index/Data 各 2 节点 + Query Node 2 节点）合计 **¥7144/月**——注意该示例的 Query Node 是「容量型 8 核 32 GiB ×2」。**开高可用还要再翻倍。**

**对本项目**：4 万向量（0.04 M）远低于官方「500 万以下可用入门版」的门槛，按资源算入门版绰绰有余——**但入门版是单机版，用它来解 HA 是自相矛盾**。要 HA 就必须上标准版 + 高可用/多可用区，成本量级跳到数千元/月，而数据只有 164 MB。**结论：能力够、但成本与本量级严重不匹配。**

> 口径冲突提示：FAQ 页有一句「在公测期间，入门版和标准版都不提供服务等级协议（SLA）的保证」，而产品页与多可用区页给出 99.9%/99.95% SLA，且计费页已更新至 2026-09-11（已商业化）。**疑为 FAQ 残留旧文案，未核实最新口径**（§九.3）。

### 6.2 DashVector（向量检索服务）

**HA：明确有，且是官方建议。** 原文【官：[产品规格](https://help.aliyun.com/document_detail/2636861.html)】：

> 「向量检索服务DashVector支持调整副本数，可选范围为 **1-5**。副本之间数据完全相同，副本数越大，可支持的 QPS 越高，呈线性关系。同时副本数越大，**服务可用性越高，建议对可用性有较高要求的生产环境选择 >=2 的副本数**。」
> 「副本数的增加和减少不会影响存储容量，仅影响 QPS 和可用性。」

**规格与容量（4 万向量够不够）**：官方容量参考以 768 维 / 1536 维给出，我们 1024 维介于两者之间【推】：

| 实例类型 | 规格 | 768 维容量 | 1536 维容量 | 单价（按量） |
|---|---|---|---|---|
| 性能型 | P.small | 500,000 | 250,000 | ¥0.375/时 |
| 存储型（官方推荐） | **S.small** | **2,500,000** | **1,250,000** | **¥0.250/时** |
| Serverless | — | 不限（QPS<2 场景） | 不限 | 写 ¥3.60/百万写请求单元、读 ¥8.00/百万读请求单元、存储 ¥1.50/GB/月 |

**计费公式**：`实例规格单价 × 计费时长 × 副本数`，**仅支持按量付费**【官：[产品计费](https://help.aliyun.com/document_detail/2510232.html)】。

【推】**最低可用 HA 档位 ≈ S.small × 2 副本 = ¥0.5/小时 ≈ ¥365/月**（按 730 小时）。40,000 个 1024 维向量约为 S.small 容量的 **2%–3%**，余量极大。如果 QPS 确实很低，Serverless 型可能更便宜，但「不限容量 + 按请求计费」在写入 4 万条时的写请求单元数**未核实**。

**中文关键词：官方有现成方案。** DashText 是 DashVector 官方推荐的稀疏向量编码器，用 BM25 把文本转成稀疏向量；官方原文明确：

> 「内置 Encoder 使用**中文 Wiki 语料**进行训练，采用 **Jieba** 进行**中文分词**」

并支持 dense + sparse 混合检索与 `alpha` 权重调节【官：[DashText 快速开始](https://www.alibabacloud.com/help/zh/vrs/latest/dashtext)】。

**⚠️ 但对本项目有一个硬冲突**：付费实例**最多 32 个 Collection**，Serverless 型同样 32 个【官：[约束与限制](https://help.aliyun.com/document_detail/2510263.html)】。本项目「一 KB 一 collection」，而现状是 **691 个 collection**（已逐条复核，见邻档 §六.8）。**要在 DashVector 上跑，必须先把数据模型改成「少数 collection + `kb_id` 字段过滤」**——这是一次真实的重构，不是改配置。

其他限制：向量维度 (1, 20000]、检索 TopK ≤1024、Body ≤2MB、Fields ≤1024、每 UID 最多 3 个 API-KEY。另外 DashVector 是**读写分离架构**，官方明示写入后**可能无法立即被检索到**，只保证最终一致【官：约束与限制】——对「上传后马上问」的体验有影响，需要评测。

### 6.3 RDS PostgreSQL + pgvector + 中文分词扩展（**首选**）

这条路的推理链**基本成立**，逐条核实如下。

**(a) RDS PG 支持 pgvector —— 确认。** 官方原文【官：[pgvector 使用指南](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/pgvector-use-guide)】：

> - 「RDS PostgreSQL 支持 pgvector 插件…最大支持创建 **16000 维度**的向量，**最大支持对 2000 维度的向量建立索引**。」
> - 前提条件：「实例大版本为 **PostgreSQL 14 或以上**；实例内核小版本为 **20230430 或以上**（17 版本需 **20241030** 或以上）」
> - 支持 HNSW 与 IVFFlat，含 `vector_cosine_ops` / `vector_l2_ops` / `vector_ip_ops`
> - 创建：`CREATE EXTENSION IF NOT EXISTS vector;`（需高权限账号）

→ **1024 维完全在支持范围内**（存储上限 16000、索引上限 2000，余量充足）。

**(b) RDS PG 支持中文分词扩展 —— 确认，且有两个选择。** 官方插件页把 `zhparser`、`pg_jieba` 列在「**全文搜索**」类、且说明**必须加入 `shared_preload_libraries`**【官：[RDS PostgreSQL 支持的插件](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/extensions-supported-by-apsaradb-rds-for-postgresql)】。

`zhparser`（官方专页给了完整用法）【官：[中文分词（zhparser）](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/use-the-zhparser-extension-to-segment-chinese-text)】：

```sql
-- 前提：实例大版本 PG 10+，内核小版本 20230830+（PG17 需 20241030+）
--       并把 zhparser 加入 shared_preload_libraries
CREATE EXTENSION zhparser;
CREATE TEXT SEARCH CONFIGURATION testzhcfg (PARSER = zhparser);
ALTER TEXT SEARCH CONFIGURATION testzhcfg ADD MAPPING FOR n,v,a,i,e,l WITH simple;

-- 建 GIN 全文索引并查询
CREATE INDEX idx_t1 ON t1 USING gin (to_tsvector('testzhcfg', upper(name)));
SELECT * FROM t1 WHERE to_tsvector('testzhcfg', upper(t1.name)) @@ to_tsquery('testzhcfg', '(防火)');

-- 自定义词典（最多 100 万条，每词最长 128 字节）
INSERT INTO pg_ts_custom_word VALUES ('保障房资');
SELECT zhprs_sync_dict_xdb();   -- 之后需重连生效
```

`pg_jieba` 同样有官方专页，并要求加入 `shared_preload_libraries`【官：[使用 pg_jieba 插件](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/use-the-pg-jieba-extension-on-an-apsaradb-rds-for-postgresql-instance)】。

→ 「向量 + 中文检索 + HA」三件事在一个托管数据库里基本解决（**「严格 BM25」的附加条件见下方 (f)**），且**不需要自研 Python BM25，也不需要引入 Milvus**——但 BM25 排序的成立与否需先落实 (f) 的待核实项。

**(c) RDS PG 自身 HA 是买来的 —— 确认。**【官：[高可用和容灾设计](https://help.aliyun.com/zh/rds/product-overview/high-availability-and-disaster-recovery)】：

> - 高可用系列「采用**一主一备**的双机热备架构…主节点故障时，**主备节点秒级完成切换**，整个切换过程对应用透明；备节点故障时，RDS 会自动新建备节点以保障高可用。」
> - 「**多可用区实例**（也称为同城双机房或者同城容灾实例）：主备节点位于同一地域的不同可用区，提供跨可用区的容灾能力，且**不额外收费**。」
> - 集群系列：一主多备，**备节点可读**。
> - 备份/PITR：「RDS 默认支持按备份集和指定时间点进行数据恢复…可以将 **7 天内**任意一个时间点的数据恢复」。

**(d) 一个必须点破的诱惑：ParadeDB 装不到 RDS 上 —— 判断成立。** RDS 只允许安装其**官方支持列表**内的插件（`SELECT * FROM pg_available_extensions` 可查当前实例可用插件），且部分插件因安全风险被限制创建；`pg_search` / ParadeDB **不在列表中**（其发行形态是独立镜像，非 RDS 可插拔扩展）【官：RDS 插件页 + [ParadeDB K8s 部署](https://www.paradedb.com/docs/operate/deploy/self-hosted/kubernetes)】。
更重要的是：**ParadeDB Community 本身官方定位为单节点**，HA 需付费 Enterprise【官：[ParadeDB HA](https://www.paradedb.com/docs/operate/deploy/self-hosted/high-availability)】。**所以这条路要的是「vanilla pgvector + RDS 自带中文分词」，与 ParadeDB 无关。**

**(e) 成本与工作量**：需要**新增一个 RDS PostgreSQL 实例**（或把现有 MySQL 迁到 PG）。RDS PG 实例价格本文**未查到具体数字**（§九.4）。工作量在于：重写 `src/infra/db/vector_store/` 适配层、去掉全局 RLock、把 BM25 从进程内 Python 换成 SQL 全文检索、数据迁移。这是本文所有方案里**代码改动最大**的一个，但也是**唯一一个把三件事一次性做对**的。

**(f) 补充核实（2026-09-18）：「中文 BM25」在 RDS PG 上到底怎么成立 —— 关键是 `pg_textsearch`**

> ⚠ 先修正 (b) 的措辞，避免把三件事说成一件事：**PostgreSQL 内置全文检索的排序函数是 `ts_rank` / `ts_rank_cd`（覆盖密度类），并不是 BM25**【官：[PG 全文检索函数](https://www.postgresql.org/docs/current/functions-textsearch.html)】。因此「RDS PG + `zhparser`/`pg_jieba`」单独只给到 **中文全文检索 + 内置相关性排序**，严格说不等于「中文 BM25」。下面这条才是让「中文 BM25」真正成立的一手依据。

**RDS 插件列表里另有 `pg_textsearch`（Timescale 出品，真 BM25，列表版本 1.3.1，仅 PG 17 / 18）**【官：[RDS PostgreSQL 支持的插件](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/extensions-supported-by-apsaradb-rds-for-postgresql)】。其上游官方 README 的「Chinese Full-Text Search」样例**直接组合 `zhparser`** 做中文 BM25【官/上游：[timescale/pg_textsearch](https://github.com/timescale/pg_textsearch)】：

```sql
CREATE EXTENSION zhparser;
CREATE TEXT SEARCH CONFIGURATION public.chinese (PARSER = zhparser);
ALTER TEXT SEARCH CONFIGURATION public.chinese ADD MAPPING FOR n,v,a,i,e,l WITH simple;

CREATE INDEX chinese_documents_bm25 ON chinese_documents
    USING bm25 (content) WITH (text_config='public.chinese');

SELECT id FROM chinese_documents
ORDER BY content <@> to_bm25query('机器学习', 'chinese_documents_bm25') LIMIT 10;
```

即：**同一份 `text_config` 同时用于索引与查询分词**，`k1`（默认 1.2）/ `b`（默认 0.75）可调，官方称其为「true BM25 ranking」。这把上面 (b) 的「中文分词」和真正的 BM25 排序缝合了起来。

> ⚠ **两个已知边界**（**必须**在动手前确认，见 §九.14）：
> 1. `pg_textsearch` 需写入 `shared_preload_libraries`，而 RDS「需要预加载的插件」清单里当时只列了 `pg_bigm` / `zhparser` / `pg_jieba`，**未列 `pg_textsearch`**。RDS 上能否为它配置预加载、能否与自定义 `chinese` 配置组合，**本次未核实**。若不能，则 RDS PG 上的相关性排序回落到 `ts_rank`，「严格 BM25」不成立。
> 2. 该扩展**仅支持 PG 17/18**，选实例大版本时需注意（pgvector 只要求 PG 14+，zhparser/pg_jieba 只要求 PG 10+）。

【推】修正后的准确表述是：**RDS PG 这条路在「中文全文检索」上确定成立（`zhparser`/`pg_jieba` 官方支持），在「严格 BM25」上取决于 `pg_textsearch` 的可用性且需 PG 17/18**。§一 与 §八 的推荐不因此改变（方向仍是「把向量与中文检索挂到托管关系库」），但**「中文 BM25」四个字应以上述条件为准**。

**(g) 补充核实：`pg_jieba` / `zhparser` 与 Milvus `chinese` analyzer 的分词口径差异**（回应 §九.2）

Milvus `chinese` analyzer 的官方定义【官：[Chinese analyzer](https://milvus.io/docs/chinese-analyzer.md)】：Tokenizer = `jieba`，Filter = `cnalphanumonly`，等价配置 `{"tokenizer":"jieba","filter":["cnalphanumonly"]}`，不接受其他可选参数、不输出拼音。官方示例：

```
输入：'Milvus 是一个高性能、可扩展的向量数据库！'
输出：['Milvus','是','一个','高性','性能','高性能','可','扩展','的','向量','数据','据库','数据库']
```

| 维度 | Milvus `chinese` analyzer | RDS PG：`zhparser` / `pg_jieba` |
|---|---|---|
| 分词算法 | jieba | zhparser=SCWS；**pg_jieba=jieba（与 Milvus 同源）** |
| 输出形态 | **纯 token 列表**（供 BM25 统计词频） | **`tsvector`**（lexeme + 位置、去重并按字典序），如 `'中国科学院':5 ...`【官：zhparser 页】 |
| 标点处理 | `cnalphanumonly` 过滤（官方描述为「remove tokens that contain any non-Chinese characters」，但示例仍保留 `Milvus` 这类字母数字 token） | zhparser 只映射 `n,v,a,i,e,l`，通常不含标点；pg_jieba 官方示例里出现 `'，':7` 这类 token |
| 大小写 | 默认不 lowercase（需另加 filter） | PG `simple` 字典对 ASCII 默认转小写 |
| 查询侧 | 同一 analyzer 处理查询文本 | `to_tsquery` 支持 `&`/`|`/`!`/`<->`/前缀 `:*`；pg_textsearch 用 `to_bm25query` |
| 重叠 token | 示例同时输出 `高性`/`性能`/`高性能` | `tsvector` 去重合并为一个 lexeme + 位置列表 |

【推】`pg_jieba` 与 Milvus 同用 jieba，**切分算法本身同源**；差异主要在后处理层——Milvus 走 analyzer filter，PG 走 text search 字典 + `tsvector` 规范化（去重、位置、大小写），并叠加 `tsquery` 的布尔/前缀能力。**换方案后同一句中文的命中集合可能不同，需按实际语料重新调参，不能假设行为等价。**

另：RDS 官方两个专页给出的**版本/内核门槛**为——`zhparser` PG 10+（内核小版本 20230830+，PG17 需 20241030+）、`pg_jieba` PG 10+（PG17 需 20241030+），两者都需加入 `shared_preload_libraries`【官：两专页，见附录】。

### 6.4 阿里云 Elasticsearch（dense_vector + IK）

- **向量**：官方示例直接用 `dense_vector`，并明确「dims…**Maximum is 1024 when `index: true`**」——我们的 1024 维**刚好卡在上限**，没有余量【官：[使用阿里云百炼向量化文本数据](https://help.aliyun.com/en/es/user-guide/vectorization-of-text-data-through-alibaba-cloud)】。官方示例里索引设置同时写了 `"number_of_shards": 3, "number_of_replicas": 1`，即 ES 的 HA 由分片副本承担。
- **中文 BM25**：IK 分词插件是**阿里云 ES 提供的中文分词扩展插件，内置默认词典可直接使用**，支持 `ik_max_word` / `ik_smart`，主词典含 27 万余词，且支持从 OSS 动态加载词典【官：[使用 IK 分词插件](https://www.alibabacloud.com/help/zh/es/user-guide/use-the-analysis-ik-plug-in)】。
- **评价**【推】：技术上成立（向量 + 中文 BM25 + 副本 HA 都有），但为了 164 MB 数据引入一套 ES 集群，**资源与运维重量都远超 RDS PG**；且维度刚好顶到 1024 上限，未来换更大维度的 embedding 模型会直接撞墙。列为**第三候选**。

### 6.5 Zilliz Cloud（原厂托管，对照）

官方定价页【官：[zilliz.com/pricing](https://zilliz.com/pricing)】：

| 套餐 | 起步价 | HA 相关原文 |
|---|---|---|
| Free | $0（5 GB / 2.5M vCU / 最多 5 collections） | — |
| Standard | Serverless $0 起；Dedicated **$126/GB/月** | 「Best for: **prototypes and testing** environments」 |
| **Enterprise** | Dedicated **$197/月** 起 | 「**99.95% uptime SLA**」「**Multi-replica** and elastic scaling」「Best for **production**」 |
| Business Critical | 面谈 | 「Global cluster with high-level availability and disaster recovery」 |

容量口径（768 维）：Performance-optimized 每 CU 200 万向量、Capacity-optimized 每 CU **800 万向量**、Tiered-storage 每 CU 4000 万向量。Uptime SLA 行另注「**99.99% if multi-replica is enabled**」。

【推】本量级（4 万向量）用 Capacity-optimized 的**最小 1 CU 即够**，官方口径 from $16/百万向量/月，但**最低消费与是否能在国内合规落地未核实**（§九.5）。作为对照项列出，不作为推荐。

### 6.6 自建 Milvus Cluster：在「2 台机」前提下直接出局

官方 Helm 部署后应看到的 Pod 清单【官：[install_cluster-helm](https://milvus.io/docs/install_cluster-helm.md)】：

```
etcd-0/1/2                              （3 副本，元数据）
minio-0/1/2/3                           （4 副本，对象存储）
woodpecker-0/1/2/3                      （4 副本，WAL/MQ；官方明示不得低于 3）
milvus-mixcoord / datanode / querynode / proxy / streamingnode
```

且 **Milvus Distributed 必须部署在 Kubernetes 上**；官方最低 **8 GB / 4 核**、推荐 16 GB / 8 核（单节点口径）【官：[install-overview](https://milvus.io/docs/install-overview.md)、[prerequisite-docker](https://milvus.io/docs/zh/prerequisite-docker.md)】。

【推】要「扛一台机器永久故障」，K8s 至少要有 3 个可调度节点（etcd 多数派、Woodpecker quorum=3），**2 台机器不满足**；即便强行挤进 2 台，etcd/Woodpecker 的 quorum 也会因一台下线而失去多数派。**结论：自建 Milvus Cluster 与本项目「2 台机」的约束不相容。**

---

## 七、对象存储：MinIO → 阿里云 OSS（与向量库分开评估）

> 先把 team-lead 更正的判断确认一遍：**MinIO 就是文档上传的存储后端，不是 Langfuse 专属**【项，已核实】：
> - `src/infra/db/file_store.py:3,14-21` 用 `minio` Python SDK，endpoint/bucket 来自 `settings.MINIO_ENDPOINT` / `settings.MINIO_DOC_BUCKET`
> - `src/config/settings.py:253-258`：注释即写「对象存储服务，**用于持久化原始文档文件**」，`MINIO_DOC_BUCKET="documents"`
> - `src/infra/db/file_store.py:30-31`：key 格式 `documents/{user_id}/{kb_id}/{doc_id}/{filename}`
> - `docker-compose.yml:97-120`：`minio` 服务**没有 `profiles` 键 → 默认启动**，被 app 与 Langfuse 共用
>
> 因此「文件走 OSS」= 把**上传的原始文档**从小块本地盘迁到 OSS，与 Langfuse 无关。

**为什么这件事的重要性不低于向量库**：chunk 正文只在 Chroma，MySQL 只存文档元数据（`src/infra/db/models/document.py` 无正文字段）；**原始文件丢了，向量索引就无法重建**【项 + 推】。即：**MinIO 是「不可再生的源头」，向量库只是「可再生的派生物」。**

### 7.1 S3 兼容性（一手依据）

OSS 官方明确「OSS 对 S3 Bucket、Object 以及 Multipart 操作兼容的 API 如下」【官：[OSS 兼容的 S3 API 及与 S3 的差异](https://help.aliyun.com/zh/oss/developer-reference/compatibility-with-amazon-s3)】，与 `FileStore` 所需调用一一对应：

| `FileStore` 调用 | 底层 S3 操作 | OSS 是否在兼容清单 |
|---|---|---|
| 构造时 `_ensure_bucket()` → `bucket_exists()` | **HeadBucket** | ✅ |
| 构造时 `_ensure_bucket()` → `make_bucket()` | **PutBucket** | ✅ |
| `upload()` → `put_object()` | **PutObject**（大文件走 Multipart） | ✅ |
| `download()` → `get_object()` | **GetObject** | ✅ |
| `delete()` → `remove_object()` | **DeleteObject** | ✅ |

**已明确的差异（会影响迁移）**：

1. **请求风格**：原文「基于安全考虑，OSS **仅支持虚拟托管访问方式**，即 Bucket 名称必须作为子域名使用…否则可能导致 OSS 报错，并禁止访问。」→ **不能用路径风格**。S3 兼容端点官方格式为外网 `https://s3.oss-{region}.aliyuncs.com`、内网 `https://s3.oss-{region}-internal.aliyuncs.com`（生产走内网，流量免费且不经公网）【官：[使用 AWS SDK 访问 OSS](https://help.aliyun.com/zh/oss/developer-reference/use-aws-sdks-to-access-oss)】。
2. **ETag**：PUT 上传的 ETag **OSS 为大写、S3 为小写**（做校验需忽略大小写）；**分片上传的 ETag 计算方式与 S3 不同**。
3. **ACL 定义不完全一致**；OSS 仅支持私有 / 公共读 / 公共读写三种模式。
4. `x-oss-process` 参数仅支持 `image/` 与 `style/`（与文档上传无关）。

**`minio` Python SDK 能否直接指向 OSS？——比原先预期乐观，但仍需冒烟验证（补充核实 2026-09-18）。**

本项目实际安装的是 **minio-py 7.2.20**（非 boto3）。查其源码有两条关键事实：

1. **minio-py 对 `aliyuncs.com` 域名有内建适配**：`BaseURL` 构造时按 `hostname.endswith("aliyuncs.com")` **自动启用虚拟托管风格**（源码 `self._virtual_style_flag = (self._aws_info is not None or hostname.endswith("aliyuncs.com"))`）【一手：`.venv/lib/python3.12/site-packages/minio/helpers.py:561-563`】。这正好满足上条「OSS 只支持虚拟托管风格」的要求。
2. **minio-py 的 V4 签名使用 `UNSIGNED-PAYLOAD`，不启用 `aws-chunked` 传输编码**【一手：`.venv/.../minio/signer.py:298-305`】。官方文档指出 OSS **不支持 chunked encoding**，**boto3 因 V4 实现与 chunked encoding 强耦合而必须改用 V2 签名**、否则报 `InvalidArgument: aws-chunked encoding is not supported`【官：同上「使用 AWS SDK 访问 OSS」的常见问题】。minio-py 不存在这一耦合。

【推】对 §7.1 表中所列的 5 个操作，**minio-py 很可能只需改 Endpoint / AK / SK / Bucket（并把 `secure` 设为 `True`）即可直连 OSS**。但**尚未实测**（§九.6），落地前应先用 `bucket_exists()` + 一次上传/下载做冒烟验证。若实测不通过，改用阿里云官方 `oss2` SDK：`put_object`/`get_object`/`delete_object`/`bucket.exists()` 与原 S3 语义一一对应，改动面收敛在 `src/infra/db/file_store.py` 这一个约 60 行的类（替换 import、构造、4 个方法体，并把 `S3Error` 换成 `oss2.exceptions.OssError`），对外接口不变。

### 7.2 迁移路径的粒度与那个「坑」

**若 S3 兼容端点可用**，迁移粒度确实很小【推】：
- 配置层面：`MINIO_ENDPOINT` → OSS 地域域名、`MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` → RAM 子账号 AK/SK、`MINIO_DOC_BUCKET` → 目标 Bucket 名（共 4 项，均在 `src/config/settings.py`）；
- 代码层面：若 minio-py 兼容则**零改动**；若不兼容则替换 `FileStore` 内部实现（对外接口不变）；
- 数据层面：把既有 `documents/` 前缀下的对象搬到 OSS（`ossutil` / SDK 均可），并保持 key 路径不变，避免 `file_path` 字段失效。

**⚠️ `_ensure_bucket()` 的权限坑（确认存在）**【项：`src/infra/db/file_store.py:24-27`】：

```python
def _ensure_bucket(self) -> None:
    if not self._client.bucket_exists(self._bucket):
        self._client.make_bucket(self._bucket)   # ← 需要 oss:PutBucket
```

构造 `FileStore()` 时会自动建桶。云端对象存储的常规做法是**由管理员预建 Bucket**，应用侧只给 `oss:PutObject` / `oss:GetObject` / `oss:DeleteObject` / `oss:ListObjects`（以及 `HeadBucket` 所需的桶级读权限）这类最小权限，**不给 `oss:PutBucket`**。官方 RAM 场景示例印证了这一点：读取目录的最小授权是 `oss:ListObjects`（桶级 Resource）+ `oss:GetObject`（对象级 Resource），写删是 `oss:PutObject` / `oss:DeleteObject`（对象级），**均不含 `oss:PutBucket`**【官：[RAM Policy 与场景示例](https://help.aliyun.com/zh/oss/user-guide/ram-policy/)，场景 3 / 场景 9】。

**失败面比「缺 PutBucket」更大**（补充核实 2026-09-18，查 minio-py 源码）：`bucket_exists()` 在捕获 `S3Error` 后**仅当错误码为 `NoSuchBucket` 时返回 `False`，其他任何错误码都会重新抛出**【一手：`.venv/.../minio/api.py:685-705`】。于是：

| 场景 | 结果 |
|---|---|
| 桶已预建 + RAM 有桶级读权限 | `bucket_exists` 返回 True → 不调 PutBucket → **正常** |
| **RAM 未授予桶级读权限**（AccessDenied） | `bucket_exists` **抛异常** → `FileStore.__init__` 抛出 → **应用启动即失败** |
| 桶不存在 + RAM 无 `oss:PutBucket` | 调 `make_bucket` → AccessDenied → **应用启动即失败** |

即缺**桶级读权限**与缺**建桶权限**都会在 `FileStore()` 构造阶段**直接抛错**，**属于启动期故障，不是运行期**。

**建议处理**：把 `_ensure_bucket()` 改为「预检 + 友好报错」或直接移除自动建桶逻辑，改由部署脚本/控制台预建。改动量约 3–5 行。

### 7.3 结论

**「对象存储迁到 OSS 即可获得托管 HA」——成立。** 依据更硬的一手来源【官：[OSS 存储冗余类型](https://help.aliyun.com/zh/oss/user-guide/overview-of-storage-redundancy-types/)】：

- **同城冗余存储（ZRS）** 把数据冗余在**同一地域的 3 个及以上可用区**（可用区仅 2 个的地域则双可用区），**某个可用区不可用时仍能正常访问**；标准存储的数据持久性官方标为 **99.9999999999%（12 个 9）**。
- **本地冗余存储（LRS）** 为单可用区、11 个 9。**生产应选 ZRS**。
- （旁证）阿里云 Milvus 产品页亦写明业务数据交由 OSS 保证、提供「同城多副本冗余机制」【官：[阿里云 Milvus 产品页](https://www.aliyun.com/product/milvus)】。

迁移后 **MinIO 这个单点消失**，且这是**收益确定、改动最小**的一格。

**成本量级（官方单价，补充核实 2026-09-18）**【官：[OSS 价格详情](https://www.aliyun.com/price/detail/oss)】：中国内地按量付费标准存储 **本地冗余 0.12 元/GB/月、同城冗余 0.15 元/GB/月**；**内网流入/流出流量免费**（同地域 ECS 走内网 Endpoint，无流量费）；PUT 请求每月每地域前 500 万次免费、GET 前 2000 万次免费。按千级文档、原始文件总量按数 GB 估，**存储费为每月个位数元**量级。对比 §6 各托管向量库的数千元/月，**这一格的性价比没有争议**。

---

## 八、推荐（针对「2 台机 + 阿里云 + 千级文档 + 企业内网」）

**排序如下，每一档都给出触发条件，不要求一次做完。**

**第 0 档（立刻做，成本≈0）——先把「可恢复」和「源头」做扎实**
- **迁 MinIO → OSS**（§七）：收益确定、改动最小、消除一个真实单点。同时修掉 `_ensure_bucket()` 的权限隐患。
- 补一个**索引重建脚本**（OSS 原文 → 解析 → 嵌入 → 建库）并**实际演练一次**，把实测 RTO 写进运维手册。理由：向量库是可再生数据，有了重建能力，向量库自身的 HA 需求会大幅下降。
- 明确写下 RPO/RTO 目标。**若业务能接受「机器坏了，当天恢复」，到这里就可以停。**

**第 1 档（向量库要 HA 的正解）——`RDS PostgreSQL + pgvector + pg_jieba/zhparser`**
- 触发条件：明确要求「一台机器故障时向量检索不中断」。
- 收益：**三件事一次性解决**（向量 / 中文检索 / HA），全部是托管能力，无需自建任何新组件。**注意口径**：严格 BM25 需 `pg_textsearch`（PG 17/18，RDS 可预加载性待核实，见 §6.3(f)）；否则是「中文全文检索 + `ts_rank`」。
- 代价：新增一个 RDS PG 实例；重写向量适配层、去掉全局 RLock、BM25 改用 SQL 全文检索、数据迁移。**这是本文改动量最大的方案，也是唯一「结构性正确」的方案。**
- 注意：**ParadeDB 在 RDS 上装不了**，要的是 vanilla pgvector + RDS 自带中文分词；v1 的 Python BM25 + RRF 融合逻辑可以保留在应用层，也可以下推为 SQL。

**第 2 档（不想碰 PG 时的托管向量库）——`DashVector + DashText`**
- 触发条件：接受「少数 collection + `kb_id` 过滤」的数据模型重构（现状 691 个 collection，而付费实例上限 32 个）。
- 成本可算：S.small × 2 副本 ≈ ¥365/月。
- 风险点：读写分离导致的**写入后短暂不可见**（官方明示最终一致），需实测对「上传后立刻提问」体验的影响；Serverless 型的写请求单元成本未核实。

**明确不推荐**
- **自建 Milvus Cluster**：2 台机不满足 etcd/Woodpecker 多数派要求。
- **阿里云 Milvus 入门版（单机版）**：它本身就是单点，用它解 HA 是自相矛盾；标准版 + 高可用/多可用区虽能解决，但官方计费示例已在 ¥7144/月量级，与本项目 164 MB 数据严重不匹配。
- **共享存储 + Chroma 双机接管**：被 SQLite 官方明确否定（§五）。
- **继续只用 Chroma 并期待「换库」解决**：Chroma 开源版无复制，任何单机形态都不解决机器故障。

**如果最终选择「不换库、只做备份+重建」**——这也是一个可接受的答案，但请把它写成**明确的决策**（写清 RPO/RTO 与演练结果），而不是默认状态。

---

## 九、未核实项

1. **RDS PG 支持列表的完整性与版本矩阵**：官方插件页很长，本次抓取在 `hll` 处截断；`zhparser` / `pg_jieba` 的存在由该页「需要预加载的插件」清单确认，`pgvector` 由专页确认，`pg_textsearch` 的版本行（PG 18 / 17 = 1.3.1，其余版本「无」）在表头附近可见。但**「标准版 vs 倚天版」两个标签页的完整差异未逐条核对**。
2. ~~**`pg_jieba` 与 `zhparser` 的分词口径差异**~~ → **已补（见 §6.3(g)）**：`zhparser` 基于 SCWS、`pg_jieba` 基于 jieba（与 Milvus `chinese` analyzer 同源），差异主要在后处理层（PG 走 `tsvector` 规范化 + 字典，Milvus 走 analyzer filter）。**仍未核实**：两个扩展在 RDS 上的具体版本号、以及同一语料下的实测命中差异。
3. **阿里云 Milvus 的 SLA 口径冲突**：FAQ 说公测期间无 SLA，产品页/多可用区页给 99.9%–99.99%，计费页已商业化。**最新口径未核实**。
4. **RDS PostgreSQL 实例价格**：未查（未打开 RDS 计费/规格页），因此「RDS PG」一格的成本量级只能标注为「与同规格 RDS 实例相当」。
5. **Zilliz Cloud 在国内的可用性与合规**：zilliz.com.cn 存在，但**未核实**其与 zilliz.com 套餐/计费的关系、是否可在企业内网合规使用。
6. **`minio` Python SDK 对接阿里云 OSS 的可行性**：**已大幅降低不确定性但未实测**（补 §7.1）——minio-py 7.2.20 源码显示其对 `aliyuncs.com` 域名自动启用虚拟托管风格、且签名用 `UNSIGNED-PAYLOAD` 不启用 OSS 不支持的 chunked encoding；因此对本文用到的 5 个操作很可能零代码改动。**残留下限未知项**：未做真实 OSS 冒烟验证（`bucket_exists` + 上传/下载），以及分片上传（大文件）路径未验证。
7. ~~**阿里云 OSS 单价**~~ → **已补（见 §7.3）**：标准存储本地冗余 0.12、同城冗余 0.15 元/GB/月，内网流量免费，PUT/GET 有免费额度。**仍未核实**：阿里云官方对「NAS 上跑 SQLite」的明确警告（SQLite 官方页已足够作为否决依据），以及 `ossfs` 官方对数据库用途的明确不建议说明。
8. **阿里云 ES 的价格与最小可用规格**：未查；其 `dense_vector` 索引维度上限 1024 与本项目维度**刚好相等**（无余量）这一点已确认。
9. **DashVector Serverless 型的写请求单元换算**：官方按「写请求单元」计费，**4 万条 1024 维向量的一次性写入成本未核实**。
10. **DashVector 的数据导出/备份能力**：官方计费页提到「停服 7 天后释放，数据不可恢复」，但**未核实**其是否提供导出/备份 API（若没有，则 DashVector 自身又引入一个新的数据源单点）。
11. **阿里云 OpenSearch 向量检索版**：本次只抓到一篇快速开始（含数据分片、副本、内置 embedding 模板），**HA 与计费细节未核实**，故未纳入对照表。
12. **RDS PG 的物理复制是否会连同 pgvector 索引一起复制到备节点**：**未核实**。物理复制的常规语义是字节级一致【推】，但 ParadeDB 官方称其索引需 Enterprise 才能物理复制，提示「向量/全文索引的复制」可能不是所有扩展都天然成立——这一条在动手前**必须查证**（若 pgvector 索引在备节点需要重建，RTO 会变长）。
13. **app 层双副本的可行性**：本文只指出「流式状态在进程内 + 单 worker」这一约束【项：CLAUDE.md】，**未评估**改造代价。
14. **`pg_textsearch` 在 RDS 上的可用性（新增，2026-09-18）**：RDS 插件列表含 `pg_textsearch`（v1.3.1，仅 PG 17/18），其官方样例可组合 `zhparser` 做中文 BM25；但该扩展需写入 `shared_preload_libraries`，而 RDS「需要预加载的插件」清单**未列它**。**未核实**：能否为它配置预加载、能否使用自定义 `chinese` text search 配置、以及其 BM25 索引是否随物理复制到备节点。**这一条直接决定「RDS PG 上的中文 BM25」是否成立**，动手前应开 RDS 实例实测或提工单确认。
15. **`pg_textsearch` 索引的复制语义**：与 §九.12（pgvector 索引是否随物理复制）同类风险。上游 README 有 `bm25_pending_free_pages()`「awaiting standby-safe reclaim」字样，提示其具备备节点感知设计【官/上游：[timescale/pg_textsearch](https://github.com/timescale/pg_textsearch)】，但**未核实**其索引在 RDS 备节点是否可用、切换后是否需要重建。

---

## 附：本文关键一手来源

| 主题 | 来源 |
|---|---|
| 阿里云 Milvus 产品定位、99.9% 可用性、计费构成 | https://www.aliyun.com/product/milvus |
| 阿里云 Milvus 多可用区基础版 vs 高可用版（RPO/RTO/SLA/成本） | https://help.aliyun.com/zh/milvus/product-overview/multi-zone-basic-edition-vs-high-availability-edition |
| 阿里云 Milvus 计费项（CU 单价、存储单价、计费示例） | https://help.aliyun.com/zh/milvus/product-overview/billing-item |
| 阿里云 Milvus 资源估算（高可用=节点双副本、资源×2） | https://help.aliyun.com/zh/milvus/user-guide/milvus-resource-estimation-and-configuration-recommendations |
| 阿里云 Milvus FAQ（入门版=单机版 / 标准版=集群版） | https://help.aliyun.com/zh/milvus/support/faq |
| 阿里云 Milvus 公测说明（500 万门槛、地域可用区） | https://help.aliyun.com/zh/milvus/product-overview/vector-retrieval-service-milvus-version-free-public-test-instructions |
| DashVector 产品规格（容量、性能、**副本数 1-5**） | https://help.aliyun.com/document_detail/2636861.html |
| DashVector 产品计费（单价、公式、Serverless） | https://help.aliyun.com/document_detail/2510232.html |
| DashVector 约束与限制（**32 个 Collection**、维度、一致性） | https://help.aliyun.com/document_detail/2510263.html |
| DashText（BM25 稀疏向量 + **中文 Wiki 语料 + Jieba 分词**） | https://www.alibabacloud.com/help/zh/vrs/latest/dashtext |
| RDS PostgreSQL 支持插件列表（zhparser/pg_jieba 需预加载） | https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/extensions-supported-by-apsaradb-rds-for-postgresql |
| RDS pgvector 使用指南（PG14+、16000 维存/2000 维索引、HNSW/IVFFlat） | https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/pgvector-use-guide |
| RDS zhparser 中文分词（用法、GIN 索引、自定义词典） | https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/use-the-zhparser-extension-to-segment-chinese-text |
| RDS pg_jieba 中文分词 | https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/use-the-pg-jieba-extension-on-an-apsaradb-rds-for-postgresql-instance |
| RDS 高可用与容灾（一主一备、秒级切换、多可用区不额外收费、PITR 7 天） | https://help.aliyun.com/zh/rds/product-overview/high-availability-and-disaster-recovery |
| 阿里云 ES dense_vector（index:true 上限 1024 维、含 replicas 配置示例） | https://help.aliyun.com/en/es/user-guide/vectorization-of-text-data-through-alibaba-cloud |
| 阿里云 ES IK 分词插件（内置词典、ik_max_word/ik_smart、27 万词） | https://www.alibabacloud.com/help/zh/es/user-guide/use-the-analysis-ik-plug-in |
| OSS 兼容的 S3 API 清单与差异（仅虚拟托管风格、ETag 大小写） | https://help.aliyun.com/zh/oss/developer-reference/compatibility-with-amazon-s3 |
| OSS 使用 AWS SDK 访问（S3 兼容 Endpoint 格式、boto3 需 V2 签名 / chunked encoding 不兼容） | https://help.aliyun.com/zh/oss/developer-reference/use-aws-sdks-to-access-oss |
| OSS 存储冗余类型（ZRS 多可用区、持久性 12 个 9） | https://help.aliyun.com/zh/oss/user-guide/overview-of-storage-redundancy-types/ |
| OSS 价格详情（标准存储 LRS 0.12 / ZRS 0.15 元/GB/月、内网流量免费、请求免费额度） | https://www.aliyun.com/price/detail/oss |
| OSS RAM Policy 与场景示例（最小权限不含 PutBucket） | https://help.aliyun.com/zh/oss/user-guide/ram-policy/ |
| minio-py 源码：`aliyuncs.com` 自动虚拟托管风格 | `.venv/lib/python3.12/site-packages/minio/helpers.py:561-563` |
| minio-py 源码：V4 签名 `UNSIGNED-PAYLOAD`（不启用 chunked） | `.venv/lib/python3.12/site-packages/minio/signer.py:298-305` |
| minio-py 源码：`bucket_exists()` 非 NoSuchBucket 错误码会重新抛出 | `.venv/lib/python3.12/site-packages/minio/api.py:685-705` |
| PostgreSQL 全文检索函数（`ts_rank`/`ts_rank_cd`，非 BM25） | https://www.postgresql.org/docs/current/functions-textsearch.html |
| pg_textsearch 上游（真 BM25；**中文 BM25 用 zhparser 作 text_config** 的官方样例；仅 PG17/18） | https://github.com/timescale/pg_textsearch |
| Milvus `chinese` analyzer（jieba + cnalphanumonly、输出示例） | https://milvus.io/docs/chinese-analyzer.md |
| **SQLite 官方：网络文件系统上的风险与替代方案** | https://www.sqlite.org/useovernet.html |
| Zilliz Cloud 定价与 HA/SLA | https://zilliz.com/pricing |
| ParadeDB Community 单节点 / Enterprise 才有 HA | https://www.paradedb.com/docs/operate/deploy/self-hosted/high-availability |
| Milvus Helm 集群 Pod 清单与 Woodpecker 副本下限 | https://milvus.io/docs/install_cluster-helm.md |
| Milvus 部署形态（Standalone 单机 / Distributed 需 K8s） | https://milvus.io/docs/install-overview.md |
| Chroma 无复制、server 模式扩展边界 | https://cookbook.chromadb.dev/core/system_constraints/ |
| Chroma 多进程共享 persist_dir 挂起 | https://github.com/chroma-core/chroma/issues/7040 |
| 项目：MinIO 文档存储与自动建桶 | `src/infra/db/file_store.py:3,14-21,24-27,30-31` |
| 项目：MinIO 配置 | `src/config/settings.py:253-258` |
| 项目：MySQL 不存 chunk 正文 | `src/infra/db/models/document.py:12-30` |
| 项目：MinIO 服务默认启动（无 profiles） | `docker-compose.yml:97-120` |
| 项目：单 worker 部署形态 | `CLAUDE.md`「规则 → 部署形态」 |
| 相邻分析（量级、内存、成本细节） | [comparison_milvus_vs_chroma.md](./comparison_milvus_vs_chroma.md) |
