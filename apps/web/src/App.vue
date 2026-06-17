<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="logo">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <polyline points="2,18 8,12 13,16 22,6" stroke="#3b82f6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
          <polyline points="16,6 22,6 22,12" stroke="#3b82f6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
        </svg>
        <span class="logo-text">Quant ETF</span>
      </div>
      <nav>
        <RouterLink to="/" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
          总览
        </RouterLink>
        <RouterLink to="/etfs" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3h18v18H3z"/><path d="M3 9h18M3 15h18M9 3v18"/></svg>
          ETF 池
        </RouterLink>
        <RouterLink to="/strategies" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/></svg>
          策略中心
        </RouterLink>
        <RouterLink to="/factors" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="14" y2="12"/><line x1="4" y1="18" x2="18" y2="18"/><circle cx="20" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>
          因子中心
        </RouterLink>
        <RouterLink to="/runs" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
          运行记录
        </RouterLink>
        <RouterLink to="/indexes" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
          指数数据
        </RouterLink>
        <RouterLink to="/macro" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          宏观指标
        </RouterLink>
        <RouterLink to="/journal/calendar" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          市场日志
        </RouterLink>
        <RouterLink to="/backtests" class="nav-item">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/></svg>
          回测中心
        </RouterLink>
      </nav>
    </aside>
    <main class="content">
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: 220px 1fr;
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

.nav-item.router-link-active {
  background: rgba(59, 130, 246, 0.12);
  color: var(--accent);
  border-left-color: var(--accent);
}

.content {
  padding: 28px 32px;
  background: var(--bg);
  min-height: 100vh;
}
</style>
