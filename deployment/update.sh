#!/usr/bin/env bash
# =============================================================================
# 更新部署脚本（仅后端）
# 拉取最新代码 → 更新依赖 → 数据库迁移 → 重启服务
#
# 如有前端变更，需先本地构建并通过 upload-frontend.sh 上传，再执行本脚本。
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

PROJECT_ROOT="/opt/quant-etf"
GIT_BRANCH="${GIT_BRANCH:-main}"

# =============================================================================
# 1. 记录当前版本
# =============================================================================
log "记录当前版本..."
cd "$PROJECT_ROOT"
PREVIOUS_COMMIT=$(cat "$PROJECT_ROOT/.deploy-commit" 2>/dev/null || git rev-parse --short HEAD)
log "当前版本: $PREVIOUS_COMMIT"

# =============================================================================
# 2. 拉取最新代码
# =============================================================================
log "拉取最新代码..."
git fetch origin
git checkout "$GIT_BRANCH"
git pull origin "$GIT_BRANCH"

NEW_COMMIT=$(git rev-parse --short HEAD)
log "新版本: $NEW_COMMIT"

if [ "$PREVIOUS_COMMIT" = "$NEW_COMMIT" ]; then
    log "代码无变更，跳过部署"
    exit 0
fi

# =============================================================================
# 3. 检查是否有前端变更
# =============================================================================
FRONTEND_CHANGED=false
if git diff --name-only "$PREVIOUS_COMMIT" "$NEW_COMMIT" | grep -q '^apps/web/'; then
    FRONTEND_CHANGED=true
    warn "检测到前端代码变更，请在本地执行 upload-frontend.sh 上传新前端文件"
fi

# =============================================================================
# 4. 更新后端依赖
# =============================================================================
log "检查后端依赖..."
cd "$PROJECT_ROOT/apps/api"
source .venv/bin/activate

if git diff --name-only "$PREVIOUS_COMMIT" "$NEW_COMMIT" | grep -q 'pyproject.toml'; then
    log "pyproject.toml 有变更，重新安装依赖..."
    pip install -e "."
else
    log "pyproject.toml 无变更，跳过依赖安装"
fi

# =============================================================================
# 5. 数据库迁移
# =============================================================================
log "执行数据库迁移..."
source .venv/bin/activate
alembic upgrade head

# =============================================================================
# 6. 同步种子数据（幂等操作）
# =============================================================================
log "同步因子定义和指数种子数据..."
source .venv/bin/activate
python -m quant_etf_api.cli init-factors

# =============================================================================
# 7. 重启后端服务
# =============================================================================
log "重启后端服务..."
systemctl restart quant-etf-api

# =============================================================================
# 8. 记录新版本
# =============================================================================
echo "$NEW_COMMIT" > "$PROJECT_ROOT/.deploy-commit"

# =============================================================================
# 9. 健康检查
# =============================================================================
log "等待服务就绪..."
sleep 3
MAX_RETRIES=10
for i in $(seq 1 $MAX_RETRIES); do
    if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
        log "后端服务就绪 ✓"
        break
    fi
    if [ "$i" -eq "$MAX_RETRIES" ]; then
        warn "后端服务未能在 ${MAX_RETRIES} 次尝试后就绪，请检查日志"
        exit 1
    fi
    sleep 2
done

echo ""
echo "============================================"
log "更新完成！"
echo ""
echo "  旧版本: $PREVIOUS_COMMIT"
echo "  新版本: $NEW_COMMIT"
if $FRONTEND_CHANGED; then
    warn "  提醒: 前端代码有变更，请尽快上传新前端文件！"
fi
echo "============================================"
