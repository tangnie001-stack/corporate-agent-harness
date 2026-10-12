# 企业知识库 RAG 对话 — 设计系统

## 风格
**Minimalism & Swiss Style** — 简洁、留白充足、高对比度、网格化布局。

## 色彩

| 角色 | 色值 | 用途 |
|------|------|------|
| Primary | `#3B82F6` | 主按钮、AI 气泡强调色、链接 |
| Secondary | `#60A5FA` | 辅助色、次要按钮 hover |
| Accent/CTA | `#F97316` | 发送按钮、快捷选项强调 |
| Background | `#F8FAFC` | 页面/对话区背景 |
| Surface | `#FFFFFF` | 气泡卡片底色 |
| Text Primary | `#1E293B` | 正文 |
| Text Secondary | `#64748B` | 辅助文字、时间戳 |
| Border | `#E2E8F0` | 分割线、输入框边框 |
| Error | `#EF4444` | 错误提示 |

## 字体
- **Heading**: Lexend (300–700)
- **Body**: Source Sans 3 (300–700)
- 基础字号: 16px，行高 1.5

## 圆角
- 气泡: 12px (AI) / 16px (用户)
- 快捷选项: 8px
- 输入框: 10px

## 阴影
- 气泡: `0 1px 3px rgba(0,0,0,0.08)`
- 输入框聚焦: `0 0 0 3px rgba(59,130,246,0.15)`

## 间距
- 气泡间距: 16px
- 选项按钮间距: 8px
- 内容内边距: 16px

## 布局规则

- **可滚动的"列 flex"容器，其条目必须 `flex-shrink: 0`**。容器写成
  `display:flex; flex-direction:column; flex:1; overflow-y:auto` 时，条目默认 `flex-shrink:1`
  会被**压缩去适配容器高度** —— 于是 `overflow-y:auto` 永不触发、内容被 `overflow:hidden`
  裁掉，视觉上表现为"页面/弹窗展示不全"。条目一律写 `flex: 0 0 auto`（或 `flex-shrink: 0`）。
  实例：`.chunk-card`（`index.html` 分块预览弹窗，2026-10-12 修）。

## 组件规格
按页登记于 `docs/design/pages/`，此处为组件索引：

| 组件 | 页面文档 | 关键规格 |
|------|---------|---------|
| 状态标签 status-tag | `agentic-clarification.md` | 胶囊 + 蓝色呼吸圆点 |
| 追问卡片 composer | `agentic-clarification.md` | **输入区接管**（不进消息流）、选项卡片选中 primary-light、提交 primary |
| abstention 转人工 | `agentic-clarification.md` | amber 提示条 + accent 橙 outline 按钮 |
| 答案反馈 feedback | `agentic-clarification.md` | 28×28 SVG 按钮、选中 primary 高亮 |
| 模型信息 model_info | `agentic-clarification.md` | 11px muted、fallback amber 徽标 |
| 引用卡片 citation | `agentic-clarification.md`（组件）+ `chat-harness.md`（页面形态） | 末尾一体来源横条「来源 [n] ›」+ 右侧抽屉（fixed right，380px，滑出动画，不遮挡对话）；正文 `[n]` 点击打开抽屉并高亮定位 |
| 引用等级徽标 citation-tier | `chat-citation-tier-2026-09-09.md` | 仅引用抽屉条目渲染（来源名称之前）10px 胶囊徽标（T0 紫/T1 绿/T2 蓝/T3 灰/T4 橙）；横条不加；tier 缺失不渲染；标签唯一权威 = const.py SOURCE_TIER_LABELS |
| 深度思考开关 thinking-toggle | `agentic-clarification.md` | 输入区紧凑 chip、选中 primary、控制 `enable_thinking` |
| 智能体选择器 agent-selector | `chat-agent-skill-selector-2026-09-11.md` | 知识库胶囊右侧胶囊（user-round 图标）、默认文案「默认」、下拉单选（名称+描述+单选点）、会话级不可变（菜单底锁提示）；历史页不渲染，顶栏只读徽标回显 |
| 技能选择器 skill-selector | `chat-agent-skill-selector-2026-09-11.md` | 深度思考 chip 右侧同构 chip（book-open 图标）、菜单**向上**弹出、选中变蓝显示技能名、选中即向输入框行首插入 `/name `；只列可用户调用技能 |
| Markdown 渲染 | `chat-markdown.md` | 助手消息 `.bubble-content.md` 富文本渲染（标题/加粗/列表/代码块/引用/表格）、marked+DOMPurify 安全、流式节流重渲染 |
| 分析过程折叠区 delegate-progress | `chat-delegate-progress-2026-09-07.md` | AI 气泡内按 delegate_id 分节：思考二级折叠 + 正文流式；运行呼吸点/完成/中断(原因) 文案 |
| 委派状态文案 | `chat-delegate-progress-2026-09-07.md` | "领域专家分析完成" / "分析中断·原因"（取代无条件"完成"） |
| 任务/进度看板 task-board | `chat-delegate-progress-2026-09-07.md` | 顶栏"任务"chip（进行中计数）→ 右侧 360px 只读面板；引用抽屉互斥单开；快照接口初始化 |

## 页面索引

| 页面 | 规格文档 | 说明 |
|------|---------|------|
| Chat 页面（聊天主界面 `/`） | `pages/chat-harness.md` | 左侧栏 + 新对话/历史对话双页面、KB 会话级绑定、引用悬浮卡交互 |
| Chat 子代理过程 + 任务看板 | `pages/chat-delegate-progress-2026-09-07.md` | 委派过程折叠区（分节/流式/完成·中断文案）+ 任务/进度看板（chip + 右侧只读面板） |
| KB 管理页面（`/Knowledgebase`） | `pages/kb-harness-page.md` | 与 chat 同款浅色布局与侧栏（含左下角用户模块），功能不变 |
| Chat 页：智能体选择器 + 技能选择器 | `pages/chat-agent-skill-selector-2026-09-11.md` | 新对话页「知识库 + 智能体」同行（智能体在右，默认「默认」）；输入区「深度思考 + 技能」同排（技能在右）；智能体会话级不可变、技能消息级持续生效 |

新组件/页面设计时：全局基线更新到本文件，页面级规格写入 `pages/<name>.md`，效果预览输出 `docs/design/<name>-mockup.html`。
