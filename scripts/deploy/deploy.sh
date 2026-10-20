#!/usr/bin/env bash
# 目标机部署（demo 单机）
#   环境判定 → 依赖安装 → 前置检查 → ACR 登录 → 拉镜像 → 起栈 → 迁移 → 健康检查
#
# 用法：
#   bash scripts/deploy/deploy.sh [mode] [--force] [--no-install-deps]
#
#   mode 省略即 deploy（完整发布）。可选：
#     deploy | start   完整发布：环境判定 → 依赖 → 前置检查 → ACR 登录 → 拉镜像 → 起栈 → 迁移 → 健康检查
#     preflight        只校验制品（存在 + gzip 完整），**不停服**；供流水线「停止」槽位调用
#     health_check     只做健康探测（经 nginx:80 探 /api/health），失败非零退出
#     stop             优雅停 app+nginx（**人工运维用**，勿接进流水线「停止」槽位 —— 否则部署中途失败会把服务停在下线）
#     clean            清理旧 app 镜像（保留最近 2 个）+ 悬空镜像 + 残留包
#
#   --force            跳过「开发机 / 环境未知」的拒绝（仅在你明确知道后果时用）
#   --no-install-deps  缺 docker 时不自动安装，直接报错（预装好的机器可用）
#
# 文件从哪来：**不走 git**。云效流水线的「主机部署」任务把构建期打好的制品包下发到目标机并解压，
#   本脚本假定当前目录已是解压后的发布目录（含 docker-compose.image.yml / deploy/ / scripts/deploy/）。
#   制品由 scripts/ci/pack-deploy-artifact.sh 生成，其白名单与本脚本第 3 步的前置检查是同一份契约，
#   改一边必须改另一边。
#
# 非交互登录 ACR（可选）：
#   ACR_USER=xxx ACR_PASSWORD=yyy bash scripts/deploy/deploy.sh
#
# 标准发布路径：/opt/www/corporate_rag（/opt/www 为多站点父目录）
#   —— 在别处执行会打印提醒；所有文档 / 备份定时任务都引用该路径
#
# 两点前提：
#   1. 不要在开发机上跑本脚本：部署档与 dev 的 compose project name、容器名完全相同，
#      起栈会顶掉正在跑的 dev 容器（脚本会主动拦截，除非 --force）。
#   2. 缺 docker 时脚本会用**国内源**自动安装（需 root 或 sudo）。
#
# 镜像必须由本脚本拉：ACR 个人版不支持平台代拉（仅企业版可），故第 5 步 `compose pull` 不可省。

set -euo pipefail

COMPOSE_FILE="docker-compose.image.yml"
ACR="crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com"
EXPECTED_ROOT="/opt/www/corporate_rag"                               # 标准发布路径
ECS_METADATA="http://100.100.100.200/latest/meta-data/instance-id"    # 阿里云 ECS 元数据服务
METADATA_HOST="100.100.100.200"
HEALTH_URL="http://127.0.0.1/api/health"   # 经 nginx:80，与对外路径一致
RETRIES=40
INTERVAL=3

ARTIFACT="${ARTIFACT:-/home/admin/app/package.tgz}"   # 云效主机部署下发的制品包（preflight / clean 用）
KEEP_N=2                                              # clean：保留最近 N 个 app 镜像（含当前在用）

FORCE=0
INSTALL_DEPS=1

# 打印文件头的用法块（首行 shebang 之后的连续 # 注释行）
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; }

# 首个非选项参数 = 模式；缺省 deploy
MODE="deploy"
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
  MODE="$1"; shift
fi
case "$MODE" in
  deploy|start|preflight|stop|health_check|clean) ;;
  *) echo "未知模式: $MODE（-h 看用法）" >&2; exit 2 ;;
esac

while [ $# -gt 0 ]; do
  case "$1" in
    --force)            FORCE=1 ;;
    --no-install-deps)  INSTALL_DEPS=0 ;;
    -h|--help)          usage; exit 0 ;;
    *)                  echo "未知参数: $1（-h 看用法）" >&2; exit 2 ;;
  esac
  shift
done

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
ROOT=$(pwd)

if [ "$(id -u)" = "0" ]; then SUDO=""
elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"
else SUDO=""
fi

info()  { echo "[deploy]   $*"; }
warn()  { echo "[deploy]   ⚠ $*"; }
fail()  { echo "[deploy] ✗ $*" >&2; exit 1; }
step()  { echo; echo "[deploy] === $* ==="; }
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }
count_files() { { find "$1" -type f 2>/dev/null || true; } | wc -l; }

# 开发机（WSL）护栏：stop / clean 会操作本机 docker，在开发机上会误伤 dev 容器 / 镜像。
# 与 deploy 的 dev 拦截同一判据；--force 可放行。
wsl_guard() {
  if [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/version 2>/dev/null; then
    [ "$FORCE" = 1 ] || fail "本模式会操作本机 docker（开发机上会误伤 dev 容器 / 镜像）。确要执行请加 --force"
    warn "--force 已指定，继续（后果自负）"
  fi
}

# ── 健康探测（deploy 第 8 步与 health_check 模式共用）──
wait_healthy() {
  local i
  for i in $(seq 1 "$RETRIES"); do
    if curl -fsS -o /dev/null "$HEALTH_URL" 2>/dev/null; then
      info "✓ 第 $i 次探测通过"
      return 0
    fi
    info "… 第 $i/$RETRIES 次未就绪，${INTERVAL}s 后重试"
    sleep "$INTERVAL"
  done
  return 1
}

# ── preflight：只校验制品，不停任何服务（供流水线「停止」槽位调用）──
# 设计要点：破坏性动作一律不在此发生 —— 制品坏就中止，旧版本保持在线；
# 停旧起新由 start 阶段的 `compose up -d` 一次完成。
do_preflight() {
  step "预检（只校验制品，不停止服务）"
  [ -f "$ARTIFACT" ] || fail "制品不存在: $ARTIFACT（下载失败？中止，旧版本保持在线）"
  if ! gzip -t "$ARTIFACT" 2>/dev/null; then
    echo "[deploy] ✗ 制品不是有效 gzip（大小 $(stat -c%s "$ARTIFACT" 2>/dev/null) 字节），前 200 字节："
    head -c 200 "$ARTIFACT" 2>/dev/null; echo
    fail "制品损坏 —— 中止，旧版本保持在线"
  fi
  info "✓ 制品有效（$(stat -c%s "$ARTIFACT") 字节）"
}

# ── stop：优雅停 app+nginx（人工运维用）──
do_stop() {
  step "停止服务（人工运维）"
  wsl_guard
  if [ ! -f "$COMPOSE_FILE" ]; then
    info "$COMPOSE_FILE 不存在（首次部署？），无需停止"
    return 0
  fi
  # 只停 app 与 nginx（每版会替换的两个）；DB / Redis / MinIO / Langfuse 保持在线以缩短停机
  compose stop -t 30 app nginx 2>/dev/null || true
  info "app / nginx 已停止（DB、Redis、MinIO、Langfuse 保持运行）"
}

# ── health_check：只做健康探测 ──
do_health_check() {
  step "健康检查"
  command -v curl >/dev/null 2>&1 || fail "未找到 curl（健康检查需要）"
  if ! wait_healthy; then
    compose logs --tail 40 app 2>/dev/null || true
    fail "应用未在预期时间内就绪: $HEALTH_URL"
  fi
}

# ── clean：回收空间，只删"可再生"之物 ──
do_clean() {
  step "清理（旧镜像 / 悬空镜像 / 残留包）"
  wsl_guard
  # app 镜像 tag 即云效构建号（dockerTag=BUILD_NUMBER，从 1 递增），故旧版本 = 1..(CUR_TAG-KEEP_N)
  if [ -f "$COMPOSE_FILE" ]; then
    cur_ref=$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(.*deploy_store_local:[^[:space:]]*\).*/\1/p' "$COMPOSE_FILE" | head -1)
    cur_tag="${cur_ref##*:}"
    repo="${cur_ref%:*}"
    case "$cur_tag" in
      ''|*[!0-9]*)
        warn "当前 app tag 非纯数字（${cur_tag:-空}），跳过旧镜像清理" ;;
      *)
        limit=$((cur_tag - KEEP_N))
        info "当前 app tag: $cur_tag，保留最近 $KEEP_N 个（尝试删 1..$limit）"
        t=1
        while [ "$t" -le "$limit" ]; do
          if docker rmi "$repo:$t" >/dev/null 2>&1; then info "已删旧镜像: $repo:$t"; fi
          t=$((t + 1))
        done ;;
    esac
  fi
  docker image prune -f >/dev/null 2>&1 || true
  docker builder prune -f >/dev/null 2>&1 || true
  rm -f "$ARTIFACT" 2>/dev/null || true
  rm -rf "$ROOT/pack-staging" 2>/dev/null || true
  info "清理完成"
}

# 非发布模式：执行后直接退出（deploy / start 继续走下面的完整流程）
if [ "$MODE" != "deploy" ] && [ "$MODE" != "start" ]; then
  case "$MODE" in
    preflight)    do_preflight ;;
    stop)         do_stop ;;
    health_check) do_health_check ;;
    clean)        do_clean ;;
  esac
  exit 0
fi

DEPLOYED_APP_IMAGE=""

# 阿里云元数据服务可达性（在阿里云 ECS 上必通；开发机 / 自建机不通）
# 优先 curl；无 curl 时用 bash 内建 /dev/tcp 做最小 HTTP 探测
metadata_reachable() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 -o /dev/null "$ECS_METADATA" 2>/dev/null
    return $?
  fi
  exec 9<>"/dev/tcp/${METADATA_HOST}/80" 2>/dev/null || return 1
  printf 'GET /latest/meta-data/instance-id HTTP/1.0\r\nHost: %s\r\n\r\n' "$METADATA_HOST" >&9
  local line=""
  IFS= read -r line <&9 || true
  exec 9<&- 2>/dev/null || true
  case "$line" in *" 200 "*) return 0 ;; *) return 1 ;; esac
}

step "1/8 环境判定"
# 判据优先级：① DEPLOY_ROLE 显式值 > ② WSL（必为开发机，零依赖）> ③ 阿里云元数据服务（必为云上发布机）
ROLE="${DEPLOY_ROLE:-}"
if [ -z "$ROLE" ]; then
  if [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/version 2>/dev/null; then
    ROLE="dev"
  elif metadata_reachable; then
    ROLE="prod"
  else
    ROLE="unknown"
  fi
fi

case "$ROLE" in
  prod)
    iid=""
    if command -v curl >/dev/null 2>&1; then
      iid=$(curl -fsS --max-time 2 "$ECS_METADATA" 2>/dev/null || echo "?")
    fi
    info "环境: 发布机（阿里云 ECS${iid:+ instance-id=$iid}）"
    ;;
  dev)
    info "环境: 开发机（WSL）"
    [ "$FORCE" = 1 ] || fail "本脚本用于目标机部署，开发机上运行会顶掉 dev 容器。确要执行请加 --force"
    warn "--force 已指定，继续（后果自负）"
    ;;
  *)
    info "环境: 未识别（非 WSL，且阿里云元数据服务不可达）"
    [ "$FORCE" = 1 ] || fail "无法确认这是发布机。若是自建 / 非阿里云主机，请加 --force 或设 DEPLOY_ROLE=prod"
    warn "--force 已指定，继续"
    ;;
esac

step "2/8 依赖安装（docker，走国内源）"
if [ "$INSTALL_DEPS" != 1 ]; then
  info "跳过（--no-install-deps）"
else
  PKG=""
  if command -v apt-get >/dev/null 2>&1; then PKG="apt"
  elif command -v dnf >/dev/null 2>&1; then PKG="dnf"
  elif command -v yum >/dev/null 2>&1; then PKG="yum"
  fi
  [ -n "$PKG" ] || fail "无法识别包管理器（需 apt / dnf / yum）；请手动安装 docker 与 compose 插件"
  if [ -z "$SUDO" ] && [ "$(id -u)" != "0" ]; then
    fail "安装依赖需要 root 权限：请用 root 执行，或先装好 sudo"
  fi
  info "包管理器: $PKG${SUDO:+（经 sudo）}"

  # ---- docker + compose 插件 ----
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    info "docker 已安装: $(docker --version)"
  else
    info "未找到 docker / compose 插件 → 用阿里云镜像源安装"
    case "$PKG" in
      apt)
        $SUDO apt-get update -y
        $SUDO apt-get install -y ca-certificates curl gnupg
        $SUDO install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg \
          | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
        codename=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}")
        [ -n "$codename" ] || fail "无法确定发行版代号（/etc/os-release 缺 UBUNTU_CODENAME / VERSION_CODENAME）"
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $codename stable" \
          | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
        $SUDO apt-get update -y
        $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        ;;
      dnf|yum)
        # 直接写 repo 文件，避免依赖 yum-utils / dnf-plugins-core
        $SUDO tee /etc/yum.repos.d/docker-ce.repo >/dev/null <<'REPO'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://mirrors.aliyun.com/docker-ce/linux/centos/$releasever/$basearch/stable
enabled=1
gpgcheck=1
gpgkey=https://mirrors.aliyun.com/docker-ce/linux/centos/gpg
REPO
        # Alibaba Cloud Linux 3 等发行版的 $releasever 不被 docker-ce 仓支持 → 按 RHEL 兼容大版本固定
        rel=$(rpm -E '%{rhel}' 2>/dev/null || true)
        case "$rel" in ''|*[!0-9]*) rel=8 ;; esac
        [ "$rel" -ge 7 ] 2>/dev/null || rel=8
        $SUDO sed -i "s/\$releasever/$rel/" /etc/yum.repos.d/docker-ce.repo
        info "docker-ce 源: 阿里云镜像（releasever=$rel）"
        $SUDO "$PKG" install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        ;;
    esac
    $SUDO systemctl enable --now docker >/dev/null 2>&1 \
      || $SUDO service docker start >/dev/null 2>&1 \
      || warn "docker 服务未能自动启动，请手动检查（systemctl status docker）"
    command -v docker >/dev/null 2>&1 || fail "docker 安装失败"
    docker compose version >/dev/null 2>&1 || fail "docker compose 插件未安装成功"
    info "docker 安装完成: $(docker --version)"
  fi
fi

step "3/8 前置检查"
info "发布目录: $ROOT"
if [ "$ROOT" != "$EXPECTED_ROOT" ]; then
  warn "标准发布路径是 $EXPECTED_ROOT，当前在 $ROOT"
  warn "文档 / 备份定时任务都按标准路径写；非标准路径请自行保持一致"
fi
[ -f "$COMPOSE_FILE" ] || fail "缺少 $COMPOSE_FILE —— 当前目录不是解压后的制品（期望发布目录 $EXPECTED_ROOT）"
[ -f .env ] || fail "缺少 .env —— 制品不含密钥，需人工放置：从团队渠道取 .env.example 填真实值（MinIO 两键、PG/Redis 口令等）"

# .env 完整性：compose 里被插值的变量必须有非空值。
# 清单由 compose 自己派生 —— 不像手写清单那样会漂移（compose 少一个变量根本起不来）；
# 且能堵住 `${VAR:-默认值}` 的静默降级：缺键时 compose 会用兜底弱口令启动而不报错。
compose_vars=$(grep -oE '\$\{[A-Z_][A-Z0-9_]*' "$COMPOSE_FILE" | sed 's/\${//' | sort -u)
missing=""
for v in $compose_vars; do
  # 兼容 `VAR=` / `export VAR=` / 带引号 / CRLF，剥壳后再判空
  val=$(sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?${v}=//p" .env | tail -1 | tr -d '\r')
  val=$(printf '%s' "$val" | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")
  if [ -z "$val" ]; then missing="${missing:+$missing }$v"; fi
done
[ -z "$missing" ] || fail "主机 .env 缺以下键或值为空：${missing}（缺键会让 compose 静默用兜底值启动，如 REDIS_PASSWORD 兜成 corporate_rag_pass）"
info "✓ .env 完整性通过（compose 插值的 $(echo $compose_vars | wc -w) 个变量均有非空值）"

# 这些路径靠挂载供容器使用；缺任一条会导致**静默降级**（nginx 起不来、应用库不建），
# 所以必须报错，而不是让 Docker 自建空目录
missing=0
for d in deploy/nginx/html deploy/postgres/init; do
  if [ ! -d "$d" ]; then info "✗ 缺目录: $d"; missing=1; fi
done
for f in deploy/nginx/nginx.conf; do
  if [ ! -f "$f" ]; then info "✗ 缺文件: $f"; missing=1; fi
done
[ "$missing" = 0 ] || fail "制品不完整 —— 上述路径靠挂载供 nginx / postgres 使用，缺失会导致对应功能失效"

# TLS 证书：nginx.conf 里引用的每个证书文件都必须在宿主就位。
# 缺证书时 nginx 新容器**启动即退出**（restart 策略下反复重启），80/443 无人监听、
# 健康检查必然失败、整站下线 —— 必须在动容器之前拦住。2026-10-20 实际踩过：
# 制品先上机、证书没传，compose 重建 nginx 后整站不可用。
# 清单从 nginx.conf 派生，增删域名不用改本脚本。
CERT_ROOT="/opt/www/certs"
missing_certs=""
cert_refs=$(grep -oE 'ssl_certificate(_key)?[[:space:]]+/etc/nginx/certs/[^;[:space:]]+' deploy/nginx/nginx.conf 2>/dev/null | awk '{print $2}' | sort -u || true)
for p in $cert_refs; do
  host_p="${CERT_ROOT}/${p#/etc/nginx/certs/}"
  if [ ! -f "$host_p" ]; then missing_certs="${missing_certs} ${host_p}"; fi
done
[ -z "$missing_certs" ] || fail "缺少 TLS 证书:${missing_certs} —— nginx 会启动失败、整站不可用；先按 runbook §1.4 上传证书再发布（本次未动任何容器，旧版本仍在线）"
info "✓ TLS 证书就位（$(echo "$cert_refs" | wc -w) 个文件）"
# 注：src/ scripts/ skills/ agents/ alembic/ 已打进 app 镜像（见 Dockerfile），宿主不再需要、也就不会再出现
#     「空目录导致技能/预设静默清空」那类故障
# 运行期目录；Docker 也能自建，这里显式建以固定属主。
# 旧 ragas 数据目录不自动删除（可能仍有历史报告），确认无引用后人工清理。
mkdir -p data/deepeval

step "4/8 校验 ACR 登录"
DEPLOYED_APP_IMAGE=$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(.*deploy_store_local:[^[:space:]]*\).*/\1/p' "$COMPOSE_FILE" | head -1)
[ -n "$DEPLOYED_APP_IMAGE" ] || fail "无法从 $COMPOSE_FILE 解析出 app 镜像引用"
info "app 镜像: $DEPLOYED_APP_IMAGE"

if docker manifest inspect "$DEPLOYED_APP_IMAGE" >/dev/null 2>&1; then
  info "✓ 已登录（app 镜像在私有库，5 个基镜像在公开库）"
else
  info "未登录或镜像不可达，开始登录 $ACR"
  if [ -n "${ACR_USER:-}" ] && [ -n "${ACR_PASSWORD:-}" ]; then
    printf '%s' "$ACR_PASSWORD" | docker login -u "$ACR_USER" --password-stdin "$ACR" \
      || fail "docker login 失败"
  else
    [ -t 0 ] || fail "非交互环境无法输入密码 —— 请用 ACR_USER / ACR_PASSWORD 环境变量提供凭据"
    docker login "$ACR" || fail "docker login 失败"
  fi
  docker manifest inspect "$DEPLOYED_APP_IMAGE" >/dev/null 2>&1 \
    || fail "登录后仍取不到该镜像 —— tag 由构建阶段注入，取不到说明制品与镜像不配套（检查云效那次构建是否成功推镜像）"
  info "✓ 登录成功"
fi

step "5/8 拉取镜像（ACR 个人版不支持平台代拉）"
compose pull

step "6/8 启动服务"
compose up -d

# up -d 不会重建未变更的服务，bind mount 的 nginx.conf 改了容器也不会重读 —— 故每次都显式重载。
# nginx -s reload 对未变更的配置是安全的空操作（新 worker 起来、旧 worker 优雅退出），零停机。
info "重载 nginx（每次部署都做，避免漏掉 nginx.conf 的变更）"
compose exec -T nginx nginx -s reload || warn "重载失败，旧配置继续生效（检查 nginx.conf 语法）"

step "7/8 执行数据库迁移"
# 先等 PostgreSQL 就绪（迁移经 compose 网络连它）；app 不依赖表即可启动，故先迁移无死锁
for i in $(seq 1 20); do
  if compose exec -T postgres pg_isready -U langfuse >/dev/null 2>&1; then
    info "✓ PostgreSQL 就绪"
    break
  fi
  if [ "$i" = 20 ]; then fail "PostgreSQL 未在预期时间内就绪"; fi
  sleep 2
done

# app 可能仍在启动中，exec 会瞬时失败；迁移幂等，故重试
migrated=0
for i in $(seq 1 10); do
  if compose exec -T app alembic upgrade head; then
    migrated=1
    break
  fi
  info "… 第 $i 次失败（app 可能仍在启动），5s 后重试"
  sleep 5
done
[ "$migrated" = 1 ] || fail "数据库迁移失败（可手动排查: docker compose -f $COMPOSE_FILE logs app）"

step "8/8 健康检查"
command -v curl >/dev/null 2>&1 || fail "未找到 curl（健康检查需要）"
if ! wait_healthy; then
  compose logs --tail 40 app || true
  fail "应用未在预期时间内就绪（已打印 app 日志尾部）"
fi

# 制品版本标识 —— 不走 git 后不再有 commit 可报，以 compose 文件时间戳 + 镜像 tag（上面那行）作为版本标识。
# 两者都随本次制品走，足以回答"线上跑的是哪一版"。
mount_version="compose 时间戳 $(date -r "$COMPOSE_FILE" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "?")"
mount_counts="nginx-conf($(count_files deploy/nginx/nginx.conf)) nginx-html($(count_files deploy/nginx/html)) pg-init($(count_files deploy/postgres/init))"

echo
echo "[deploy] ✅ 部署完成，健康检查通过: $HEALTH_URL"
echo "[deploy]    发布路径: $ROOT"
echo "[deploy]    镜像: $DEPLOYED_APP_IMAGE"
echo "[deploy]    制品: $mount_version"
echo "[deploy]    挂载文件数: $mount_counts"
echo "[deploy]    手动冒烟：浏览器打开 http://<ECS-IP>/ → 登录 → 上传一份文档 → 等状态 ready → 提问并看引用"
echo "[deploy]    查看日志：docker compose -f $COMPOSE_FILE logs -f app"
