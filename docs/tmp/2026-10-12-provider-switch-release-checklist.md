# 生产发布对照单 — provider 切换（百炼 → DeepSeek）

> **性质**：本次发布的**一次性对照单**（状态跟踪 + 本次差异）。**步骤正文以 `docs/agents/deploy-runbook.md` 为准**，本单不复述流程，只记"本次特有差异 + 进度 + 判据"。
> **日期**：2026-10-12 ｜ **分支**：`dev-wsl` ｜ **发布提交**：`68a6d09`（provider 兼容层合并 `57ce076` + 归档 `4ba99da` 之后的文档收尾）

## 0. 本次要发布什么

| 项 | 内容 |
|---|---|
| 代码 | provider 兼容层（`src/infra/llm/provider/`：registry + spec/thinking/chat/factory/pricing_seed）；`models.py` 门面薄委托 + 新增 `get_judge_llm`；middleware/executor 接线 |
| 新依赖 | `pyyaml==6.0.3`（首次显式声明）|
| 配置 | 目标机 `.env` 切 **DeepSeek**（见 §1）|
| 归档 | change `provider-compat-layer` 已归档，主规格 68 |

## 1. 本次相对"常规发布"的差异（切 provider，runbook §2.7）

目标机 `/opt/www/corporate_rag/.env`：

- **加**：`LLM_PROVIDER=deepseek`、`DEEPSEEK_API_KEY=sk-…`
- **删/注释**：`LLM_BASE_URL`、`LLM_API_KEY`（留着会把请求指回百炼）、`LLM_MODEL`、`CLASSIFY_MODEL`（留着会把旧名发到 DeepSeek → 404）
- **保持**：`DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` / `RERANK_MODEL`（embedding · rerank · judge **仍走百炼**）
- 本机 `.env` 已是该状态 → 若生产与本机**同源**（runbook §1.4 "同一份 `.env` 通用"），**先 diff 再 scp**：

```bash
ssh root@8.133.218.201 'cat /opt/www/corporate_rag/.env' | diff - .env   # 先看差异（尤其生产独有键）
scp .env root@8.133.218.201:/opt/www/corporate_rag/.env
ssh root@8.133.218.201 'chmod 600 /opt/www/corporate_rag/.env'
```

## 2. 步骤跟踪（全链接速查）

| # | 步骤 | 地址 / 命令 | 状态 |
|---|---|---|---|
| 1 | 同步 `.env` | 见 §1（`scp` + `chmod 600`） | ☐ |
| 2 | ACR 登录（仅首次） | `docker login --username=hi31383397@aliyun.com crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com` | ☐（通常跳过）|
| 3 | 前置检查 | `git fetch && git status -sb` | ☑ 已代查：工作区干净、ahead/behind = 0 |
| 4 | 推代码 | `git push origin dev-wsl` | ☑ `68a6d09` |
| 5 | **Codeup 同步** | `https://deploytest-cn-shanghai.devops.aliyuncs.com/codeup/deploytest/tangnie001-stack/corporate_rag/settings/mirror_sync` → 「立即同步」 | ☐ |
| 6 | **流水线** | `https://deploytest-cn-shanghai.devops.aliyuncs.com/flow/my?page=1` | ☐ |
| 7 | 创建发布单（仅"匹配不到"时） | 同第 6 条页面 | ☐ |
| 8 | 主机部署执行 | 自动（`preflight→start→health_check→clean`） | ☐ |
| 9 | **冒烟** | `http://8.133.218.201/` | ☐ |
| 10 | 版本 / 回滚页 | `https://deploytest-cn-shanghai.devops.aliyuncs.com/appstack/app/deploy-app/versions` | — |

## 3. 两个易踩的坑

1. **预热云效 PyPI 代理仓 `repo-okxha`** —— 本次新增 `pyyaml` 依赖，懒加载缓存未命中会让 build 失败（runbook §2.6）。
2. **查生产 `.env` 有无 `LLM_KWARGS`** —— 本次只对"含 `extra_body` 的 `LLM_KWARGS`"**告警未废弃**；若生产设了它，可能与新发的思考参数打架。

## 4. 冒烟判据（runbook §5 + §2.7）

- [ ] `curl -fsS http://127.0.0.1/api/health` → 200
- [ ] 站点登录 → 建/选 KB → **上传文档** → 等 `ready` → 提问**带引用**
- [ ] 目标机日志：`[provider] validate state=on`，且真实对话 `[agent] model turn model=deepseek-flash`
- [ ] 三台企微机器人（dev / support / finance）各发一条正常
- [ ] 重点覆盖：普通问答 / 勾深度思考 / 需检索 KB 的问题（工具多轮）

## 5. 回滚

**镜像 + `.env` 一起回**（runbook §7）—— 新旧版对 `.env` 键的期待不同（新版已删软迁移键 `LLM_BASE_URL`/`LLM_API_KEY`），只回镜像会读不到键而崩。
版本回滚页：`https://deploytest-cn-shanghai.devops.aliyuncs.com/appstack/app/deploy-app/versions`
