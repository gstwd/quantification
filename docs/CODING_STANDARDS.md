# 编码规范

生成或修改代码时必须遵守本文档。规范分为通用原则、Python 后端、Vue/TypeScript 前端三部分。

---

## 通用原则

- **注释语言**：所有注释、文档字符串一律使用**中文**
- **注释维护**：修改代码时同步改写相关注释；过时注释必须改写或删除，不保留描述过时行为的文字
- **只写现状**：注释、文档字符串、CLI 帮助与文档只写现状和原因，不写迭代经过（"改造前""此前""旧实现""现在统一"），不引用审查轮次编号；变更经过写进提交信息或[决策记录](architecture/决策记录.md)
- **命名约定**：Python `snake_case`，TypeScript/Vue `camelCase`，Vue 组件文件名 `PascalCase`
- **类型标注**：所有公开函数/方法必须标注参数类型和返回类型，禁止使用 `any`（前端）
- **行长度**：Python 100 字符（ruff 已配置），TypeScript/Vue 120 字符

## 时间与日期规范

系统必须明确区分“时间戳”和“业务日期”，不得混用：

- **时间戳**表示一个全球绝对时刻，统一使用 UTC。后端生成时间戳只用
  `quant_etf_api.infra.time` 中的函数：`timestamptz` 列（新增时间戳列一律用
  `DateTime(timezone=True)`）写 `utcnow_aware()`，存量 `timestamp without time zone`
  列写 `utcnow()`（UTC naive）；禁止使用 `datetime.now()`、`datetime.utcnow()`
  或依赖服务器本地时区的时间函数。
- 数据库连接时区固定为 UTC。API 时间字段使用
  `quant_etf_api.schemas.types.UtcDatetime`，确保 JSON 输出带 `+00:00`。
- **业务日期**（交易日、行情日期、回测起止日期、数据截止日期）属于 A 股市场日期，
  统一按北京时间 `Asia/Shanghai` 计算。后端使用
  `quant_etf_api.infra.time.today_cn()`，禁止直接使用 `date.today()`。
- 调度器的配置时间（如 `schedule_time`、`ai_schedule_time`）解释为北京时间，
  不得依赖操作系统本地时区。
- 前端展示后端时间戳时必须显式指定 `timeZone: 'Asia/Shanghai'`。日期字符串
  `YYYY-MM-DD` 不得直接交给 `new Date(value)` 解析，也不得用
  `toISOString().slice(0, 10)` 生成业务日期；统一使用 `src/utils/date.ts` 中的
  `todayCn`、`shiftDateCn`、`parseDateCn` 和 `formatCnTime`。
- 时间戳接口字段、业务日期接口字段应保持语义清晰：前者使用 ISO datetime，后者使用
  `YYYY-MM-DD`，不要在接口中用 datetime 承载交易日。

## 领域层与服务层边界

- **领域层（`domain/`）**承载不依赖数据库、HTTP、FastAPI 或服务容器的业务规则和纯计算，
  例如组合收益、基准收益、行情质量判定、换手率等。领域函数应通过参数接收数据，保持可
  测试、可复用，不负责读取数据、提交事务或组织后台任务。
- **服务层（`services/`）**负责用 repository/client 读取和写入数据，编排领域规则，处理
  重试、日志、事务和 DTO/API 响应转换，不重复实现领域计算。
- 历史导入路径如必须兼容，只能保留薄的 re-export 转发模块；新代码统一从对应的
  `domain` 模块导入。纯领域代码不应反向依赖 `services`。

---

## Python 后端规范

### 注释与文档字符串

每个类、函数、方法必须有中文 docstring，采用 Google 风格。

**类：**
```python
class BacktestReconcileService:
    """回测独立对账服务：读取落库回测与原始行情，交给领域层独立复算后比对。

    只读：不写库、不入队、不修改回测记录。
    """
```

**函数/方法（含 Args / Returns / Raises）：**
```python
def find_index_daily_bars(self, index_code: str, start: date, end: date) -> list[DailyBar]:
    """读取指定指数区间内的日线行情。

    只读查询：未命中返回空列表，不触发外部拉取或入队补数（补数统一走数据管理入口）。

    Args:
        index_code: 指数代码，如 "000300"。
        start: 起始日期（含）。
        end: 结束日期（含）。

    Returns:
        按日期升序排列的日线数据列表，无数据时返回空列表。

    Raises:
        ValueError: 当 start > end 时抛出。
    """
```

**行内注释**（说明"为什么"，而非"是什么"）：
```python
# 剔除数据不足 60 个交易日的指数，避免历史数据不足导致因子失真
df = df[df["bar_days"] >= 60]

raw = resp.get("qfqday") or resp.get("day", [])  # 接口返回键名不稳定，兼容两种格式
```

### 类型标注

- 使用 Python 3.10+ 风格：`list[T]`、`dict[K, V]`、`T | None`（不用 `List`、`Optional`）
- 文件顶部加 `from __future__ import annotations`
- 所有公开方法标注返回类型，私有辅助方法（`_` 前缀）也应标注

### 错误处理

**服务层**：数据库查询放在 repository，服务层捕获具体异常、记录日志，向上抛出业务异常或返回 `None`：
```python
try:
    return self._index_repo.find_by_code(index_code)  # 未命中返回 None
except SQLAlchemyError:
    logger.exception("查询指数 %s 失败", index_code)
    raise
```

**路由层**：将业务异常转换为 HTTPException，使用语义化状态码：
```python
index = service.get_index(index_code)
if index is None:
    raise HTTPException(status_code=404, detail=f"指数 {index_code} 不存在")
```

- `404`：资源不存在
- `409`：冲突（如重复创建）
- `422`：参数校验失败（Pydantic 自动处理；研究期 / 验证期越界等业务校验同样返回 422）
- `503`：依赖不可用（如交易日历不可用时的 `TradingCalendarUnavailableError`）
- `500`：不可预期的服务端错误（不要在路由层 catch all）

### 导入顺序

```python
from __future__ import annotations  # 1. future

import logging                        # 2. 标准库
import threading
from datetime import date

from fastapi import Depends, HTTPException  # 3. 第三方库
from sqlalchemy.orm import Session

from quant_etf_api.services import IngestService  # 4. 本项目
from quant_etf_api.api.deps import get_db
```

### 测试规范

- 测试文件命名：`test_<module>.py`，放在对应的 `unit/` 或 `integration/` 目录
- 测试类命名：`Test<被测类或功能>`，方法命名：`test_<场景描述>`
- 优先使用 `@pytest.mark.parametrize` 覆盖边界值，避免重复代码
- 单元测试用替身隔离数据库会话与外部 HTTP；依赖 SQL 语义的查询需在真实数据库上验证
- 口径类行为（记账、执行模型、日历、研究期）要有能定位到具体字段的回归测试；冻结口径由
  `test_caliber_golden.py` 与 `test_backtest_reconcile.py` 守卫

```python
@pytest.mark.parametrize("volume,expected", [
    (0.0, 0.0),
    (1.0, 0.2),
    (5.0, 1.0),
])
def test_volume_probability(volume: float, expected: float) -> None:
    """验证成交量概率计算的边界值。"""
    assert volume_probability(volume) == pytest.approx(expected)
```

---

## Vue / TypeScript 前端规范

### 注释与文档字符串

**Vue 组件**（`<script setup>` 顶部必须有组件说明）：
```vue
<script setup lang="ts">
/**
 * 指数详情页面。
 *
 * 展示单只指数的基本信息、日线行情图及估值历史。
 * 数据通过 src/api/market_data.ts 的封装函数读取。
 */

// 当前指数代码，从路由参数中读取
const indexCode = computed(() => route.params.indexCode as string)

// 控制行情图加载状态，显示骨架屏
const isLoading = ref(false)
</script>
```

**TypeScript 函数**（JSDoc 风格，中文）：
```typescript
/**
 * 将后端返回的净值数据格式化为 ECharts 折线图所需的 series 配置。
 *
 * @param rawData - 后端返回的原始净值数组
 * @param benchmarkCode - 基准指数代码，如 "000300"（沪深300）
 * @returns ECharts series 配置对象数组
 */
function formatNavChartData(rawData: NavRecord[], benchmarkCode: string): EChartsSeries[] {
```

**Pinia Store**（只用于跨页面共享的状态，现有 `stores/backtests`、`stores/strategies`、`stores/toast`）：
```typescript
export const useStrategyStore = defineStore('strategies', () => {
  // 策略列表缓存：列表页与详情页共享，避免重复请求
  const items = ref<StrategySummary[]>([])

  /**
   * 重新加载策略列表。
   */
  async function loadAll(): Promise<void> {
```

### 类型标注

- 禁止使用 `any`；确实无法确定类型时使用 `unknown` 并做类型收窄
- 接口定义放在 `src/types/api.ts`，按功能分组
- 使用 `import type { ... }` 导入纯类型，避免运行时开销

### 错误处理

**API 层**（`src/api/*.ts`）：不吞掉错误，由调用方（页面或 store）决定如何处理：
```typescript
export async function fetchIndexDetail(code: string): Promise<IndexDetail> {
  const { data } = await apiClient.get<IndexDetail>(`/indexes/${code}`)
  return data
}
```

**调用方**（页面或 store）：捕获错误，存入 `error` 状态并展示：
```typescript
const error = ref<string | null>(null)

async function loadOne(code: string): Promise<void> {
  isLoading.value = true
  error.value = null
  try {
    detail.value = await fetchIndexDetail(code)
  } catch (e) {
    // 将错误信息暴露给组件，而非静默失败
    error.value = e instanceof Error ? e.message : '加载失败，请稍后重试'
  } finally {
    isLoading.value = false
  }
}
```

**模板**：展示错误状态：
```vue
<template>
  <div v-if="error" class="error-tip">{{ error }}</div>
  <div v-else-if="isLoading">加载中...</div>
  <div v-else><!-- 正常内容 --></div>
</template>
```

### Vue 组件结构顺序

```
<template>
<script setup lang="ts">
  1. 组件说明注释（JSDoc）
  2. import 语句
  3. defineProps / defineEmits
  4. store 初始化
  5. ref / reactive 声明
  6. computed 声明
  7. 函数定义
  8. 生命周期钩子（onMounted 等）
<style scoped>
```

### 导入顺序

```typescript
// 1. Vue 核心
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

// 2. 第三方库
import { defineStore } from 'pinia'

// 3. 本项目 API / stores / types（项目未配置路径别名，使用相对路径）
import { fetchIndexDetail } from '../api/market_data'
import { useStrategyStore } from '../stores/strategies'
import type { IndexDetail } from '../types/api'
```

---

## 因子相关专项

- **研发生命周期**：因子中心=正式因子（可配置可回测）；因子中心菜单下的
  研究页=独立实验、只共享只读数据。新因子必须遵循
  [`docs/architecture/因子研发与集成指引.md`](architecture/因子研发与集成指引.md)。
- **元数据必填**：每个 `FactorTemplate` 必须声明 `value_shape`（asset/market）与
  `usage`（timing/score/filter/rank 的子集）；参数化模板用 `parameter_schema` 声明参数并给出
  `default_params`，回望窗口由 `base_lookback_days` / `compute_lookback_days` 给出。
- **位置限制生效**：配置校验按 usage 拒绝错位引用；
  前端策略表单各模块的因子下拉必须与后端 usage 同步过滤。
- **前端同步**：后端新增任何策略配置能力时，必须同步策略表单与详情展示；
  不允许后端支持而前端无可选项。

---

## 快速检查清单

生成或修改代码前确认：

**Python：**
- [ ] 每个类有中文 docstring
- [ ] 每个公开函数/方法有中文 docstring（含 Args / Returns，有异常时含 Raises）
- [ ] 复杂逻辑有行内中文注释说明原因
- [ ] 所有参数和返回值有类型标注
- [ ] 路由层使用语义化 HTTP 状态码
- [ ] 过时注释已改写或删除；注释只写现状与原因，无审查编号

**TypeScript / Vue：**
- [ ] 每个 TypeScript 函数有中文 JSDoc 注释
- [ ] Vue 组件 `<script setup>` 顶部有组件用途说明
- [ ] 无 `any` 类型
- [ ] 发起请求的页面或 store 有 `error` 状态，模板有错误展示
- [ ] 过时注释已改写或删除；注释只写现状与原因，无审查编号
