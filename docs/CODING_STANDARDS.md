# 编码规范

生成或修改代码时必须遵守本文档。规范分为通用原则、Python 后端、Vue/TypeScript 前端三部分。

---

## 通用原则

- **注释语言**：所有注释、文档字符串一律使用**中文**
- **重构规则**：修改现有代码时只能**更新**注释，严禁删除注释
- **命名约定**：Python `snake_case`，TypeScript/Vue `camelCase`，Vue 组件文件名 `PascalCase`
- **类型标注**：所有公开函数/方法必须标注参数类型和返回类型，禁止使用 `any`（前端）
- **行长度**：Python 100 字符（ruff 已配置），TypeScript/Vue 120 字符

---

## Python 后端规范

### 注释与文档字符串

每个类、函数、方法必须有中文 docstring，采用 Google 风格。

**类：**
```python
class IngestService:
    """行情数据摄取服务，负责从外部接口拉取并持久化 ETF 日线和份额数据。

    首次请求时从外部 API 获取数据写入数据库，后续请求直接读库。
    使用 threading.Lock 防止并发冷启动时重复拉取。
    """
```

**函数/方法（含 Args / Returns / Raises）：**
```python
def fetch_daily_bars(self, code: str, start: date, end: date) -> list[DailyBar]:
    """获取指定 ETF 的日线行情数据。

    优先从数据库读取，若无数据则从腾讯行情接口拉取并持久化。

    Args:
        code: ETF 代码，如 "510300"。
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
# 剔除上市不足 60 个交易日的新 ETF，避免历史数据不足导致因子失真
df = df[df["listing_days"] >= 60]

raw = resp.get("qfqday") or resp.get("day", [])  # 接口返回键名不稳定，兼容两种格式
```

### 类型标注

- 使用 Python 3.10+ 风格：`list[T]`、`dict[K, V]`、`T | None`（不用 `List`、`Optional`）
- 文件顶部加 `from __future__ import annotations`
- 所有公开方法标注返回类型，私有辅助方法（`_` 前缀）也应标注

### 错误处理

**服务层**：捕获具体异常，记录日志，向上抛出业务异常或返回 `None`：
```python
try:
    return self._db.query(EtfModel).filter_by(code=code).one()
except NoResultFound:
    return None
except Exception:
    logger.exception("查询 ETF %s 失败", code)
    raise
```

**路由层**：将业务异常转换为 HTTPException，使用语义化状态码：
```python
etf = service.get_etf(etf_code)
if etf is None:
    raise HTTPException(status_code=404, detail=f"ETF {etf_code} 不存在")
```

- `404`：资源不存在
- `409`：冲突（如重复创建）
- `422`：参数校验失败（Pydantic 自动处理，无需手动抛出）
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
- 不 mock 数据库（集成测试使用真实 DB 连接）；外部 HTTP 接口可 mock

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
 * ETF 详情页面。
 *
 * 展示单只 ETF 的基本信息、日线行情图及信号历史。
 * 数据通过 useEtfStore 统一管理，组件不直接调用 API。
 */

// 当前 ETF 代码，从路由参数中读取
const etfCode = computed(() => route.params.code as string)

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

**Pinia Store**：
```typescript
export const useEtfStore = defineStore('etf', () => {
  // 已加载的 ETF 元数据缓存，避免重复请求
  const cache = ref<Map<string, EtfDetail>>(new Map())

  /**
   * 加载单只 ETF 的完整详情，优先读取缓存。
   *
   * @param code - ETF 代码
   */
  async function loadOne(code: string): Promise<void> {
```

### 类型标注

- 禁止使用 `any`；确实无法确定类型时使用 `unknown` 并做类型收窄
- 接口定义放在 `src/types/api.ts`，按功能分组
- 使用 `import type { ... }` 导入纯类型，避免运行时开销

### 错误处理

**API 层**：不吞掉错误，让 store 决定如何处理：
```typescript
export async function fetchEtfDetail(code: string): Promise<EtfDetail> {
  const { data } = await apiClient.get<EtfDetail>(`/etfs/${code}`)
  return data
}
```

**Store 层**：捕获错误，存入 `error` 状态，供组件展示：
```typescript
const error = ref<string | null>(null)

async function loadOne(code: string): Promise<void> {
  isLoading.value = true
  error.value = null
  try {
    detail.value = await fetchEtfDetail(code)
  } catch (e) {
    // 将错误信息暴露给组件，而非静默失败
    error.value = e instanceof Error ? e.message : '加载失败，请稍后重试'
  } finally {
    isLoading.value = false
  }
}
```

**组件层**：展示 store 中的错误状态：
```vue
<template>
  <div v-if="store.error" class="error-tip">{{ store.error }}</div>
  <div v-else-if="store.isLoading">加载中...</div>
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

// 3. 本项目 API / stores / types
import { fetchEtfDetail } from '@/api/etfs'
import { useSignalStore } from '@/stores/signals'
import type { EtfDetail } from '@/types/api'
```

---

## 快速检查清单

生成或修改代码前确认：

**Python：**
- [ ] 每个类有中文 docstring
- [ ] 每个公开函数/方法有中文 docstring（含 Args / Returns，有异常时含 Raises）
- [ ] 复杂逻辑有行内中文注释说明原因
- [ ] 所有参数和返回值有类型标注
- [ ] 路由层使用语义化 HTTP 状态码
- [ ] 重构时已更新（而非删除）原有注释

**TypeScript / Vue：**
- [ ] 每个 TypeScript 函数有中文 JSDoc 注释
- [ ] Vue 组件 `<script setup>` 顶部有组件用途说明
- [ ] 无 `any` 类型
- [ ] Store 有 `error` 状态，组件有错误展示
- [ ] 重构时已更新（而非删除）原有注释
