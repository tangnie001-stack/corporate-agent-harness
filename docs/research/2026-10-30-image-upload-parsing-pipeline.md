# 知识库上传图片的解析链路：业界做法

> 调研问题：知识库支持图片上传时整条链路是什么？业界是否 OCR 记录、是否用多模态大模型兜底？解析产物是什么数据、怎么喂给检索？图片本身是表格时，识别后是否按 chunk 切？
> 日期：2026-10-30　来源见文末（均为一手文档/论文/厂商文档）

## 一、结论先行

1. **链路固定为五段**：上传 → 版面分析（detect）→ 内容识别（OCR / TSR / VLM）→ 统一成"结构化文本 + 几何 + 资源引用"的中间产物 →（切块 + 向量化）→ 检索 → **生成期再把原图送一次多模态模型**。
2. **业界不是"OCR 或 VLM 二选一"，是分工**：文字/表格走确定性识别（OCR + 表格结构识别），图形/图表/照片走 VLM 生成描述。RAGFlow 的做法最典型：OCR 与版面识别打底，`figure_parser.py` 单独用 vision LLM 给图配描述。
3. **"多模态兜底"真实存在，但兜的是"非文本内容"，不是"OCR 失败"**：Azure 的参考实现就是 layout 检出 figure → 裁图 → GPT-4V 描述 → 把描述回写进 Markdown 的图片占位符。
4. **解析产物不是图片，也不是纯文本，是"元素列表"**：每个元素带 type（text/table/figure）、bbox、页码、父子关系；表格转 HTML/Markdown 字符串，图片转"描述文本 + 资源 URI（或 base64）"。检索走的是这些**文本表示**的向量；原图只在生成期被取用。
5. **图片是表格时不按字符切**：要么整表一个块，要么按行/行组切（row-level、键值块、层级行树），要么"双表示"（表格 HTML 存原文 + LLM 摘要做检索向量）。字符窗口切分是反面教材——表头和表体被切开后列语义直接丢失。
6. **另一条完全不同的路线**：ColPali 类模型不做 OCR，把整页当图片打成多向量（patch embeddings）直接检索。它绕过了上面的解析产物，但实测对"没见过的低质量文档"泛化不如 OCR 路线（arXiv 2505.05666）。

## 二、整条链路（以图片为最小输入）

| 段 | 做什么 | 产物 | 代表实现 |
|---|---|---|---|
| 1 上传 | 落对象存储（MinIO/S3），记 doc_id / 哈希 / 原始 URI；**原始文件必须保留**，后面裁图要用 | 文件对象 + 元数据行 | 通用 |
| 2 版面分析 | 检测 text / title / table / figure / selection mark 的区域 | 区域列表（type + bbox + 页码 + 阅读顺序） | Azure `prebuilt-layout`；RAGFlow `LayoutRecognizer`（YOLOv10） |
| 3 内容识别 | 文字 OCR；表格做 TSR（行/列/合并单元格恢复）；图形走 VLM | 文本 + 表格结构 + 图描述 | Azure 表格→HTML；RAGFlow `TableStructureRecognizer` |
| 4 组装 | 按阅读顺序拼成 Markdown/元素流，图插占位符，表插 HTML | 文档级结构化文本 | Azure `outputContentFormat=markdown` |
| 5 切块 + 向量化 | 结构感知切块，逐块 embed 入向量库 | chunks + embedding + 元数据 | LangChain 多向量；RAGFlow chunk template |
| 6 生成 | 检索命中后，把**原图**（不是描述）连同文本一起送 VLM 作答 | 回答 + 引用（页码/图号） | NVIDIA RAG Blueprint VLM |

关键点：第 5 段检索用的是**文字替身**，第 6 段回答用的是**原图本体**。NVIDIA 文档把这条讲得最直白——"ingestion 阶段给图配 caption 以便索引和被引用；查询阶段把被引用的图直接发给 VLM"。

## 三、OCR 还是多模态：三种分工形态

### 形态 A：OCR/TSR 打底 + VLM 只处理图（主流）
- RAGFlow DeepDoc 默认 PDF 路径：渲染页面图 → OCR 检测识别 → LayoutRecognizer 打版面标签 → TableStructureRecognizer 恢复表格结构 → XGBoost 模型判相邻 box 是否同一阅读序列；图形另由 `figure_parser.py` 用 vision LLM 富化。
- Azure layout：`figures` 对象给 bbox/spans/关联段落/caption，`output=figures` 额外返回裁好的图；表格在 Markdown 里用**完整 HTML 表格**（保留 rowspan/colspan 与 `<caption>`）。
- 注意一个易混点：Azure 的 `caption` 是**文档自带的图注**，不是模型生成的；"生成描述"是参考样例里另加的一步（GPT-4V，prompt 极简："Describe this image (note: it has image caption: {caption})"）。

### 形态 B：VLM 生成的自然语言摘要当检索替身
- Unstructured Workflow 的增强节点明确提供：图摘要、表摘要、表转 HTML、生成式 OCR 优化；再配 `chunk_by_contextual`（给每块前置 LLM 生成的上下文摘要）与 `chunk_by_table_merging`（跨页表合并）。
- 这一步的产物是"人话描述"，解决"图里的信息没法被文本向量检索到"。

### 形态 C：不做 OCR，整页视觉检索（ColPali / ColQwen / ColNomic）
- 页面图切 patch → 每 patch 一个向量（ColBERT 式多向量、late interaction），查询也是多向量。
- 优点：零解析、版式/图表/手写天然保留；缺点：存储与算力高、不能直接给出可引用的文本片段、**对分布外文档泛化弱**。arXiv 2505.05666 的系统对比结论就是这句：vision-based 在微调过的文档上更好，OCR-based 对未见过的、质量参差的文档更能泛化。

### "兜底"到底兜什么
- 兜**非文本模态**（图/图表/公式/手写），不是兜 OCR 报错。
- 真正的级联兜底（OCR 置信度低 → 转 VLM 重识别）在工业界更多是"整页走 VLM-OCR"的替代方案，而非逐行 fallback；因为逐行级联的判定阈值很难定，且延迟不可控。

## 四、解析产物长什么样、怎么喂检索

### 4.1 中间产物：元素 + 元数据
每个元素至少要有：`type`（text/table/figure）、`page`、`bbox`、`text`（或 html）、`resource_uri`（裁图后的对象存储地址）、`parent`（所属标题/章节）。有了 parent 才能做父子块（小块检索、大块喂模型）。

### 4.2 三类落库形态

| 内容 | 存什么 | 检索表示 | 生成时给什么 |
|---|---|---|---|
| 正文 | 文本 | 文本 embedding | 文本 |
| 表格 | **HTML 原文**（保结构）+ 可选 LLM 摘要 | 摘要 + 表头/上下文 的 embedding | HTML 或 Markdown 渲染 |
| 图片 | 原图（对象存储）+ **描述文本** | 描述的 embedding | 原图给 VLM（+ 描述做弱提示） |

### 4.3 检索怎么召回"图"
- 纯文本向量只能召回**描述**。所以描述质量直接决定召回率：描述里必须包含实体名、数字趋势、图表类型这些"用户会问的词"，否则图永远检索不到。
- 工程上的常见补丁：把图所在章节标题、图注、上下文段落一起拼进被 embed 的文本；或者对图块单独再 embed 一次"问题化的摘要"。
- 多表示（multi-vector）是 LangChain 的标准解法：`docstore` 存原始 HTML/图，`vectorstore` 只存摘要向量，用 id 关联；检索命中摘要 → 取回原始内容。
- 一个已知坑：**chunker 会丢图**。Unstructured issue #2674 就是"分块策略直接把 Image 元素丢弃"，社区只能靠预处理或后处理回填。自研时必须显式规定"图/表元素是原子块，不参与字符切分"。

### 4.4 生成期的第二次看图
NVIDIA 的做法是把"被引用的图"随问题一起送 VLM。收益是准确率，代价是延迟（文档明确写了 latency 上升）。所以图不是"检索完就扔掉"，要在 chunk 元数据里保留 URI 供生成期取回。

## 五、图片本身是表格时怎么切

先纠正一个前提：**表格不是"识别完再按 chunk 切"，而是"识别成结构化表示后，按结构单元切"**。字符窗口切分（固定 512 token 之类）会把表头与表体分离，列名丢失后这块数据不可用。

业界四种切法：

1. **整表一个块（atomic table）**：表不大（几十行内）时最稳，配上表标题 + 上下文段落。缺点是超过 embedding 上限要截断，且一个大表会稀释语义。
2. **按行 / 行组切**：每行（或每 N 行）一个块，**每块都复制表头**。Table RAG 的 STC 框架就是这么做的——按行建模成"层级行树"，每行编码为 key-value 块，再按 token 上限对齐结构边界切分、无重叠贪心合并。适合财报、明细表。
3. **双表示（summary + raw）**：表摘要向量用于召回，命中后把整张 HTML 表喂给模型。LangChain 的表格基准里 "multi-vector + ensemble" 就是这一路，结论是能优先把表格来源的块排上来。
4. **跨页表合并**：先合并成一张再切，否则跨页表会被拆成两块无头表。Unstructured 有专门的 `chunk_by_table_merging` 节点（LLM 辅助）。

LangChain 那张基准的实测结论值得引用：长上下文直接塞整体表现不稳（受表在文档中的位置影响）；定向抽取天花板最高但实现复杂、识别失败模式多；**按页边界切分是把表完整保留在块里的"合理折中"**，缺点是跨页表。

## 六、落到本仓（corporate_rag）的现状差距

- 支持格式只有 `.txt` / `.docx` / `.pdf`（`src/parsers/router.py:42` 注册表）；无图片入口、无 OCR、无 TSR、无 VLM。
- 扫描件（无文字层 PDF）**直接判失败**：`src/services/document_service.py:496` 把文档状态写成 `failed` + "扫描件暂不支持"。也就是说当前连"扫描 PDF"这一档都没走通，图片只是更极端的情况。
- `ParseResult` 只有 chunk 文本 + 页数/字符数/编码/标题树，没有 bbox、没有元素类型（`src/parsers/base.py:20`）。
- `chunks` 表有 `extra` JSONB（`src/infra/db/models/chunk.py`），是将来存 `image_uri / bbox / table_html` 的天然落点，无需改表结构。
- 缺口排序（最小可用路径）：① 放开图片/扫描件 → ② 接一个版面+OCR（PaddleOCR / MinerU / Docling 任一）→ ③ 表格 TSR 转 HTML → ④ 图走 VLM 描述 → ⑤ 生成期取回原图（需要 LLM 支持 vision）。其中 ⑤ 依赖模型能力，是硬前提。

## 七、反例与风险

- **描述即真相**：VLM 给图生成的描述一旦错（数字读错、图表趋势读反），检索和回答都会继承这个错误，且**无法从原文核对**。描述必须与图注、上下文一起呈现，不能替代原图。
- **成本**：每张图一次 VLM 调用，导入期成本线性上涨；NVIDIA 明确提示生成期看图会拉高延迟。批量导入要限流 + 缓存（同图哈希去重）。
- **表格 HTML 的 token 膨胀**：`<td>` 标签比 Markdown 管道符更费 token，大表进上下文容易爆预算。存 HTML、喂模型前按需转 Markdown 或摘要。
- **ColPali 路线在本仓不适合**：需要 GPU 常驻 + 多向量存储改造，且对中文企业文档（多为扫描/低质）泛化无保障。

## 八、未解问题

- 中文表格的 TSR 实际准确率没有一手实测数据；PaddleOCR PP-Structure 与 MinerU 在合并单元格上的差异需自己跑。
- "图片里只有一行字"（截图、印章、名片）这类，走 OCR 还是走 VLM 更划算，没有公认阈值。
- 描述文本与 OCR 文本混在同一向量空间时，检索分数是否可比（同一模型 embed、但语域不同）缺少量化结论。

## 来源

- Azure AI Document Intelligence · layout model（figures 对象：boundingRegions / spans / elements / caption；`output=figures` 返回裁图）— https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout
- Azure · Markdown output format（表格用 HTML 表格保留 rowspan/colspan 与 caption）— https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/markdown-elements
- Azure 样例 · figure understanding（裁图 → GPT-4V 描述 → 回写 Markdown → 语义分块）— https://github.com/Azure-Samples/document-intelligence-code-samples/blob/main/Python(v4.0)/Retrieval_Augmented_Generation_(RAG)_samples/sample_figure_understanding.ipynb
- Unstructured · Enhancement nodes（图摘要 / 表摘要 / 表转 HTML / 生成式 OCR / contextual chunking / 跨页表合并）— https://docs.unstructured.io/api-reference/workflow/nodes/enhancement/enhancement
- Unstructured issue #2674 · Chunking Ignores Images（分块丢弃 Image 元素）— https://github.com/Unstructured-IO/unstructured/issues/2674
- RAGFlow DeepDoc（OCR + YOLOv10 版面 + TSR + XGBoost 阅读顺序 + figure_parser 的 vision LLM）— https://doc.holiday/library/ragflow/deepdoc/
- RAGFlow · Accelerate Indexing（OCR / TSR 是可选的重计算阶段）— https://ragflow.io/docs/accelerate_doc_indexing
- NVIDIA RAG Blueprint · VLM（ingestion 配 caption 索引，查询期把被引用图送 VLM）— https://docs.nvidia.com/rag/2.4.0/vlm.html
- ColPali 家族（多向量视觉检索、模型规模与多语言变体）— https://qdrant.tech/course/multi-vector-search/module-2/colpali-family/
- ColPali 原论文 — https://arxiv.org/abs/2407.01449
- Lost in OCR Translation?（vision-based vs OCR-based 泛化对比）— https://arxiv.org/abs/2505.05666
- LangChain · Benchmarking RAG on tables（长上下文 / 定向抽取 / 分块三路线实测，按页切分的折中）— https://www.langchain.com/blog/benchmarking-rag-on-tables
- Structure-Aware Chunking for Tabular Data（行级行树 + token 约束切分 + 无重叠贪心合并）— https://arxiv.org/abs/2605.00318
