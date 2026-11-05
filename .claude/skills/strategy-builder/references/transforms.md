# 变换函数目录

变换把原始因子值映射到 0-100 的统一得分空间。注册表在 `apps/api/src/quant_etf_api/engine/transforms.py`
（`_BUILTIN_TRANSFORMS` 与参数化的 `linear(lo,hi)`）；写错名字会在配置校验期被拒绝。

## 内置变换函数

| 变换名 | 映射逻辑 | 适用场景 |
|--------|----------|----------|
| `invert_percentile` | 100 − value，越低（越便宜）得分越高 | 估值百分位类因子 |
| `momentum_score` | 分段线性：> 15% → 95，> 10% → 85，> 5% → 70，> 2% → 60，> 0% → 50，> −2% → 40，> −5% → 30，> −10% → 20，≤ −10% → 10 | 收益率 / 动量 |
| `volume_score` | 分段映射：< 0.3 → 10，< 0.5 → 20，< 0.8 → 35，< 1.0 → 50，< 1.3 → 70，< 1.5 → 80，< 2.0 → 85，< 3.0 → 70，≥ 3.0 → 50 | 量比 |
| `trend_score` | 50 + 偏离 × 5：−10% → 0，0% → 50，+10% → 100 | 均线乖离（`ma_deviation`） |
| `clamp_0_100` | 截断到 [0, 100] | 原值已在 0-100 附近的因子 |
| `erp_score` | 分段线性：> 5 → 95，> 3 → 80，> 2 → 65，> 1 → 50，> 0 → 35，≤ 0 → 15 | `erp` |
| `drawdown_score` | 分段线性：0 → 100，−5 → 80，−10 → 60，−15 → 40，−20 → 20，≤ −25 → 5 | `drawdown`（值为负） |
| `rsrs_score` | z ≤ −2 → 0，z ≥ +2 → 100，中间 50 + z × 25 | `rsrs` 放择时时**必须**使用 |
| `resonance_score` | 共振比 > 1 → 100，否则 0 | `pv_resonance` |
| `linear(lo,hi)` | (v − lo) / (hi − lo) × 100，截断到 [0, 100]；lo > hi 时方向反转 | 内置变换的饱和区间不合适时，如年线乖离用 `linear(-20,20)`、波动率用 `linear(5,40)` |

## 使用方式

在 `score.transforms` 或 `timing.transforms` 中按引用名指定：

```json
{
  "factor_aliases": {"mom20": {"template_id": "return", "params": {"period": 20}}},
  "score": {
    "factors": {"mom20": 1.0, "pe_percentile": 0.5},
    "transforms": {"mom20": "momentum_score", "pe_percentile": "invert_percentile"}
  }
}
```

- 未指定变换的因子直接用原值参与加权。
- **方向只表达一次**：`invert_percentile` 已把"低分位"变成高分，配正权重即可；absolute 模式下负权重会再按 `100 − 得分`
  反转一次，两者叠加等于没有反转。
- 择时加权模式里，原值不在 0-100 的因子（收益率、RSRS、量比、波动率等，模板未声明 `native_0_100`）必须配变换，否则校验报错；
  RSI、市场宽度、估值 / ERP 百分位原值即 0-100，可不配。规则模式比较原值，不使用变换。
- `rank` / `zscore` 评分模式先逐因子做横截面标准化，不需要为统一量纲再配变换。
