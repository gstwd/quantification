#!/usr/bin/env bash
# =============================================================================
# 首次部署脚本（仅后端）
# 从 git 仓库拉取代码 → 安装依赖 → 数据库迁移 → 启动服务
#
# 前置条件：前端静态文件已上传至 /var/www/quant-etf/
#          （在本地执行 upload-frontend.sh 完成上传）
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# =============================================================================
# 配置项
# =============================================================================
GIT_REPO="${GIT_REPO:-git@github.com:your-org/quant-etf.git}"  # Git 仓库地址
GIT_BRANCH="${GIT_BRANCH:-main}"                                 # 分支
PROJECT_ROOT="/opt/quant-etf"
WEB_ROOT="/var/www/quant-etf"
LOG_DIR="/var/log/quant-etf"

# =============================================================================
# 1. 前置检查
# =============================================================================
log "开始首次部署..."

if [ ! -d "$PROJECT_ROOT" ]; then
    err "项目目录 $PROJECT_ROOT 不存在，请先执行 setup-server.sh"
fi

if [ ! -f "$WEB_ROOT/index.html" ]; then
    warn "前端文件未上传到 $WEB_ROOT/，请先执行 upload-frontend.sh"
    warn "继续部署后端，前端页面将无法访问..."
fi

# =============================================================================
# 2. 克隆 / 更新代码
# =============================================================================
if [ -d "$PROJECT_ROOT/.git" ]; then
    log "Git 仓库已存在，执行 git pull..."
    cd "$PROJECT_ROOT"
    git fetch origin
    git checkout "$GIT_BRANCH"
    git pull origin "$GIT_BRANCH"
else
    log "克隆代码仓库..."
    git clone --branch "$GIT_BRANCH" "$GIT_REPO" "$PROJECT_ROOT"
fi

# 记录当前 commit
CURRENT_COMMIT=$(cd "$PROJECT_ROOT" && git rev-parse --short HEAD)
log "当前 commit: $CURRENT_COMMIT"
echo "$CURRENT_COMMIT" > "$PROJECT_ROOT/.deploy-commit"

# =============================================================================
# 3. 安装后端依赖
# =============================================================================
log "安装后端依赖..."
cd "$PROJECT_ROOT/apps/api"

# 创建虚拟环境（如果不存在）
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# 激活虚拟环境并安装
source .venv/bin/activate
pip install --upgrade pip
pip install -e "."

# =============================================================================
# 4. 数据库迁移
# =============================================================================
log "执行数据库迁移..."
source .venv/bin/activate
alembic upgrade head

# =============================================================================
# 5. 同步种子数据
# =============================================================================
log "同步因子定义和指数种子数据..."
source .venv/bin/activate
python -m quant_etf_api.cli init-factors
python -m quant_etf_api.cli init-indexes

# =============================================================================
# 6. 启动服务
# =============================================================================
log "启动服务..."

if [ -f /etc/systemd/system/quant-etf-api.service ]; then
    systemctl daemon-reload
    systemctl enable quant-etf-api
    systemctl restart quant-etf-api
else
    warn "systemd 服务未安装，请先执行 setup-server.sh"
fi

# 重载 Nginx
nginx -t && systemctl reload nginx

# =============================================================================
# 7. 健康检查
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
    fi
    sleep 2
done

echo ""
echo "============================================"
log "部署完成！"
echo ""
echo "  前端地址: http://<your-server-ip>"
echo "  API 文档: http://<your-server-ip>/api/docs"
echo "  健康检查: http://<your-server-ip>/api/health"
echo ""
echo "  查看日志: sudo journalctl -u quant-etf-api -f"
echo "  应用日志: tail -f $LOG_DIR/api.log"
echo "============================================"