<template>
  <div class="app-shell" :class="{ 'report-mobile-shell': route.path === '/daily-report' }">
    <aside class="sidebar">
      <div class="logo">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <polyline points="2,18 8,12 13,16 22,6" stroke="#3b82f6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
          <polyline points="16,6 22,6 22,12" stroke="#3b82f6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
        </svg>
        <span class="logo-text">Quant</span>
      </div>
      <AlertBell class="alert-slot" />
      <nav>
        <RouterLink to="/" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
          总览
        </RouterLink>
        <RouterLink to="/daily-report" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h11l3 3v13H5z"/><path d="M16 4v3h3"/><path d="M8 11h8M8 15h8M8 7h3"/></svg>
          每日简报
        </RouterLink>
        <RouterLink to="/strategies" class="nav-item" :class="{ 'nav-active': isUnder('/strategies') }">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/></svg>
          策略中心
        </RouterLink>
        <RouterLink to="/lifecycle" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3 8 4-16 3 8h4"/></svg>
          生命周期
        </RouterLink>
        <RouterLink to="/research" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6"/><path d="M10 3v6L4.5 18.5A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2.5L14 9V3"/><line x1="7" y1="15" x2="17" y2="15"/></svg>
          研究看板
        </RouterLink>
        <RouterLink to="/factors" class="nav-item" :class="{ 'nav-active': isUnder('/factors') }">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="14" y2="12"/><line x1="4" y1="18" x2="18" y2="18"/><circle cx="20" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>
          因子中心
        </RouterLink>
        <RouterLink to="/tools/rrg-lab" class="nav-item nav-item-sub">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="12" x2="16" y2="8"/><line x1="12" y1="12" x2="12" y2="7"/><line x1="12" y1="12" x2="15.5" y2="14.5"/></svg>
          RRG / 扩散研究
        </RouterLink>
        <RouterLink to="/tools/signal-lab" class="nav-item nav-item-sub">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="20" x2="4" y2="12"/><line x1="10" y1="20" x2="10" y2="6"/><line x1="16" y1="20" x2="16" y2="14"/><polyline points="3 9 9 4 15 10 21 5"/></svg>
          量价 / 择时研究
        </RouterLink>
        <RouterLink to="/runs" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
          运行记录
        </RouterLink>
        <RouterLink to="/data-management" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/><circle cx="12" cy="12" r="4"/></svg>
          数据管理
        </RouterLink>
        <RouterLink to="/indexes" class="nav-item" :class="{ 'nav-active': isUnder('/indexes') }">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
          指数数据
        </RouterLink>
        <RouterLink to="/industries" class="nav-item" :class="{ 'nav-active': isUnder('/industries') }">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="18" rx="2"/><line x1="2" y1="9" x2="22" y2="9"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="10" y1="6" x2="10.01" y2="6"/></svg>
          申万行业
        </RouterLink>
        <RouterLink to="/stocks" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="3" width="10" height="18" rx="2"/><line x1="10" y1="8" x2="14" y2="8"/><line x1="10" y1="12" x2="14" y2="12"/><line x1="10" y1="16" x2="12" y2="16"/></svg>
          个股数据
        </RouterLink>
        <RouterLink to="/macro" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          宏观指标
        </RouterLink>
        <RouterLink to="/backtests" class="nav-item" :class="{ 'nav-active': isUnder('/backtests') }">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/></svg>
          回测中心
        </RouterLink>
      </nav>
    </aside>
    <main class="content">
      <!-- 以路径为 key：详情页路由参数变化（如 /strategies/a → /strategies/b）时重建页面并重新加载数据 -->
      <RouterView :key="route.path" />
    </main>
    <ToastHost />
  </div>
</template>

<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'
import AlertBell from './components/layout/AlertBell.vue'
import ToastHost from './components/ToastHost.vue'

const route = useRoute()

/**
 * 当前路由是否位于某个导航入口之下（详情页也高亮列表入口）。
 *
 * @param prefix - 导航入口路径
 * @returns 是否高亮
 */
function isUnder(prefix: string): boolean {
  return route.path === prefix || route.path.startsWith(`${prefix}/`)
}
</script>

<style scoped>
.app-shell {
  display: grid;
  /* minmax(0, 1fr)：允许内容列收缩，宽表格/图表在自身容器内滚动，而不是把整页撑出横向滚动条 */
  grid-template-columns: 220px minmax(0, 1fr);
  min-height: 100vh;
}

.sidebar {
  background: var(--surface);
  border-right: 1px solid var(--border);
  padding: 20px 0;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  /* 矮窗口下导航项超出一屏时可纵向滚动 */
  overflow-y: auto;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 20px 20px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}

.logo-text {
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.02em;
}

nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 8px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 13.5px;
  transition: background 0.15s, color 0.15s;
  border-left: 2px solid transparent;
}

.nav-item:hover {
  background: var(--surface-2);
  color: var(--text);
}

.nav-item.router-link-active,
.nav-item.nav-active {
  background: rgba(59, 130, 246, 0.12);
  color: var(--accent);
  border-left-color: var(--accent);
}

.nav-item-sub {
  padding-left: 28px;
  font-size: 13px;
}

.content {
  min-width: 0;
  padding: 28px 32px;
  background: var(--bg);
  min-height: 100vh;
}

@media (max-width: 820px) {
  .app-shell.report-mobile-shell {
    display: block;
  }

  .report-mobile-shell .sidebar {
    position: sticky;
    top: 0;
    z-index: 20;
    height: auto;
    padding: 8px 12px;
    overflow-x: auto;
    flex-direction: row;
    align-items: center;
    scrollbar-width: none;
  }

  .report-mobile-shell .sidebar::-webkit-scrollbar {
    display: none;
  }

  .report-mobile-shell .logo {
    flex: 0 0 auto;
    margin: 0;
    padding: 0 12px 0 2px;
    border: 0;
  }

  .report-mobile-shell .logo-text,
  .report-mobile-shell .alert-slot {
    display: none;
  }

  .report-mobile-shell nav {
    flex-direction: row;
    gap: 4px;
    padding: 0;
  }

  .report-mobile-shell .nav-item {
    flex: 0 0 auto;
    padding: 7px 10px;
    border-bottom: 2px solid transparent;
    border-left: 0;
    font-size: 12px;
  }

  .report-mobile-shell .nav-item-sub {
    padding-left: 10px;
    font-size: 12px;
  }

  .report-mobile-shell .nav-item.router-link-active,
  .report-mobile-shell .nav-item.nav-active {
    border-bottom-color: var(--accent);
    border-left-color: transparent;
  }

  .report-mobile-shell .content {
    padding: 14px 12px 28px;
  }
}
</style>
