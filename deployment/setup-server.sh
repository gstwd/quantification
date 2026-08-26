#!/usr/bin/env bash
# =============================================================================
# 服务器环境初始化脚本（首次部署前执行一次）
# 适用于 Ubuntu 22.04+ / Debian 12+
#
# 注意：前端在本地构建后上传，服务器无需 Node.js 环境。
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# 检查 root 权限
if [ "$(id -u)" -ne 0 ]; then
    err "请使用 sudo 执行此脚本"
fi

# =============================================================================
# 创建目录结构
# =============================================================================
log "创建目录..."
mkdir -p /opt/quant-etf
mkdir -p /var/www/quant-etf
mkdir -p /var/log/quant-etf

# =============================================================================
# 生成 .env 配置
# =============================================================================
if [ ! -f /opt/quant-etf/.env ]; then
    log "生成 .env 配置..."
    cat > /opt/quant-etf/.env << EOF
# 指数量化系统生产环境配置
QUANT_ETF_DATABASE_URL=postgresql+psycopg://quant_etf:${DB_PASS}@localhost:5432/quant_etf
QUANT_ETF_APP_ENV=production
QUANT_ETF_APP_HOST=127.0.0.1
QUANT_ETF_APP_PORT=8000
QUANT_ETF_CORS_ORIGINS=["http://localhost"]
QUANT_ETF_API_PREFIX=/api
QUANT_ETF_SCHEDULE_ENABLED=true
QUANT_ETF_SCHEDULE_TIME=17:30
QUANT_ETF_STARTUP_FILL_ENABLED=true
QUANT_ETF_LOG_LEVEL=INFO
QUANT_ETF_LOG_FILE=/var/log/quant-etf/api.log
EOF
    chown quant-etf:quant-etf /opt/quant-etf/.env
    chmod 600 /opt/quant-etf/.env
else
    warn "/opt/quant-etf/.env 已存在，跳过生成"
fi

# =============================================================================
# 安装 systemd 服务
# =============================================================================
log "安装 systemd 服务..."
if [ -f "$SCRIPT_DIR/quant-etf-api.service" ]; then
    cp "$SCRIPT_DIR/quant-etf-api.service" /etc/systemd/system/
    systemctl daemon-reload
    log "systemd 服务已安装"
else
    warn "未找到 quant-etf-api.service，跳过 systemd 配置"
fi
