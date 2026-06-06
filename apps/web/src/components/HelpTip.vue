<template>
  <!--
    通用指标提示组件。

    鼠标悬停（桌面端）或点击（移动端）显示指标含义说明。
    使用 Teleport 将气泡渲染到 body 层，避免被父级 overflow/z-index 遮挡。
  -->
  <span
    ref="triggerRef"
    class="help-tip"
    :class="{ 'help-tip--active': showTip }"
    @mouseenter="onEnter"
    @mouseleave="onLeave"
    @click.stop="onClick"
    @touchstart.stop="onClick"
  >
    <span class="help-tip__icon">{{ icon }}</span>
  </span>

  <!-- 通过 Teleport 渲染到 body，彻底解决遮挡和裁剪问题 -->
  <Teleport to="body">
    <Transition name="help-tip-fade">
      <div
        v-if="showTip"
        ref="tooltipRef"
        class="help-tip__tooltip"
        :class="`help-tip__tooltip--${actualPosition}`"
        :style="tooltipStyle"
      >
        <!-- eslint-disable-next-line vue/no-v-html -->
        <span class="help-tip__content" v-html="text"></span>
        <span class="help-tip__arrow"></span>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * 通用提示组件。
 *
 * 在指标标签旁显示一个小问号图标，
 * 鼠标悬停（桌面）或点击（移动端）时弹出详细说明。
 *
 * 气泡通过 Teleport 渲染到 <body> 末尾，
 * 以 position: fixed 定位，自动检测视口边缘并翻转方向。
 *
 * @example
 * ```vue
 * <HelpTip
 *   text="年化收益率：将累计收益折算为年化收益率（%）。<br>便于横向对比不同回测时间长度的策略表现。"
 * />
 * ```
 */

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 提示内容，支持 HTML 标签（如 <br> 换行） */
    text: string
    /** 首选弹出位置（空间不足时自动翻转） */
    position?: 'top' | 'bottom' | 'left' | 'right'
    /** 最大宽度 */
    maxWidth?: string
    /** 图标文字 */
    icon?: string
  }>(),
  {
    position: 'top',
    maxWidth: '300px',
    icon: '?',
  },
)

const showTip = ref(false)
const triggerRef = ref<HTMLElement | null>(null)
const tooltipRef = ref<HTMLElement | null>(null)

/** 实际使用的弹出方向（自动翻转后的结果） */
const actualPosition = ref<'top' | 'bottom' | 'left' | 'right'>(props.position)

/** tooltip fixed 定位的 style */
const tooltipStyle = ref<Record<string, string>>({})

/** 桌面端：鼠标进入时显示 */
function onEnter(): void {
  showTip.value = true
  nextTick(() => updatePosition())
}

/** 桌面端：鼠标离开时隐藏 */
function onLeave(): void {
  showTip.value = false
}

/** 移动端：点击/触摸切换显示 */
function onClick(): void {
  showTip.value = !showTip.value
  if (showTip.value) {
    nextTick(() => updatePosition())
  }
}

/** 计算 tooltip 在视口中的最佳位置 */
function updatePosition(): void {
  if (!triggerRef.value || !tooltipRef.value) return

  const trigger = triggerRef.value.getBoundingClientRect()
  const tooltip = tooltipRef.value.getBoundingClientRect()
  const vw = window.innerWidth
  const vh = window.innerHeight
  const gap = 8

  // 检测各方向是否有足够空间
  const spaceTop = trigger.top - gap
  const spaceBottom = vh - trigger.bottom - gap
  const spaceLeft = trigger.left - gap
  const spaceRight = vw - trigger.right - gap

  let best: 'top' | 'bottom' | 'left' | 'right'

  // 先在首选方向的垂直/水平轴上选
  if (props.position === 'top') {
    best = spaceTop >= tooltip.height || spaceTop >= 60 ? 'top'
      : spaceBottom >= tooltip.height || spaceBottom >= 60 ? 'bottom'
      : spaceRight >= tooltip.width ? 'right'
      : 'left'
  } else if (props.position === 'bottom') {
    best = spaceBottom >= tooltip.height || spaceBottom >= 60 ? 'bottom'
      : spaceTop >= tooltip.height || spaceTop >= 60 ? 'top'
      : spaceRight >= tooltip.width ? 'right'
      : 'left'
  } else if (props.position === 'left') {
    best = spaceLeft >= tooltip.width || spaceLeft >= 100 ? 'left'
      : spaceRight >= tooltip.width || spaceRight >= 100 ? 'right'
      : spaceBottom >= tooltip.height ? 'bottom'
      : 'top'
  } else {
    // right
    best = spaceRight >= tooltip.width || spaceRight >= 100 ? 'right'
      : spaceLeft >= tooltip.width || spaceLeft >= 100 ? 'left'
      : spaceBottom >= tooltip.height ? 'bottom'
      : 'top'
  }

  actualPosition.value = best

  // 计算 fixed 坐标
  let left: number
  let top: number

  switch (best) {
    case 'top':
      left = trigger.left + trigger.width / 2 - tooltip.width / 2
      top = trigger.top - tooltip.height - gap
      break
    case 'bottom':
      left = trigger.left + trigger.width / 2 - tooltip.width / 2
      top = trigger.bottom + gap
      break
    case 'left':
      left = trigger.left - tooltip.width - gap
      top = trigger.top + trigger.height / 2 - tooltip.height / 2
      break
    case 'right':
      left = trigger.right + gap
      top = trigger.top + trigger.height / 2 - tooltip.height / 2
      break
    default:
      left = trigger.left
      top = trigger.top - tooltip.height - gap
  }

  // 约束在视口内（左右不超出屏幕）
  const margin = 8
  if (left < margin) left = margin
  if (left + tooltip.width > vw - margin) left = vw - tooltip.width - margin
  if (top < margin) top = margin
  if (top + tooltip.height > vh - margin) top = vh - tooltip.height - margin

  tooltipStyle.value = {
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`,
    maxWidth: props.maxWidth,
  }
}

/** 点击外部区域关闭 tooltip */
function onDocumentClick(e: MouseEvent): void {
  if (!showTip.value) return
  const target = e.target as HTMLElement | null
  if (!target) return
  // 点击在触发图标或气泡内部时不关闭
  if (triggerRef.value?.contains(target) || tooltipRef.value?.contains(target)) return
  showTip.value = false
}

// 监听 showTip 变化，添加/移除全局点击监听
watch(showTip, (val) => {
  if (val) {
    document.addEventListener('click', onDocumentClick, true)
    // 监听窗口变化，重新计算位置
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
  } else {
    document.removeEventListener('click', onDocumentClick, true)
    window.removeEventListener('resize', updatePosition)
    window.removeEventListener('scroll', updatePosition, true)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick, true)
  window.removeEventListener('resize', updatePosition)
  window.removeEventListener('scroll', updatePosition, true)
})
</script>

<style>
/* ⚠️ 以下样式不加 scoped —— Teleport 到 body 后 scoped 样式不生效 */
/* 使用 BEM 命名 + help-tip 前缀避免污染 */

/* ── 触发图标 ─────────────────────────────────────────────────────── */
.help-tip {
  display: inline-flex;
  align-items: center;
  position: relative;
  cursor: pointer;
  vertical-align: middle;
}

.help-tip__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--surface-2, #334155);
  color: var(--text-muted, #94a3b8);
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  transition: all 0.15s;
  user-select: none;
  flex-shrink: 0;
}

.help-tip:hover .help-tip__icon,
.help-tip--active .help-tip__icon {
  background: var(--accent, #3b82f6);
  color: #fff;
}

/* ── 提示气泡（fixed 定位，body 层级） ────────────────────────────── */
.help-tip__tooltip {
  position: fixed;
  z-index: 99999;
  background: var(--surface, #1e293b);
  border: 1px solid var(--border, #334155);
  border-radius: var(--radius-sm, 6px);
  padding: 10px 14px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  white-space: normal;
  word-wrap: break-word;
  pointer-events: auto;
}

.help-tip__content {
  font-size: 12px;
  color: var(--text-muted, #94a3b8);
  line-height: 1.7;
  display: block;
}

/* ── 箭头（默认朝上，即气泡在触发元素上方，箭头朝下指向触发元素） ── */
.help-tip__tooltip--top .help-tip__arrow {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  width: 0;
  height: 0;
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-top: 6px solid var(--border, #334155);
}

.help-tip__tooltip--bottom .help-tip__arrow {
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  width: 0;
  height: 0;
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-bottom: 6px solid var(--border, #334155);
}

.help-tip__tooltip--left .help-tip__arrow {
  position: absolute;
  left: 100%;
  top: 50%;
  transform: translateY(-50%);
  width: 0;
  height: 0;
  border-top: 6px solid transparent;
  border-bottom: 6px solid transparent;
  border-left: 6px solid var(--border, #334155);
}

.help-tip__tooltip--right .help-tip__arrow {
  position: absolute;
  right: 100%;
  top: 50%;
  transform: translateY(-50%);
  width: 0;
  height: 0;
  border-top: 6px solid transparent;
  border-bottom: 6px solid transparent;
  border-right: 6px solid var(--border, #334155);
}

/* ── 过渡动画 ─────────────────────────────────────────────────────── */
.help-tip-fade-enter-active,
.help-tip-fade-leave-active {
  transition: opacity 0.15s ease;
}
.help-tip-fade-enter-from,
.help-tip-fade-leave-to {
  opacity: 0;
}

/* ── 响应式：移动端自适应宽度 ───────────────────────────────────── */
@media (max-width: 640px) {
  .help-tip__tooltip {
    max-width: 240px !important;
  }
}
</style>
