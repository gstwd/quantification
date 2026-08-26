import { createRouter, createWebHistory } from 'vue-router'

import AIFactorsPage from '../pages/AIFactorsPage.vue'
import BacktestCreatePage from '../pages/BacktestCreatePage.vue'
import BacktestComparisonCreatePage from '../pages/BacktestComparisonCreatePage.vue'
import BacktestComparisonDetailPage from '../pages/BacktestComparisonDetailPage.vue'
import BacktestDetailPage from '../pages/BacktestDetailPage.vue'
import BacktestListPage from '../pages/BacktestListPage.vue'
import DashboardPage from '../pages/DashboardPage.vue'
import FactorDetailPage from '../pages/FactorDetailPage.vue'
import FactorsPage from '../pages/FactorsPage.vue'
import KeywordTagsPage from '../pages/KeywordTagsPage.vue'
import RunsPage from '../pages/RunsPage.vue'
import IndexDetailPage from '../pages/IndexDetailPage.vue'
import IndexListPage from '../pages/IndexListPage.vue'
import MacroPage from '../pages/MacroPage.vue'
import StrategiesPage from '../pages/StrategiesPage.vue'
import StrategyDetailPage from '../pages/StrategyDetailPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardPage },
    { path: '/indexes', component: IndexListPage },
    { path: '/indexes/:indexCode', component: IndexDetailPage, props: true },
    { path: '/macro', component: MacroPage },
    { path: '/strategies', component: StrategiesPage },
    { path: '/strategies/:strategyId', component: StrategyDetailPage, props: true },
    { path: '/ai-factors', component: AIFactorsPage },
    { path: '/keyword-tags', component: KeywordTagsPage },
    { path: '/factors', component: FactorsPage },
    { path: '/factors/:factorId', component: FactorDetailPage, props: true },
    { path: '/runs', component: RunsPage },
    { path: '/backtests', component: BacktestListPage },
    { path: '/backtests/new', component: BacktestCreatePage },
    { path: '/backtests/comparison/new', component: BacktestComparisonCreatePage },
    { path: '/backtests/comparison/:comparisonId', component: BacktestComparisonDetailPage, props: true },
    { path: '/backtests/:backtestId', component: BacktestDetailPage, props: true },
  ],
})

export default router
