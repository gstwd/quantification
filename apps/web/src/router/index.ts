import { createRouter, createWebHistory } from 'vue-router'

import BacktestCreatePage from '../pages/BacktestCreatePage.vue'
import BacktestDetailPage from '../pages/BacktestDetailPage.vue'
import BacktestListPage from '../pages/BacktestListPage.vue'
import DashboardPage from '../pages/DashboardPage.vue'
import DataStatusPage from '../pages/DataStatusPage.vue'
import EtfDetailPage from '../pages/EtfDetailPage.vue'
import EtfUniversePage from '../pages/EtfUniversePage.vue'
import RunsPage from '../pages/RunsPage.vue'
import StrategiesPage from '../pages/StrategiesPage.vue'
import StrategyDetailPage from '../pages/StrategyDetailPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardPage },
    { path: '/etfs', component: EtfUniversePage },
    { path: '/etfs/:etfCode', component: EtfDetailPage, props: true },
    { path: '/strategies', component: StrategiesPage },
    { path: '/strategies/:strategyId', component: StrategyDetailPage, props: true },
    { path: '/runs', component: RunsPage },
    { path: '/data-status', component: DataStatusPage },
    { path: '/backtests', component: BacktestListPage },
    { path: '/backtests/new', component: BacktestCreatePage },
    { path: '/backtests/:backtestId', component: BacktestDetailPage, props: true },
  ],
})

export default router
