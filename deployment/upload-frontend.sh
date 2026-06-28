#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ZIP_FILE="${SCRIPT_DIR}/dist.zip"
NGINX_ROOT="/var/www/quant-etf"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
err()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; }

check_deps() {
    for cmd in unzip nginx; do
        if ! command -v $cmd &>/dev/null; then
            err "$cmd 未安装或不在 PATH 中"
            exit 1
        fi
    done

    if [ ! -f "$ZIP_FILE" ]; then
        err "${ZIP_FILE} 不存在，请先在本地执行 npm run build，将 dist 目录压缩为 dist.zip 并上传到服务器本脚本同目录"
        exit 1
    fi
}

deploy() {
    log ">>> 解压 dist.zip..."
    unzip -o "$ZIP_FILE" -d "${SCRIPT_DIR}/dist-tmp"

    log ">>> 部署前端静态文件到 ${NGINX_ROOT}..."
    sudo rm -rf "${NGINX_ROOT:?}"/*
    sudo cp -r "${SCRIPT_DIR}/dist-tmp/dist/"* "${NGINX_ROOT}/"

    log ">>> 清理临时文件..."
    rm -rf "${SCRIPT_DIR}/dist-tmp"

    log ">>> 检查 nginx 配置..."
    sudo nginx -t

    log ">>> 重载 nginx..."
    sudo nginx -s reload

    log "nginx 重载完成"
}

main() {
    log "============================================"
    log "  量化系统前端部署"
    log "  时间:  $(date '+%Y-%m-%d %H:%M:%S')"
    log "============================================"

    check_deps
    deploy

    log "============================================"
    log "  部署完成!"
    log "============================================"
}

main "$@"
