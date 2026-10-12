# KB 管理页面（浅色 harness 风格）

> `/Knowledgebase`（`deploy/nginx/html/index.html`）从**深色 Tailwind 风格**改造为与 chat 页面（`chat.html` / `chat-harness.md`）一致的**浅色 harness 风格**。布局壳与侧栏**全量复刻 chat 页面**，主区功能不变（知识库卡片网格 / 文档列表 / 上传 / 分块预览 / 评估弹窗），仅做视觉换肤。

## 目标与范围

- **风格对齐**：复用 chat.html 设计系统（`#3B82F6` primary 浅色系、CSS 变量、280px 侧栏），去掉深色 `slate-900` 侧栏与 Tailwind CDN 依赖
- **功能不变**：知识库 CRUD、选中文档列表、文档上传轮询、分块预览分页、分块评估详情弹窗、删除确认、toast —— 交互逻辑与后端契约原样保留，仅换 HTML 结构 + CSS
- **侧栏底部**：原「新建知识库」按钮位置改为**用户模块**（同 chat.html：未登录 → 登录入口；已登录 → 头像 + 用户名 + 退出登录下拉）
- **「新建知识库」入口**：仅保留**主区右上角**按钮（决策 2026-09-02）
- **侧栏复刻 chat.html**：Logo + 品牌名 + 版本号 → 功能块（智能问答 `/`、知识库管理当前页高亮）→ **KB 列表**（替代 chat 的会话历史列表）→ 底部用户模块

## 整体布局

```
┌─ Sidebar（浅色 280px，全量复刻 chat.html）──┬─ Main（浅色 #F8FAFC）──────────┐
│ Logo FQ + Corporate Agent Harness + v0.1.0 │  主区顶栏：                    │
│ ────────────────────────                   │   知识库管理（title）          │
│ [功能块] 💬 智能问答                        │                          [+ 新建知识库]│
│         📚 知识库管理（active 高亮）        │ ─────────────────────────────── │
│ ────────────────────────                   │  KB 卡片网格（3 列响应式）      │
│ 知识库（列表，可滚动）                      │   首字母图标 + 名称 + 文档数   │
│  · KB 项（hover/active）                    │   + 描述 + 删除（hover 显）    │
│  · 空态：暂无知识库                         │ ─────────────────────────────── │
│                                             │  【选中 KB → 文档区】          │
│ ────────────────────────                   │   标题 + RAGAS 徽标 + 上传按钮 │
│ [用户模块] 头像 + 用户名 / 退出登录         │   文档行列表（类型图标/状态/评分│
│                                            │   详情/删除）                  │
└────────────────────────────────────────────┴────────────────────────────────┘
```

- **侧栏结构元素全部复用 chat.html 的 class**（`.sidebar/.sidebar-header/.logo/.brand/.feature-block/.feature-btn/.section-title/.sidebar-footer/.user-avatar/.user-dropdown`），中间列表区从"会话历史"替换为"知识库"列表（复用 `.session-list/.session-item` 形态）
- **主区**：页面背景 `#F8FAFC`，内容卡片白底 `#FFFFFF` + 1px `#E2E8F0` 描边 + `--shadow` 轻阴影，圆角 12px
- **KB 选中态**：侧栏项 primary 底白字（同 chat active 会话）；主区卡片 hover 描边转 primary

## 主区组件规格（浅色换肤）

| 组件 | 现深色实现 | 浅色换肤 |
|------|-----------|---------|
| 页面标题区 | `知识库管理` h2 + 右上「新建知识库」主蓝按钮 | 同位置；标题 `--text` 20px Lexend；按钮 primary 底白字 13px 圆角 10px |
| KB 网格卡片 | 白卡 `border-slate-200` + hover 蓝边 | 白卡 1px `--border` + hover 边 `--primary` + `--shadow`；首字母图标块 `--primary-light` 底 primary 字；文档数/描述 `--text-secondary` |
| 删除（KB/文档） | hover 显红 trash icon | 同；hover 底色 `#FEF2F2`、icon `#DC2626` |
| 文档列表行 | `divide-slate-100` 分隔 | 行 hover `--surface-muted`；文件名 `--text` 14px；meta 12px `--text-muted` |
| 文件类型图标 | PDF 红 / DOCX 蓝 / TXT 灰圆角块 | 同语义色（红 `#DC2626` 系 / 蓝 `#3B82F6` 系 / 灰 `--text-muted`），改用浅底深字圆角 8px |
| 状态徽标 status-badge | style.css `.ready/.processing/.error` | 语义色保留：ready 绿勾 / processing 蓝点 / error 红叉（12px 胶囊） |
| RAGAS 徽标（KB 级） | 浅蓝底蓝边标签 | `--primary-light` 底、1px `#93C5FD` 边、`--primary` 字 |
| 分块评分 | 行内 0.89 绿/红 | 通过 emerald（`#059669`）/失败 red（`#DC2626`）12px medium |
| 「详情」按钮 | `text-blue-600 hover:bg-blue-50` | `--primary` 字 hover `--primary-light` 底 |

## 弹窗 / 浮层（浅色换肤）

- **新建知识库 Modal**：白底圆角 12px、标题 16px `--text`；input 1px `--border` focus ring `rgba(59,130,246,0.15)`；主按钮 primary、次按钮灰
- **删除确认 Modal**：红警告 icon（`#FEF2F2` 圆底）+ 确认按钮 red
- **分块预览 / 评估详情 / 断裂详情 Modal**：宽版白卡 `max-h-[85vh]`，header 分隔线 `--border`；无数据时 loading spinner
  - **分块列表是"固定高度的列 flex"，条目必须 `flex: 0 0 auto`**：`.chunk-list` 为 `display:flex; flex-direction:column; flex:1; overflow-y:auto`（模态体），`.chunk-card` 若用默认 `flex-shrink:1`，条目会被**压缩去适配容器高度** ⇒ `overflow-y` 永不触发、正文被 `overflow:hidden` 裁成一条灰边（表现为"弹窗里全是灰条、内容展示不全"）。2026-10-12 修复，属 `a634d33` 浅色化引入的缺陷。
  - 同段共用的 `.eval-detail-body` / `.eval-broken-body` **不是** flex 列，不受此约束影响。
- **上传 loading**：居中白卡 + spinner
- **toast**：语义色（成功绿 `#059669` / 错误 red）

## 用户模块（左下角，同 chat.html）

- 未登录：`[头像占位] 未登录 / 登录`，点击跳 `/login.html?redirect=/Knowledgebase`
- 已登录：`[首字母圆头像] 用户名 / 退出登录`，点击弹下拉菜单（同 chat `.user-dropdown`），含「退出登录」按钮
- 复用 chat.html `updateUserArea / toggleUserDropdown / handleLogout` 逻辑与 `/api/auth/verify` 鉴权

## 移动端

- 保留现有 hamburger + 抽屉行为；侧栏 `-translate-x-full` 收起，遮罩点击关闭
- 主区 KB 网格 1 列 / 文档区横向条目垂直堆叠

## 验收 / 验证

- 用 `playwright-cli` 打开 `http://localhost/Knowledgebase` 核对：
  - 侧栏浅色、功能块「知识库管理」高亮；左下角为用户模块而非「新建知识库」按钮
  - 主区右上角「新建知识库」可打开弹窗并创建成功
  - KB 卡片选中 → 文档列表显示；上传/分块预览/评估/删除弹窗正常
  - 整体观感与 chat.html 一致（浅色、同款侧栏/字体/圆角/阴影）
- 设计稿预览：`docs/design/kb-harness-page-mockup.html`（效果预览按 ui-design-flow 只出 HTML，不截图）
