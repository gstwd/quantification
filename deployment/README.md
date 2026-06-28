# Quant ETF 裸机部署方案

## 架构概览

```
客户端浏览器
    │
    ▼
┌─────────────────────────────────────┐
│  Nginx (:80)                        │
│  ├── /          → 前端静态文件       │
│  └── /api/*     → 反向代理至 :8000  │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  Uvicorn (:8000)                    │
│  由 systemd 管理                    │
│  quant-etf-api.service              │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  PostgreSQL (:5432)                 │
│  数据库: quant_etf                  │
└─────────────────────────────────────┘
```

## 部署模式

- **后端**：服务器上拉取 Git 代码，裸机 Python 虚拟环境运行
- **前端**：本地构建，通过 rsync 上传至服务器 Nginx 静态目录
- **服务器不需要 Node.js 环境**

## 目录规划

| 路径 | 用途 |
|------|------|
| `/opt/quant-etf/` | Git 仓库根目录 |
| `/opt/quant-etf/apps/api/` | 后端代码 + Python 虚拟环境 |
| `/opt/quant-etf/.env` | 后端环境变量（生产配置） |
| `/var/www/quant-etf/` | 前端构建产物（Nginx 静态根） |
| `/var/log/quant-etf/` | 应用日志 |
| `/opt/quant-etf/deployment/` | 部署脚本 |

## 快速开始

### 1. 服务器环境初始化（仅首次，在服务器上执行）

```bash
# 将代码推送到 git 仓库后，SSH 登录服务器执行：
sudo bash deployment/setup-server.sh
```

此脚本会完成：系统包安装（Python/PostgreSQL/Nginx/Git，**不含 Node.js**）、数据库和用户创建、目录创建、Nginx 和 systemd 配置安装。

### 2. 本地构建并上传前端

```bash
# 在本地开发机上执行：
bash deployment/upload-frontend.sh user@your-server
```

此脚本会：安装 npm 依赖 → 类型检查 + 构建 → rsync 上传到服务器 `/var/www/quant-etf/`。

### 3. 服务器端部署后端

```bash
# SSH 登录服务器执行：
bash /opt/quant-etf/deployment/deploy.sh
```

此脚本会：git clone/pull 代码 → 创建 Python venv → 安装依赖 → 数据库迁移 → 同步种子数据 → 启动 systemd 服务。

### 4. 后续更新

```bash
# 仅后端有变更时：
bash /opt/quant-etf/deployment/update.sh

# 前端有变更时，先在本地执行：
bash deployment/upload-frontend.sh user@your-server

# 前后端同时有变更时，先上传前端，再更新后端
```

## 部署流程图

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  本地开发机   │     │  Git 仓库    │     │  云服务器     │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       │  git push          │                    │
       ├───────────────────►│                    │
       │                    │                    │
       │  npm run build     │                    │
       │  rsync dist/       │                    │
       ├────────────────────┼───────────────────►│ /var/www/quant-etf/
       │                    │                    │
       │                    │  git pull          │
       │                    │◄───────────────────┤ /opt/quant-etf/
       │                    │                    │
       │                    │                    │ pip install + alembic
       │                    │                    │ systemctl restart
```

## 日常运维

```bash
# 查看后端状态
sudo systemctl status quant-etf-api

# 重启后端
sudo systemctl restart quant-etf-api

# 查看后端日志
sudo journalctl -u quant-etf-api -f

# 查看应用日志
tail -f /var/log/quant-etf/api.log

# 重载 Nginx 配置
sudo nginx -t && sudo systemctl reload nginx

# 数据库迁移（代码更新后如需手动迁移）
cd /opt/quant-etf/apps/api
source .venv/bin/activate
alembic upgrade head
```

## 环境要求

**服务器**：
- 操作系统：Ubuntu 22.04+ / Debian 12+
- Python：3.11+
- PostgreSQL：15+
- Nginx：1.24+
- Git

**本地开发机**（仅构建前端时需要）：
- Node.js 20+
- rsync

## 配置说明

### 后端环境变量 (`.env`)

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `QUANT_ETF_DATABASE_URL` | PostgreSQL 连接串 | `postgresql+psycopg://...` |
| `QUANT_ETF_APP_ENV` | 运行环境 | `production` |
| `QUANT_ETF_APP_HOST` | 监听地址 | `127.0.0.1` |
| `QUANT_ETF_APP_PORT` | 监听端口 | `8000` |
| `QUANT_ETF_CORS_ORIGINS` | CORS 允许来源 | `["http://localhost"]` |
| `QUANT_ETF_LOG_FILE` | 日志文件路径 | `/var/log/quant-etf/api.log` |
| `QUANT_ETF_SCHEDULE_ENABLED` | 启用定时数据摄取 | `true` |
| `QUANT_ETF_SCHEDULE_TIME` | 每日摄取时间 | `17:30` |

### Nginx 配置

- 监听 80 端口
- 静态文件路径 `/var/www/quant-etf/`
- `/api/` 反向代理至 `http://127.0.0.1:8000/api/`
- 前端 SPA 路由回退（`try_files` → `index.html`）
