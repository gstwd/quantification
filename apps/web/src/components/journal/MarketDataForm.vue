<template>
  <div class="market-data-form">
    <div class="form-section">
      <h4 class="section-title">市场广度</h4>
      <p class="section-hint">来源：同花顺 → 行情中心 → 市场概况（快捷键 61/63）</p>
      <div class="field-row">
        <div class="field">
          <label>上涨家数</label>
          <input type="number" min="0" max="10000" :value="modelValue.market_up_stocks" @input="update('market_up_stocks', toInt($event))" placeholder="如 2100" />
        </div>
        <div class="field">
          <label>下跌家数</label>
          <input type="number" min="0" max="10000" :value="modelValue.market_down_stocks" @input="update('market_down_stocks', toInt($event))" placeholder="如 2800" />
        </div>
        <div class="field">
          <label>平盘家数</label>
          <input type="number" min="0" max="10000" :value="modelValue.market_flat_stocks" @input="update('market_flat_stocks', toInt($event))" placeholder="如 200" />
        </div>
        <div class="field">
          <label>涨停家数</label>
          <input type="number" min="0" max="500" :value="modelValue.limit_up_stocks" @input="update('limit_up_stocks', toInt($event))" placeholder="如 45" />
        </div>
        <div class="field">
          <label>跌停家数</label>
          <input type="number" min="0" max="500" :value="modelValue.limit_down_stocks" @input="update('limit_down_stocks', toInt($event))" placeholder="如 12" />
        </div>
      </div>
    </div>

    <div class="form-section">
      <h4 class="section-title">成交与资金</h4>
      <p class="section-hint">成交额：同花顺首页顶部。北向：数据中心 → 沪深港通。两融：前一日数据。</p>
      <div class="field-row">
        <div class="field">
          <label>全市场成交额（亿）</label>
          <input type="number" min="0" step="0.01" :value="modelValue.total_turnover_yi" @input="update('total_turnover_yi', toFloat($event))" placeholder="如 8500" />
        </div>
        <div class="field">
          <label>较前日变化（%）</label>
          <input type="number" step="0.01" :value="modelValue.turnover_vs_prev_pct" @input="update('turnover_vs_prev_pct', toFloat($event))" placeholder="如 -5.2" />
        </div>
        <div class="field">
          <label>北向资金净流入（亿）</label>
          <input type="number" step="0.01" :value="modelValue.north_bound_net_yi" @input="update('north_bound_net_yi', toFloat($event))" placeholder="如 -15.3" />
        </div>
        <div class="field">
          <label>两融余额变化（亿）</label>
          <input type="number" step="0.01" :value="modelValue.margin_balance_change_yi" @input="update('margin_balance_change_yi', toFloat($event))" placeholder="T-1日数据（可选）" />
        </div>
      </div>
    </div>

    <div class="form-section">
      <h4 class="section-title">风格判断</h4>
      <div class="field-row">
        <div class="field">
          <label>大小盘风格</label>
          <select :value="modelValue.size_style ?? ''" @change="update('size_style', ($event.target as HTMLSelectElement).value || null)">
            <option value="">-- 未选择 --</option>
            <option value="large_cap">大盘主导（沪深300/上证50 > 中证1000 超0.5%）</option>
            <option value="small_cap">小盘主导（中证1000 > 沪深300 超0.5%）</option>
            <option value="balanced">均衡（差距在0.5%以内）</option>
          </select>
        </div>
        <div class="field">
          <label>成长/价值风格</label>
          <select :value="modelValue.growth_style ?? ''" @change="update('growth_style', ($event.target as HTMLSelectElement).value || null)">
            <option value="">-- 未选择 --</option>
            <option value="growth">成长主导（科创/创业板 > 红利 超1%）</option>
            <option value="value">价值主导（银行/红利 > 成长 超0.5%）</option>
            <option value="balanced">均衡</option>
          </select>
        </div>
        <div class="field">
          <label>行业主导方向</label>
          <select :value="modelValue.sector_leading ?? ''" @change="update('sector_leading', ($event.target as HTMLSelectElement).value || null)">
            <option value="">-- 未选择 --</option>
            <option value="tech">科技主导</option>
            <option value="dividend">红利主导</option>
            <option value="cyclical">周期主导</option>
            <option value="financial">金融主导</option>
            <option value="consumption">消费主导</option>
            <option value="healthcare">医药主导</option>
            <option value="balanced">无明显主导</option>
          </select>
        </div>
      </div>
    </div>

    <div class="form-section">
      <h4 class="section-title">行业表现</h4>
      <p class="section-hint">来源：同花顺 → 板块 → 行业板块 → 按涨幅排序。用中文逗号分隔前5个。</p>
      <div class="field-row">
        <div class="field flex-1">
          <label>领涨行业前5</label>
          <input type="text" :value="modelValue.top_sectors ?? ''" @input="update('top_sectors', ($event.target as HTMLInputElement).value || null)" placeholder="半导体,通信设备,计算机应用,传媒,电子制造" />
        </div>
        <div class="field flex-1">
          <label>领跌行业前5</label>
          <input type="text" :value="modelValue.bottom_sectors ?? ''" @input="update('bottom_sectors', ($event.target as HTMLInputElement).value || null)" placeholder="银行,煤炭开采,房地产开发,钢铁,建筑材料" />
        </div>
      </div>
    </div>

    <div class="form-section">
      <div class="field-row">
        <div class="field">
          <label>数据来源</label>
          <input type="text" :value="modelValue.data_source ?? ''" @input="update('data_source', ($event.target as HTMLInputElement).value || null)" placeholder="如 同花顺 + Wind" />
        </div>
        <div class="field flex-1">
          <label>补充备注</label>
          <input type="text" :value="modelValue.notes ?? ''" @input="update('notes', ($event.target as HTMLInputElement).value || null)" placeholder="其他需要记录的市场数据说明" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { JournalMarketData } from '../../types/api'

const props = defineProps<{
  modelValue: JournalMarketData
}>()

const emit = defineEmits<{
  'update:modelValue': [value: JournalMarketData]
}>()

function update(key: string, value: unknown): void {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function toInt(event: Event): number | null {
  const v = (event.target as HTMLInputElement).value
  return v === '' ? null : parseInt(v, 10)
}

function toFloat(event: Event): number | null {
  const v = (event.target as HTMLInputElement).value
  return v === '' ? null : parseFloat(v)
}
</script>

<style scoped>
.market-data-form {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
}
.form-section {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.form-section:last-child {
  margin-bottom: 0;
  padding-bottom: 0;
  border-bottom: none;
}
.section-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}
.section-hint {
  margin: 0 0 10px;
  font-size: 11px;
  color: var(--text-muted);
}
.field-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 140px;
}
.field.flex-1 { flex: 1; min-width: 200px; }
.field label {
  font-size: 12px;
  color: var(--text-muted);
}
.field input, .field select {
  padding: 6px 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 13px;
  outline: none;
}
.field input:focus, .field select:focus {
  border-color: var(--accent);
}
.field input::placeholder {
  color: var(--text-muted);
  opacity: 0.5;
}
</style>
