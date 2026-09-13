# Quant Audit Core

本项目是独立的日频模拟交易审计内核。第一期只实现主板股票的账户、双式记账、FIFO 批次和 T+1 约束；不接入行情、撮合、公司行为、机器学习或 LLM。

当前已完成 M1–M5：审计账本、日频模拟撮合、快照因果校验、规则基线推荐与绩效报告。

每日模拟交易通过 `create_sim_account.py` 初始化账户，再由 `settle_frozen_buys.py` 依照冻结推荐的排名创建买入意图、日频撮合并写入账本、批次与 NAV。该命令要求目标日行情已包含真实 `limit_up` 和 `limit_down`。

`snapshot_eastmoney_spot.py` 用于持仓和候选池的低频实时快照，保存总市值、涨跌停价和日内行情。它不用于回填历史回测数据，也不应对全市场进行高频轮询。

所有真实行情在入库前执行价格、OHLC 与涨跌停字段校验；快照同时保存原始接口响应、标准化记录及其哈希。缺失或不完整字段会被拒绝，系统不会根据规则推算真实行情值。

`run_monitoring_groups.py` 在收盘后读取多个历史/当日行情快照和 `config/monitoring_groups.json`，为每个动态分组分别冻结下一交易日推荐。随后于下一交易日使用 `settle_frozen_buys.py` 结算。

`config/monitoring_groups.json` 同时支持 `dynamic`（市值/动量筛选）和 `fixed`（显式 ticker 列表）分组。固定组不会随账户后续交易自动改变；如需更新，需显式修改该配置并重新生成 universe snapshot。

`generate_daily_report.py` 输出统一日报，列出每个推荐批次、成交/拒绝/待处理订单、拒绝原因及指定模拟账户的 NAV。

本地模拟盘看板使用 Streamlit，只读 DuckDB，不会修改交易或账本记录。首次运行安装项目依赖后执行：

```bash
python -m pip install -e .
streamlit run scripts/streamlit_dashboard.py -- --db data/top50/quant_dashboard.duckdb --account-id top50_forward_account
```

打开 `http://127.0.0.1:8501` 即可查看总览、净值、持仓、推荐、风控、订单、新闻事件与影子评估。看板读取定时任务完成后发布的 `quant_dashboard.duckdb` 副本；不要让 Streamlit 直接读取主库 `quant.duckdb`，否则会和日终写任务竞争 DuckDB 文件锁。项目级 `.streamlit/config.toml` 已将服务限制为本机访问，并关闭 Streamlit 首次启动时的统计/邮件提示。

事件影子与生产基线共用同一套日频开盘撮合和成本模型，但影子轨绝不创建账户订单。推荐后的完整 T+20 行情与沪深 300 基准数据到齐后，使用同一份已校验的行情快照运行评估：

```bash
python scripts/evaluate_shadow_tracks.py \
  --db data/top50/quant.duckdb \
  --all-paired \
  --market-snapshot-id <包含推荐日到T+20日且已补齐涨跌停价的快照> \
  --benchmark-ticker 000300.SH
```

未满 T+20 的推荐只会被报告为 `pending_maturity_count`，不会写入绩效表；因此看板只展示完整持有窗口的可比结果。

`run_signal_study.py` 可在不含涨跌停价的免费历史 K 线上执行滚动信号研究：每个信号仅使用当日及之前的行情，输出 T+1/T+5/T+20 收盘方向统计。它不是成交回测，不能替代后续的严格模拟交易。

本地日线 CSV 需要字段：`trade_date,ticker,open,high,low,close,volume,amount,limit_up,limit_down,status`。

生产日流程由 `daily_pipeline.py` 协调，其中涨跌停 enrich 使用 immutable snapshot 入口 `enrich_snapshot_with_tushare_limits.py`。`evaluate_run.py` 保留用于传统、单个 recommendation run 的实际成交评估；production/shadow 的可比评估使用上方的 `evaluate_shadow_tracks.py`。所有时间参数必须包含时区。

真实免费历史日线可先使用 `download_baostock_daily.py` 生成 CSV，再走同一导入链路。该适配器使用不复权日线，且不会伪造涨跌停价；因此它可用于研究与推荐，不能单独作为模拟撮合价格限制的数据来源。

`enrich_with_tushare_limits.py` 仅用于研究或 CSV import workflow：它生成一份带历史涨跌停价的新 CSV，不是 daily/production snapshot 的正式入口。正式日流程使用 `enrich_snapshot_with_tushare_limits.py`，以 immutable market snapshot、原始响应哈希和 manifest 保存来源链路。将 `.env.example` 复制为 `.env` 并填写 `TUSHARE_TOKEN`；`.env` 已被 Git 忽略，且系统环境变量优先于文件。对严格撮合的 CSV 标的可使用 `--require-tickers 600000.SH,000001.SZ` 校验每日边界完整性。

ETF 使用独立的证券类型、费用和交收规则：ETF 卖出不收股票印花税，默认 T+1；只有参考数据明确标记为 T+0 时才允许当日卖出。`sync_security_master.py` 同步 A 股与 ETF 名称。`snapshot_tushare_etf_daily.py` 获取 ETF 研究日线，但该接口不含官方涨跌停价，产生的快照只能用于研究；严格模拟撮合仍要求供应商提供实际涨跌停字段。

动态监控池可用 `snapshot_current_market_cap.py` 获取当前总市值快照，再用 `build_dynamic_universe.py` 以本地日线构建冻结名单；每个名单都保存分组名称、完整筛选参数、当前市值快照与成分股。例如：`--group-name large_cap_momentum --min-total-market-cap 80000000000 --momentum-days 30 --top-n 50`。最后在 `run_daily_strategy.py` 中传入对应的 `--universe-snapshot-id`。当前市值快照仅用于当日监控池，不用于历史回测。

历史回测默认与前向模拟盘使用相同的仓位规则：最多 5 个持仓、等权分配可用资金并保留 5% 现金；默认量能阈值也是 1.5 倍 20 日均量。若要复现旧实验，可显式传入 `--portfolio-method fixed_shares --shares-per-order 100`。

ML 策略先只以影子模式运行：先用 `build_dataset.py` 生成带 PIT 约束和 T+5 超额收益标签的数据集，再用 `train_ml_shadow_model.py` 训练可审计的 Ridge 模型，最后通过 `run_ml_shadow_strategy.py` 冻结预测排名。该命令写入 `SHADOW` recommendation run，结算层会明确排除它，因此不会创建模拟盘订单。训练需要 `python -m pip install -e '.[ml]'`；模型训练截止日必须早于其产生预测的交易日。

在启用每日 ML 影子信号前，使用 `run_ml_ridge_walk_forward.py` 对同一 Ridge 模型做滚动样本外评估。报告会逐窗口记录训练期、测试期、Rank IC 与 ML/规则基线的同期 T+5 毛超额收益；它是研究指标，不替代包含费用、涨跌停和 T+1 的严格成交回测。

`run_ml_strict_backtest.py` 将一份已冻结的 ML 模型接入与前向模拟盘完全相同的开盘撮合、成本、涨跌停、T+1、等权仓位与止损规则。严格回放必须使用独立账户，且回测开始日严格晚于模型训练截止日。

安装开发依赖后运行：`python -m pytest`。
