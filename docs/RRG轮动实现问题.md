# RRG 轮动实现问题清单（现状核实）

> 记录日期：2026-09-06
> 适用范围：申万行业 RRG + 扩散（数量占比）子系统（迁移 `0029_industry_rrg_subsystem`）从“已集成”走向“可回测、可运行、可监控”过程中已核实的全部问题。
>
> 文档约定：**本文件只记录现状核实（现象、代码路径、已确认的影响），不写解决方案或实施计划**。每一条问题在处理时单独制定计划，处理完成后回到本文件更新状态并注明日期。

## 一、核实方法与可信度说明

- 全部条目基于当前工作区代码静态核实 + 用户实际运行现象整理，未在本次核对中直连数据库复核行数、失败清单或真实净值（当前沙箱无数据库/外网权限）。
- “约 1000 万行个股收盘”来自用户反馈，与全 A 约 4900 只 × 2013 年以来约 2000+ 交易日的量级一致，按量级引用。
- 文中“已核实”指代码路径可直接定位；“现象”指用户运行输出；“估算”指由代码路径推导的数量级，均单独标注。

## 二、问题总览

| 编号 | 主题 | 来源 |
| --- | --- | --- |
| P01 | 个股收盘历史回填：失败不落库、无续跑、无前端可见 | 用户问题 1 |
| P02 | 扩散/轮动查询全量物化 10M 行，内存飙升至死机 | 用户问题 2 |
| P03 | 行业数据源未纳入数据源状态/数据质量/补拉体系 | 用户问题 3 |
| P04 | RRG/扩散因子未纳入因子中心管理 | 用户问题 4 |
| P05 | RRG/扩散调试页功能与研究报告呈现不足 | 用户问题 5 |
| P06 | 个股收盘快照入库日期错标（可能写入周末/节假日） | 整体评估 |
| P07 | `stock_daily_close` 缺股票维度索引与“已全覆盖跳过”能力 | 整体评估 |
| P08 | 行业日线表 `prev_close_price`/`change_pct` 恒为 NULL | 整体评估 |
| P09 | 行业日线“增量刷新”实际仍全量拉取再过滤 | 整体评估 |
| P10 | 行业日频摄取部分失败仍整体 success，无质量指标落库 | 整体评估 |
| P11 | 申万行业/个股数据源单一，健康检查未接入统一状态 | 整体评估 |
| P12 | 行业因子同 factor_id 覆盖不同参数计算结果，无元数据 | 整体评估 |
| P13 | RRG 日历缺口 ffill 兜底仅打日志，不进因子/结果 | 整体评估 |
| P14 | 扩散判涨复权口径未定（当前不复权/原始价） | 整体评估 |
| P15 | 历史成分存在 2021 版体系回写偏差且系统无提示 | 整体评估 |
| P16 | 独立回测逐日收益重复计算与日期错配（阻塞级） | 整体评估 |
| P17 | 独立回测无基准/行业等权对照输出 | 整体评估 |
| P18 | rotation 策略无法走标准策略执行与标准回测链路（阻塞级） | 整体评估 |
| P19 | 标准链路打通后信号/因子持久化的指数域与行业域混用风险 | 整体评估 |
| P20 | 复刻策略顶层 `frequency=daily` 与调仓 `monthly` 不一致 | 整体评估 |
| P21 | API 与调试页错误静默，空数据与失败不可区分 | 整体评估 |
| P22 | RRG/扩散接口逐日逐点展开，无区间/数据量上限 | 整体评估 |
| P23 | 调试页无侧栏导航入口，仅可 URL 直达 | 整体评估 |
| P24 | 自动化测试覆盖不足（回测测试过浅） | 整体评估 |
| P25 | 行业因子计算路径均每次从零重建 warm-up 面板，无增量复用 | 整体评估 |

## 三、问题现状核实（逐条）

### P01 个股收盘历史回填：失败不落库、无续跑、无前端可见（用户问题 1）

> 处理记录（2026-09-06，P01 实施方案落地，尚未应用迁移/真库联调）：新增
> `stock_universe` 元数据表与质量快照（迁移 0030），个股数据页 `/stocks` 可查看
> 每只股票的缺失条数并对单股执行质量检查/补全/重拉；全市场批量补全收口到 CLI
> `stock quality/fill/rebuild`；质量快照作为持久化的失败可见结果，CLI 支持
> `--only-missing` 续拉。

现状核实：

- 用户实际执行 `industry backfill-stock-close` 时，日志出现 `'NoneType' object has no attribute 'error_code'`、`日期格式不正确，请修改。`，且执行后期报了大量拉取失败；命令结束后没有可查的失败清单。
- 已核实代码路径：`IndustryDataService.backfill_stock_close`（`apps/api/src/quant_etf_api/services/industry_data_service.py:214`）把每只股票的失败拼进内存 `errors` 列表并 `logger.warning`，命令结束即丢失；CLI 收到 `errors` 后打印并 `sys.exit(1)`（`apps/api/src/quant_etf_api/cli.py:441`），**不建 `research_run`、不入后台任务队列**。
- Runs 页只展示 `research_run` 记录；`industry_ingest`/`industry_factor_compute` 两类会建 run（`apps/api/src/quant_etf_api/infra/job_queue/handlers.py:302/329`），但 backfill 系列命令全程不建 run，因此前端确实没有任何页面可看回填进度或失败原因。
- 已核实失败来源：代码按 6 位股票代码升序遍历，北交所/`8xx/4xx` 代码在末尾；`StockCloseClient._market_prefix` 只识别 `sh/sz`，`bj` 直接返回空列表（`apps/api/src/quant_etf_api/infra/clients/stock_close_client.py:44`）；`backfill_stock_close` 只在 baostock **返回空列表**时才兜底 AkShare，**抛异常不会兜底**（`industry_data_service.py:230-234`），因此尾部大量北交所代码逐只转 AkShare 单股接口，失败率高。
- 已核实 baostock 客户端现状：日期格式已修复为 `YYYY-MM-DD`（`stock_close_client.py:27`），查询结果 `rs is None` 已显式抛错；但登录对象 `lg = bs.login()` 仍无判空，直接访问 `lg.error_code`（`stock_close_client.py:70-72`），`login()` 返回 None 时仍会复现用户日志中的同款 `NoneType` 错误。
- 每只股票都独立 `login()/logout()`（`stock_close_client.py:70/110`），4900+ 只串行且每只全量拉取，耗时长且容易被上游限流；无分批并发、无会话复用、无“已全覆盖则跳过”。
- 写入为 `ON CONFLICT DO NOTHING` 幂等 upsert（`apps/api/src/quant_etf_api/infra/db/repositories/industry.py:186`），已入库的约 1000 万行不受影响，可安全重跑；但当前没有按股票查“是否已拉满”的方法，重跑仍会重复请求全部代码。
- 影响：数据完整性无法验证；扩散分母在大量股票缺失时静默变小；无法确定需要补拉的具体股票清单。

### P02 扩散/轮动查询全量物化 10M 行，内存飙升至死机（用户问题 2）

> 处理记录（2026-09-06，RRG/扩散数据研究调试台落地）：扩散改为“行业 → 成员股”
> 分批计算，每行业只通过轻量列查询加载自身成员股收盘并逐行业释放（新增
> `find_close_rows` 与 `domain/industry/diffusion_panel.py`），不再把全市场
> 收盘一次物化进 Python；调试页默认近 3 年、按钮改为顺序执行，避免 RRG/扩散
> 并发重复计算；服务端限制扩散单次查询 2200 自然日（RRG 3700），超限 422
> 明确提示。轮动/回测路径复用同一扩散实现，一并受益；标准回测链路问题见 P18。

现状核实：

- 用户现象：执行 RRG/扩散调试台查询时“什么都没查出来”，但电脑内存占用飙升并最终死机。
- 已核实根因路径：`IndustryFactorService._build_diffusion_panel`（`apps/api/src/quant_etf_api/services/industry_factor_service.py:151`）按 `[start-400 自然日, end]` 调用 `StockDailyCloseRepository.find_range`（`apps/api/src/quant_etf_api/infra/db/repositories/industry.py:157`），**无分页、无列裁剪、无按成分股过滤**，一次性把区间内全部股票的收盘行加载成 ORM 对象列表，再经 `_stock_close_panel`（`industry_factor_service.py:297`）以“字典套字典”方式转 `date × stock` 宽表。
- 量级估算：默认页面区间为近 3 年（`apps/web/src/pages/RRGLabPage.vue:213` 的 `initDates`），加 400 自然日回看约覆盖 800+ 个交易日 × 约 4900 只股票 ≈ 400 万行级；若区间更长则逼近千万行。ORM 对象 + 嵌套字典 + DataFrame 转换的峰值内存以 GB 计，足以死机。
- 已核实并发放大：页面“查询”用 `Promise.all([loadRRG(), loadDiffusion(), loadRotation()])`（`RRGLabPage.vue:347`），其中 diffusion 与 rotation 两个请求各自独立执行一次同款全量扩散重算，内存并发翻倍。
- 已核实无任何防护：`/api/industry/diffusion`、`/api/industry/rotation`（`apps/api/src/quant_etf_api/api/routers/industry.py:117/150`）均无区间长度上限、无参数上限、无服务端缓存；每次请求都即时从库重建面板。
- 独立回测与轮动分析同样受影响：`IndustryRotationService._prepare` 对整段回测区间调用 `build_panels`（`apps/api/src/quant_etf_api/services/industry_rotation_service.py:147`），即回测也必须先把区间内全部个股收盘载入内存，与用户“10M 行无法像指数那样整段载入内存回测”的担忧一致。
- “什么都没查出来”的机制无法从现有日志唯一归因：可能是进程被系统 OOM 终止、请求超时，也可能是数据缺口导致全 NaN/空面板；两者在现有代码路径下都会表现为空结果。
- 已核实数据规模本身不是异常：约 4900 只 × 十余年 ≈ 千万行是正常量级；问题是“每次请求全量物化进 Python 内存”的读取与计算方式。

### P03 行业数据源未纳入数据源状态/数据质量/补拉体系（用户问题 3）

> 处理记录（2026-09-06，P03 方案落地，迁移 0031 待应用）：
> 新增行业数据管理页 `/industries` 与行业详情页（K 线 + 数据质量），侧栏加入口；
> `industry_universe` 增加日线质量快照列；新增 `GET /industry/summary` 与单行业
> bars/quality 只读端点，以及 industry_universe_refresh / industry_bars_refresh /
> industry_quality_check / industry_data_fill / industry_data_rebuild 五个后台任务；
> `/system/status` 增加行业日线与申万行业成分两张数据源状态卡，
> `/system/data-quality` 增加 industry_bars 分组；CLI 新增 industry quality/fill/rebuild。

现状核实：

- 现有 `GET /system/status` 的 `data_sources` 只统计 `index_daily_bar`、`index_valuation`、`macro_indicator` 三张表（`apps/api/src/quant_etf_api/services/system_service.py:127`），不含 `industry_*` 与 `stock_daily_close`。
- 现有 `GET /system/data-quality` 响应只有 `index_bars`/`index_valuation` 两组（`apps/api/src/quant_etf_api/schemas/system.py:29`），行业数据无质量检查输出；`services/data_quality.py` 的异常/连续性检测也没有行业版。
- 行业相关刷新全部是 CLI 同步命令（`industry backfill-bars/backfill-membership/backfill-stock-close/compute-factors`），没有 POST API、不入后台任务队列；只有日频调度器触发的 `industry_daily_ingest` 建 run（`apps/api/src/quant_etf_api/infra/scheduler/__init__.py:296`）。
- 前端 Dashboard 只渲染上述三张旧表的数据源卡片与指数质量；侧栏无任何行业数据入口（`apps/web/src/App.vue`），行业数据目前只能靠 CLI 和临时调试页 URL 访问。
- 影响：行业日线/成分/个股收盘的新鲜度、覆盖率、缺口、拉取失败均无系统级可见性与告警，属于用户问题 3 的完整现状。

### P04 RRG/扩散因子未纳入因子中心（用户问题 4）

> 处理记录（2026-09-07）：RRG/扩散 4 因子已登记进 `factor_definition`
> （asset_domain=industry、value_shape=panel、usage=rotation_input、
> default_params=220/60/20/220+剔除综合），同步纳入因子中心列表/详情；
> 因子详情行业因子显示“数据状态”（industry_factor_value 参数变体与覆盖）。
> 策略层通过 rotation 模块消费，实时分配读预计算值，标准回测按参数指纹
> 一次性预计算面板。

现状核实：

- 因子中心（Factors 列表/详情页）的数据来源是 `factor_definition` + `index_factor_value`（`apps/api/src/quant_etf_api/services/factor_admin_service.py`），只管理“基于指数数据、供通用 score/filter/timing 消费”的指数因子。
- RRG/扩散按既定方案写入独立的 `industry_factor_value`，factor_id 为 `rrg_rs_ratio/rrg_rs_momentum/rrg_quadrant/diffusion_count_ratio`（`apps/api/src/quant_etf_api/domain/industry/constants.py` 的 `INDUSTRY_FACTOR_IDS`），**不注册进 `factor_definition`、不进 `init-factors` 同步、不参与指数因子计算服务**。
- 因此“因子中心看不到 RRG/扩散”是当前设计的直接结果，不是遗漏；但系统也没有任何“行业因子”元数据出口（定义/参数/存储/最新日期），除代码常量与调试页外不可发现、不可管理。
- 影响：四个行业因子无统一的元数据与状态视图，用户无法确认参数口径、最近计算日期与覆盖情况。

### P05 RRG/扩散调试页功能与研究报告呈现不足（用户问题 5）

> 处理记录（2026-09-06）：调试页升级为“数据研究版”（`/tools/rrg-lab`，侧栏新增
> “RRG / 扩散研究”入口）：RRG 四象限散点 + 可选历史轨迹与日期选择、warm-up 与
> 数据覆盖表；扩散时序（限 8 行业）、最新日排名（含有效样本/成员数/覆盖度）、
> 覆盖度时序与行业覆盖汇总表；页面/接口返回数据问题区与口径/公式/信号规则说明。
> 页面仅做数据研究，不写策略、不写因子、不触发回测，也不与标准回测链路关联。

现状核实：

- 临时调试页（`apps/web/src/pages/RRGLabPage.vue`）当前只有：参数表单、RRG 最新日散点、扩散最新排名表、轮动选择表四块；页面标题自带 `TEMP` 标记。
- 缺少：方法论/公式/口径说明、数据就绪与 warm-up 状态提示、行业历史轨迹、扩散时序图/热力图、轮动持仓时间线、净值与基准对比、错误原因展示、CSV 导出。
- 页面错误处理全部为 `catch { ... = [] }` 静默置空（`RRGLabPage.vue:284/304/324`），空数据与失败在 UI 上不可区分。
- 页面仅注册路由 `/tools/rrg-lab`（`apps/web/src/router/index.ts:34`），侧栏无入口（`apps/web/src/App.vue`），不属于正式导航体系。
- 与 P02 关联：默认 3 年区间 + 并发三查询 + 服务端全量重算，是“页面一查就死机”的直接触发面。

### P06 个股收盘快照入库日期错标（可能写入周末/节假日）

> 处理记录（2026-09-06）：核实当前代码 `refresh_stock_close_snapshot` 已按调用方
> 传入的 `trade_date` 落库，日频入口以行业日线最新交易日为目标；建议迁移应用后
> 观察周末/节假日自动运行的入库日期复核。

现状核实：

- `StockCloseClient.fetch_all_close_snapshot` 在行上写 `trade_date = date.today()`（`stock_close_client.py:143`），即“抓取当天”，而非目标交易日。
- `IndustryDataService.refresh_stock_close_snapshot(trade_date)` 的参数 `trade_date` 形同虚设，入库仍取 `row["trade_date"]`（`industry_data_service.py:249-253`），即忽略调用方传入的目标日期。
- 日频入口 `run_daily_ingest` 传入 `target_date or latest`（`industry_data_service.py:293`），但该值不会生效；调度器在周末/节假日也会入队（`infra/scheduler/__init__.py` 注释明确不做交易日判断），凌晨 03:10 执行时 `date.today()` 可能是非交易日或“下一自然日”，会把全 A 快照写到周六/周日或错位日期。
- 影响：`stock_daily_close` 会出现非交易日行；扩散面板以“库内存在的日期集合”作为日历（`_stock_close_panel` 的 index 为各行日期并集），周末/错位日期会进入 `shift(lookback)` 与 20 日 MA 的滑窗，污染后续扩散值；且与行业日线日历不一致。

### P07 `stock_daily_close` 缺股票维度索引与“已全覆盖跳过”能力

> 处理记录（2026-09-06）：迁移 0030 已新增
> `ix_stock_daily_close_code_date(stock_code, trade_date)`；按股票查询/补全/重拉的
> 单股路径已具备支撑索引。

现状核实：

- 表唯一约束/索引只有 `(trade_date, stock_code)`（迁移 `0029` 的 `uq_stock_daily_close`），没有任何以 `stock_code` 开头的索引；按单只股票查最新日期/覆盖区间无法高效走索引。
- `StockDailyCloseRepository` 只有 `find_range`（按日期区间）、`trading_dates`、`latest_date`（全局最新日）三个查询方法（`infra/db/repositories/industry.py:157-205`），没有“单股票覆盖区间/是否已拉满”查询。
- `backfill_stock_close` 因此无法跳过已全覆盖股票，每次重跑都全量重拉（与 P01 关联）。
- 行业日线表同样只有 `(trade_date, industry_code)` 唯一约束，按行业代码查询无独立索引（影响相对小，31 行业量级可忽略）。

### P08 行业日线表 `prev_close_price`/`change_pct` 恒为 NULL

> 处理记录（2026-09-06）：摄取链路统一按同一行业代码升序补写
> `prev_close_price`/`change_pct`（`domain/industry/bars.py` 派生，
> `_upsert_bars` 对冲突行 DO UPDATE），历史存量可在应用迁移 0031 后执行
> `industry fill --all`（或 `industry backfill-bars`）一次性补齐。

现状核实：

- 模型与迁移都定义了 `prev_close_price`、`change_pct` 两列（`infra/db/models/industry.py:88-89`、迁移 `0029`）。
- 数据侧从未写入：`SwIndustryClient.fetch_daily` 返回字典只有 open/high/low/close/volume/turnover/source（`sw_industry_client.py:55-70`）；`IndustryDataService._upsert_bars` 也只写上述字段（`industry_data_service.py:139-151`）。
- 全仓库检索确认除模型定义与回测局部变量外，没有任何代码为这两列赋值。
- 影响：需要涨跌幅/前收的质量检查、缺口检测、快照对齐都无法直接使用这两列；数据质量模块的 `check_daily_bar_anomalies` 依赖 `change_pct`，对行业日线不适用。

### P09 行业日线“增量刷新”实际仍全量拉取再过滤

> 处理记录（2026-09-06）：已评估，本轮随 P03 不处理、留作专项。
> 上游 AkShare `index_hist_sw` 无按日期增量下载能力，客户端仍是全量下载后本地
> 裁剪；fill/rebuild 复用全量路径，增量刷新以“窗口前最近收盘”作为
> prev_close_override，保证窗口首行派生正确。待上游支持或换源时专项优化。

现状核实：

- `SwIndustryClient.fetch_daily` 的请求固定为 `ak.index_hist_sw(symbol=symbol, period="day")` 全量拉取，`start_date/end_date` 参数只在本地对返回 DataFrame 做过滤（`sw_industry_client.py:41-55`）。
- `IndustryDataService.refresh_industry_bars_incremental` 每天对 31 个行业各调一次 `fetch_daily(start_date=latest-45天)`（`industry_data_service.py:119-127`），实际是 31 次全历史下载后本地裁剪。
- 影响：每日刷新成本约等于全量回填的重复成本；且对上游接口压力大，失败面与全量拉取相同。

### P10 行业日频摄取部分失败仍整体 success，无质量指标落库

> 处理记录（2026-09-06）：`run_daily_ingest` 改为返回结构化 dict
> `{target_date, bars, membership, stock_snapshot, quality}`，逐行业失败进入
> `bars.errors`；handler 以 metrics 落库并仅在 bars/个股快照无错误时入队当日
> 因子计算（否则记录 `factor_skipped` 原因）；成分失败不阻塞但显式进入 metrics。

现状核实：

- `run_daily_ingest` 内部对行业日线/成分/收盘三类刷新分别捕获异常并仅 `logger.warning`（`industry_data_service.py:121-127/296-308`），不抛错、不返回失败统计。
- 后台 handler `handle_industry_daily_ingest` 只要不抛异常就 `mark_success(run_id)` 且不写 metrics（`infra/job_queue/handlers.py:302-321`）；成分或个股快照当天全部失败时，run 仍显示成功，随后照常入队因子计算。
- 行业日线各行业失败、成分刷新失败、快照失败均无结构化记录进入 `research_run` 或任何质量表。
- 影响：日频链路“看起来成功”但数据可能长期缺边缺角，且与 P03 叠加没有前端可发现。

### P11 申万行业/个股数据源单一，健康检查未接入统一状态

> 处理记录（2026-09-06）：`/system/status` 已增加“行业日线行情
> (industry_daily_bar)”与“申万行业成分 (industry_membership_event)”快照卡；
> 个股日线卡（P01）已接入。统一健康轮询仍未实现，保持“按需调用 + 失败在
> run metrics/质量快照可见”的现状，全局健康巡检另立后续项。
>
> 补充处理（2026-09-07）：个股收盘快照由“东财唯一源”改为“东财
> `stock_zh_a_spot_em` 主源 + 新浪 `stock_zh_a_spot` 备用源”，两源均为
> 代码/最新价同构列，主源在代理/限流故障时自动回退（
> `stock_close_client.py:fetch_all_close_snapshot`），避免单源故障导致当日
> 快照整体缺失并连带跳过行业因子计算。

现状核实：

- 行业指数日线唯一来源为申万官网/AkShare `index_hist_sw`；分类文件唯一来源为申万官网 XLS（客户端内做了浏览器头 + 多 URL 重试 + `verify=False`，`sw_industry_client.py:84-135`）；个股历史唯一来源为 baostock（AkShare 仅空结果兜底）；快照唯一来源为东财 `stock_zh_a_spot_em`。
- 各客户端实现了 `health_check()`（`sw_industry_client.py:165`、`stock_close_client.py:174`），但没有任何统一 health 轮询或接入 `/system/status`；行业/个股源失败时系统无感知。
- 影响：单一上游风控（508/502）、限流或停更时无降级与告警，只能人工跑 CLI 发现。

### P12 行业因子同 factor_id 覆盖不同参数计算结果，无元数据

> 处理记录（2026-09-06）：已评估，本轮不处理、留作专项。需要为
> `industry_factor_value` 增加参数版本维度（如 params_hash/参数 JSON 列），
> 并将默认参数消费者与自定义参数计算隔离，避免覆盖污染。
> 处理记录（2026-09-07）：已落地。`industry_factor_value` 新增
> params_hash/params 列，唯一键扩为 (trade_date, industry_code, factor_id,
> params_hash)；计算与 upsert 全部带参数指纹，不同参数分行互不覆盖。

现状核实：

- `industry_factor_value` 唯一键为 `(trade_date, industry_code, factor_id)`，`factor_payload` 列恒为 NULL（`IndustryFactorService.compute_and_store` 写行时 payload 均为 None，`industry_factor_service.py:215-232`）。
- CLI `compute-factors` 暴露 `--lookback-ratio/--lookback-mom/--smooth-window/--diffusion-lookback`（`cli.py:368-372`），API 也可传自定义参数；同一 `factor_id` 用不同参数重算会直接 upsert 覆盖旧值（`IndustryFactorValueRepository.bulk_upsert` 为 do_update），库内无法区分该行是用哪组参数算的。
- 默认消费者（每日 handler、调试页默认参数、复刻回测）假设库内值都是默认参数 220/60/20/220，一旦被自定义参数覆盖，后续结果会被静默污染。
- 影响：因子值的“参数版本/计算来源/样本数”无迹可查，默认参数与自定义参数混算会产生不可信数据。

### P13 RRG 日历缺口 ffill 兜底仅打日志，不进因子/结果

> 处理记录（2026-09-06）：`/industry/rrg`、`/industry/diffusion` 现在返回
> `meta.issues`（如 BAR_GAP_FFILL / RRG_WARMUP_LEADING / TRAILING_DATA /
> START_BEFORE_DATA）与逐行业覆盖表（输入缺日、前置无效行、有效区间），
> ffill 兜底不再只进服务端日志，缺口在页面问题区直接可见。

现状核实：

- `compute_rs` 对行业收盘价 `reindex` 到基准日历后，缺失交易日整表 `ffill()` 并 `warnings.warn`（`domain/industry/factor_algo.py:53-70`），即缺数日期用前值硬补。
- `IndustryFactorService.build_panels` 只捕获 `ValueError`（warm-up 不足），不捕获/不传递该 `UserWarning`（`industry_factor_service.py:108-135`）；API/回测消费者拿到的因子值与“是否有缺口被 ffill”完全脱钩。
- 影响：行业日线单日缺口会静默变成“沿用前收盘”的 RRG/扩散输入，且用户无感知；与 P10 的数据缺口叠加会放大错误。

### P14 扩散判涨复权口径未定（当前不复权/原始价）

> 口径标注（2026-09-06）：本轮不改算法与数据源，仅在调试页与扩散响应
> `meta.rules` 中显式标注“个股收盘为未复权口径（P14 待定），除权除息会产生
> 人工涨跌，仅限研究观察”；真正修复需待复权数据源/口径专项。

现状核实：

- baostock 历史拉取 `adjustflag="3"`（不复权，`stock_close_client.py:80`）；当日快照使用东财“最新价”（原始价，`stock_close_client.py:129-153`）；`stock_daily_close` 无任何字段记录复权方式。
- 扩散算法为 `close_t > close_{t-lookback}`（`domain/industry/factor_algo.py:109-128`），直接消费上述原始收盘。
- 未核实研究报告对除权除息的处理口径；不复权价格在除权/除息日会产生人工“下跌”，且 220 日窗口内含除权事件的股票判涨结果受影响，目前代码无校正、无口径标注。
- 影响：扩散的“上涨占比”统计存在系统性的除权噪声，方向影响视分红密度而定；属于方法论口径问题，当前代码只是默认了一种未经验证的口径。

### P15 历史成分存在 2021 版体系回写偏差且系统无提示

现状核实：

- 成分事件只采纳 `update_time >= 2021-07-01` 的申万官网“回写”记录（`industry_data_service.py:173-177`、常量 `SW_CLASSIFICATION_REMAP_SINCE`），即历史成分统一按 2021 版现行行业分类回溯到 2014-02-21 起的 `start_date`。
- 2021 年体系切换会改变部分股票的行业归属，把现行归属直接用于 2021 年以前的扩散计算属于“用当前分类回填历史”，与研报 PIT（point-in-time）口径存在偏差。
- 该边界只在常量注释与使用说明中一句话提及，计算/回测/API 结果中无任何提示或覆盖率说明。
- 影响：早期（2014-2021）回测的扩散分母/分子按 2021 版行业归属计算，与当时真实行业构成不一致；影响程度未量化。

### P16 独立回测逐日收益重复计算与日期错配（阻塞级）

> 处理记录（2026-09-07）：独立回测模拟已随标准回测收敛删除；行业轮动回测
> 统一走 `BacktestService` 行业域分支（T+1 开盘、账户累积、月末调仓），
> 不再存在重复计收益/日期错配路径。

现状核实（代码推演，未用真库净值复核）：

- `simulate_rotation_backtest`（`domain/industry/backtest.py:29-83`）在决策日（T）那一行就计入：旧持仓 `close_T → open_{T+1}` + 新持仓 `open_{T+1} → close_{T+1}`（`backtest.py:50-58`），然后立刻把持仓切换为新目标。
- 到 T+1（非决策日）又按新持仓计算整段 `close_T → close_{T+1}` 收益（`backtest.py:64-71`），等于把新持仓 `open_{T+1} → close_{T+1}` 的日内收益算了两次，还额外计入了一段并不持有的 `close_T → open_{T+1}` 隔夜收益。
- 手工推演（合成数据，2 行业、3 个交易日，决策日为第 2 天、权重各 0.5，1/4 开盘买入）：
  - 决策日当天行 return ≈ 0.9804%（即 1/4 的 open→close）；
  - 次日行 return ≈ 0.9804%（close→close，含同日 open→close 的重复）；
  - 最终 NAV ≈ 1.0197，而正确口径（1/4 开盘买入持有到收盘）应 ≈ 1.0098。
- 逐日收益还被记在决策日而非实际成交日（T+1），影响日收益序列、年化/回撤/波动率等全部统计的日期归属。
- 现有单元测试只断言 `nav > 1.0` 与持仓字段存在（`tests/unit/test_industry_backtest.py:43-49`），无法发现该口径错误。
- 换手统计附带存疑点：从空仓到首次建仓按 `sum(|Δw|)/2` 只计 0.5（`backtest.py:88-97`），初始买入实际换手应为 1.0 量级。
- 影响：**当前独立回测净值不可信，是“策略达到可回测”的首要阻塞项之一**。

### P17 独立回测无基准/行业等权对照输出

> 处理记录（2026-09-07）：标准回测行业域分支默认提供“申万行业等权（剔除
> 综合）”基准对照，创建页可切换为指数基准；绩效指标/逐日 benchmark_return
> 与统一口径一致。

现状核实：

- `industry backtest` 输出只有 `stats`、`selections`，可选 `daily`/`file`（`cli.py:507-527`）；`IndustryRotationService.run_backtest` 返回 `daily/targets/stats/selections/decision_dates`（`industry_rotation_service.py:82-119`），不含行业等权组合净值、不含沪深300 等基准序列。
- “行业等权”仅存在于 RRG 的基准构造（`factor_algo.equal_weight_benchmark`），不输出为回测对照曲线；通用回测 CLI 的 `--benchmark` 参数只属于标准指数回测链路，而该链路无法运行 rotation 策略（见 P18）。
- 影响：无法在回测输出中直接对比策略 vs 行业等权 vs 宽基，研究判断需自行另算。

### P18 rotation 策略无法走标准策略执行与标准回测链路（阻塞级）

> 处理记录（2026-09-07）：rotation 策略已接入标准实时分配（读预计算行业
> 因子值 → 引擎 rotation 分支）与标准回测中心（行业域分支）；定时持久化
> 运行明确拒绝并在校验/文档/前端说明。复刻策略可直接分配与回测。

现状核实：

- `StrategyEngine.run` 在 `config.rotation` 非空时进入 `_run_rotation`，要求 `context.extra["industry_rotation_input"]` 必须是 `IndustryRotationInput`，否则抛 `ValueError`（`engine/orchestrator.py:227-242`）。
- 全仓库唯一构造 `IndustryRotationInput` 的地方是 `IndustryRotationService._input_for_date`（`services/industry_rotation_service.py:203-224`），由行业独立服务/CLI/调试 API 内部使用。
- 标准实时链路 `StrategyDecisionService.run_allocation`/`run_and_persist`（`services/strategy_decision_service.py:211/367`）与标准回测 `BacktestService._run_backtest_loop`（`services/backtest_service.py:467`）均通过通用 `ContextBuilder` 构建上下文，不注入行业轮动输入；因此用 `industry create-replica-strategy` 创建出的策略：
  - 无法通过 `POST /strategies/{id}/allocation` 获取分配结果；
  - 无法在回测中心/优化闭环/AI 摘要等通用链路中运行；
  - 只能走独立 CLI `industry backtest`、行业服务方法或调试 API。
- `ContextBuilder` 的实时/回测 universe 均来自 `benchmark_index` 体系（`engine/context_builder.py`），对申万 `801xxx` 资产域本就没有构建路径，属同一结构性缺口的两面。
- 影响：复刻策略当前不是系统策略体系中的一等公民，策略中心里能看到配置，但任何通用执行入口都会失败或不可用。

### P19 标准链路打通后信号/因子持久化的指数域与行业域混用风险

> 处理记录（2026-09-07）：本轮将行业轮动策略的持久化运行显式关闭（run_and_persist
> 拒绝并提示），避免 801xxx 写入指数信号/因子快照表；后续扩展持久化时
> 需按文档先扩展信号表资产域。

现状核实：

- `StrategyDecisionService.run_and_persist` 会把引擎输出的 `strategy_results` 无条件写入 `index_signal`、把因子快照写入 `index_factor_value`（`strategy_decision_service.py:404-441`），两表按“指数域”设计；rotation 输出中的 `index_code` 将是 `801xxx` 申万行业代码。
- 行业轮动输入若以“extra 注入”方式接入上述通用路径，不额外加域分流的话，申万行业代码会进入指数信号表，前端指数详情/回测结果等查询语义会混入行业资产。
- 现状是 P18 使该路径尚不可达，但代码上没有任何防线阻止这种混用，属于“打通标准链路时必须处理”的关联风险。

### P20 复刻策略顶层 `frequency=daily` 与调仓 `monthly` 不一致

> 处理记录（2026-09-07）：create-replica-strategy 与前端策略创建已统一为
> 顶层 frequency=monthly（与 rebalance 一致）。

现状核实：

- `industry create-replica-strategy` 写入的 `StrategyConfigCreate.frequency="daily"`（`cli.py:486`），而引擎配置内是 `rebalance.frequency=monthly`（`cli.py:476-478`）。
- 策略列表/详情/星标摘要等处对“运行频率”与“调仓频率”的展示和判断来源不同（顶层 `frequency` 用于任务调度语义，`rebalance` 用于调仓日判断），两者不一致会给 UI 展示和后续调度造成误导。

### P21 API 与调试页错误静默，空数据与失败不可区分

> 处理记录（2026-09-06）：RRG/扩散接口对区间超限与数据缺失返回带明确 message 的
> 422；正常返回携带 `meta.issues`（warn/error/info + 示例日期）；前端问题区按
> 级别展示并保留“暂无结果”与“计算失败”的区分，不再 catch 后静默置空。
> 补充（2026-09-06）：页面“30s 断开、后端无日志”的根因是 axios 全局 30s 超时
> + uvicorn.access 日志被抑制。已新增请求日志中间件（/api/industry 打印开始/
> 完成/耗时，慢请求与中断 warning）、行业接口计算日志；前端对 RRG/扩散请求
> 放宽到 120s，超时错误会携带 request_id 便于到后端日志检索。

现状核实：

- 行业只读接口对内部异常统一转 422 或抛错，但 warm-up 不足/数据为空时返回空列表结构（200）；前端调试页所有请求 `catch` 后置空数组（`RRGLabPage.vue:284/304/324`），用户看到“暂无数据”无法判断是数据没回填、区间不足、还是后端失败。
- `build_panels` 对 RRG/扩散失败的 `logger.warning`（`industry_factor_service.py:118/134`）只进服务端日志，不出现在 API 响应或页面。
- 影响：数据缺失与系统错误在用户侧不可见，与 P10/P13 叠加形成“静默劣化”。

### P22 RRG/扩散接口逐日逐点展开，无区间/数据量上限

> 处理记录（2026-09-06）：RRG 单次上限 3700 自然日、扩散 2200 自然日，超出
> 返回 422 并说明上限；响应 meta 携带有效区间与市场交易日数，帮助前端判断
> 结果规模。仍逐点展开、未做降采样/分页，超长区间响应体控制留作后续专项。

现状核实：

- `/api/industry/rrg` 把每个交易日的每个行业都展开成一个 point 对象（`api/routers/industry.py:79-106`），10 年 × 31 行业约为数万行 JSON；`/diffusion` 同样逐点展开。
- 服务端对 `start/end` 跨度、行业数量、lookback 值均无上限约束（除日期先后校验外）；前端一次拉全量。
- 影响：即使修复 P02 的计算内存，超长区间响应体仍可能让浏览器/网络成为新瓶颈；无分页/降采样/按需窗口机制。

### P23 调试页无侧栏导航入口

> 处理记录（2026-09-06）：`App.vue` 侧栏在“因子中心”下新增
> “RRG / 扩散研究”入口（`/tools/rrg-lab`）。

现状核实：

- 路由 `/tools/rrg-lab` 已注册（`apps/web/src/router/index.ts:34`），但侧栏（`apps/web/src/App.vue` 的 nav 列表）没有对应 `RouterLink`，只能靠手动输入 URL 访问。

### P24 自动化测试覆盖不足（回测测试过浅）

现状核实：

- 行业相关测试仅 5 个纯单元文件：`test_industry_algo.py`、`test_industry_constants.py`、`test_industry_rotation_engine.py`、`test_industry_backtest.py`、`test_industry_membership_panel.py`。
- 覆盖范围只到纯算法/常量/单日选择/浅回测/成分面板重建；**没有**：industry factor service、data service、repositories、API 路由、CLI、job handler、调度器、数据库迁移、扩散分块/内存路径、真库端到端回测的测试。
- `test_industry_backtest.py` 对收益正确性只断言 `nav > 1.0` 与末行持仓（见 P16），无法发现重复计收益问题。
- 影响：即使出现 P16 这类阻塞级口径错误，现有测试仍全绿，CI 无法把守“可回测”底线。

### P25 行业因子计算路径均每次从零重建 warm-up 面板，无增量复用

> 处理记录（2026-09-07）：实时分配改为优先读日频预计算的 industry_factor_value
> （默认参数），回测改为按策略参数一次性预计算面板并复用；研究页仍保留
> 即时计算但不落库。

现状核实：

- 三处计算入口全部调用同一个 `IndustryFactorService.build_panels` 全量重建：
  - CLI `industry compute-factors`（`cli.py:445-459`）；
  - 每日后台任务 `handle_industry_factor_compute`（`infra/job_queue/handlers.py:329-351`，每次只传单日 start/end，但仍整体重建 warm-up 面板）；
  - 调试/轮动 API 的即时计算。
- 无任何进程内缓存、无增量窗口、无“只算缺失日期”逻辑；同一区间的重复请求会重复执行同量计算。
- 与 P02 关联：这是 P02 的“每次重算”层面，P25 偏重计算架构的重复劳动与无复用，即使内存优化后仍存在重复计算成本。

## 四、整体评估结论（仅现状）

- 阻塞级问题：P16（独立回测收益口径错误）、P02（10M 行全量物化内存）、P18（标准策略/回测链路不可用）、P01（历史数据完整性无法验证与续拉）。
- 数据完整性：P06/P07/P10/P15 叠加使个股快照、历史成分、逐日缺口三个维度都存在“数据已入库但口径/完整性不可信”的风险。
- 因子可信度：P12/P13/P14 说明因子值缺少参数版本、缺口告警与复权口径三个元信息维度，现有值只能按“默认参数、未复权、有缺口被 ffill”的隐含前提解释。
- 可观测性：P03/P21 表明新数据源在状态、质量、补拉、错误呈现四个层面均未进入系统闭环。
- 工程底线：P24 说明现有测试不足以支撑上述问题的回归防护。
