<template>
  <div class="app-shell">
    <aside class="sidebar">
      <h1>Quant ETF</h1>
      <nav>
        <RouterLink to="/">总览</RouterLink>
        <RouterLink to="/etfs">ETF 池</RouterLink>
        <RouterLink to="/strategies">策略中心</RouterLink>
        <RouterLink to="/runs">运行记录</RouterLink>
        <RouterLink to="/data-status">数据状态</RouterLink>
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
.app-shell { display: grid; grid-template-columns: 220px 1fr; min-height: 100vh; font-family: Arial, sans-serif; }
.sidebar { background: #0f172a; color: #fff; padding: 24px; }
.sidebar nav { display: flex; flex-direction: column; gap: 12px; margin-top: 24px; }
.sidebar a { color: #cbd5e1; text-decoration: none; }
.sidebar a.router-link-active { color: #fff; font-weight: 700; }
.content { padding: 24px; background: #f8fafc; }
</style>
