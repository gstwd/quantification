# Smoke tests

冒烟测试用于验证系统核心功能是否正常工作。

## 启动后端

```bash
cd apps/api
uvicorn quant_etf_api.main:app --reload --port 8000
```

## 验证步骤

1. 打开 Swagger UI：`http://localhost:8000/docs`

2. 健康检查：
   - `GET /api/health` — 返回 200 OK

3. 系统状态：
   - `GET /api/system/status` — 返回数据库连接状态、各数据表记录数

4. 策略管理：
   - `GET /api/strategies` — 返回策略列表
   - `POST /api/strategies/validate` — 校验一个策略配置 JSON

5. ETF 数据：
   - `GET /api/etfs` — 返回 ETF 宇宙列表
   - `GET /api/etfs/{code}` — 返回单只 ETF 详情

6. 指数数据：
   - `GET /api/indexes` — 返回基准指数列表
   - `GET /api/indexes/{code}/valuation` — 返回指数估值数据

7. 因子分析：
   - `GET /api/factors` — 返回因子定义列表
   - `GET /api/factors/{id}/cross-section` — 返回因子横截面数据
   - `GET /api/factors/{id}/ic` — 返回因子 IC 分析

8. 策略运行：
   - `GET /api/runs` — 返回运行历史列表

9. 回测：
   - `GET /api/backtests` — 返回回测列表
   - `POST /api/backtests` — 创建回测任务

## 启动前端

```bash
cd apps/web
npm run dev
```

## 前端验证步骤

1. 打开 `http://localhost:5173`

2. 仪表盘页面 — 确认系统概览正常渲染

3. ETF 列表页 (`/etfs`) — 确认列表加载

4. ETF 详情页 (`/etfs/{code}`) — 确认行情图和详情

5. 指数列表页 (`/indexes`) — 确认指数数据加载

6. 指数详情页 (`/indexes/{code}`) — 确认估值曲线图

7. 宏观数据页 (`/macro`) — 确认指标列表

8. 策略列表页 (`/strategies`) — 确认策略列表

9. 策略详情页 (`/strategies/{id}`) — 确认配置查看/编辑

10. 因子列表页 (`/factors`) — 确认因子列表

11. 因子详情页 (`/factors/{id}`) — 确认因子横截面和 IC 分析

12. 运行历史页 (`/runs`) — 确认运行记录

13. 回测列表页 (`/backtests`) — 确认回测列表

14. 回测创建页 (`/backtests/new`) — 确认创建表单

15. 回测详情页 (`/backtests/{id}`) — 确认绩效指标和图表
