CREATE TABLE IF NOT EXISTS sim_accounts (
    account_id VARCHAR PRIMARY KEY,
    account_name VARCHAR NOT NULL,
    base_currency VARCHAR NOT NULL DEFAULT 'CNY',
    initial_cash DECIMAL(20,4) NOT NULL CHECK (initial_cash >= 0),
    cash_reinvest_rule VARCHAR NOT NULL,
    cogs_method VARCHAR NOT NULL,
    account_status VARCHAR NOT NULL CHECK (account_status IN ('ACTIVE', 'FROZEN')),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS market_data_snapshots (
    market_snapshot_id VARCHAR PRIMARY KEY,
    trade_date DATE NOT NULL,
    source_channel VARCHAR NOT NULL,
    source_published_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    manifest_path VARCHAR NOT NULL,
    manifest_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_bars (
    market_snapshot_id VARCHAR NOT NULL REFERENCES market_data_snapshots(market_snapshot_id),
    trade_date DATE NOT NULL,
    ticker VARCHAR NOT NULL,
    open DECIMAL(20,4) NOT NULL,
    high DECIMAL(20,4) NOT NULL,
    low DECIMAL(20,4) NOT NULL,
    close DECIMAL(20,4) NOT NULL,
    volume BIGINT NOT NULL,
    amount DECIMAL(20,4) NOT NULL,
    limit_up DECIMAL(20,4),
    limit_down DECIMAL(20,4),
    status VARCHAR NOT NULL,
    PRIMARY KEY (market_snapshot_id, trade_date, ticker)
);

CREATE TABLE IF NOT EXISTS market_cap_snapshots (
    market_cap_snapshot_id VARCHAR PRIMARY KEY,
    captured_at TIMESTAMPTZ NOT NULL,
    source_channel VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS market_cap_values (
    market_cap_snapshot_id VARCHAR NOT NULL REFERENCES market_cap_snapshots(market_cap_snapshot_id),
    ticker VARCHAR NOT NULL,
    total_market_cap DECIMAL(24,4) NOT NULL CHECK (total_market_cap >= 0),
    PRIMARY KEY (market_cap_snapshot_id, ticker)
);

CREATE TABLE IF NOT EXISTS universe_snapshots (
    universe_snapshot_id VARCHAR PRIMARY KEY,
    group_name VARCHAR NOT NULL,
    as_of_trade_date DATE NOT NULL,
    market_cap_snapshot_id VARCHAR NOT NULL REFERENCES market_cap_snapshots(market_cap_snapshot_id),
    rule_version VARCHAR NOT NULL,
    rule_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_members (
    universe_snapshot_id VARCHAR NOT NULL REFERENCES universe_snapshots(universe_snapshot_id),
    ticker VARCHAR NOT NULL,
    total_market_cap DECIMAL(24,4) NOT NULL,
    momentum_return DECIMAL(20,8) NOT NULL,
    rank_order INTEGER NOT NULL CHECK (rank_order > 0),
    PRIMARY KEY (universe_snapshot_id, ticker),
    UNIQUE (universe_snapshot_id, rank_order)
);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    feature_snapshot_id VARCHAR PRIMARY KEY,
    as_of_trade_date DATE NOT NULL,
    lookback_window_days INTEGER NOT NULL CHECK (lookback_window_days > 0),
    input_manifest_hash VARCHAR NOT NULL,
    artifact_path VARCHAR NOT NULL,
    artifact_sha256 VARCHAR NOT NULL,
    max_source_published_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_snapshot_inputs (
    feature_snapshot_id VARCHAR NOT NULL REFERENCES feature_snapshots(feature_snapshot_id),
    market_snapshot_id VARCHAR NOT NULL REFERENCES market_data_snapshots(market_snapshot_id),
    PRIMARY KEY (feature_snapshot_id, market_snapshot_id)
);

CREATE TABLE IF NOT EXISTS recommendation_runs (
    run_id VARCHAR PRIMARY KEY,
    target_trade_date DATE NOT NULL,
    strategy_id VARCHAR NOT NULL,
    strategy_version VARCHAR NOT NULL,
    cost_model_version VARCHAR NOT NULL,
    feature_snapshot_id VARCHAR NOT NULL REFERENCES feature_snapshots(feature_snapshot_id),
    effective_as_of_timestamp TIMESTAMPTZ NOT NULL,
    run_status VARCHAR NOT NULL CHECK (run_status IN ('DRAFT', 'FROZEN', 'INVALID')),
    invalid_reason_code VARCHAR,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_items (
    item_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL REFERENCES recommendation_runs(run_id),
    ticker VARCHAR NOT NULL,
    rank_order INTEGER NOT NULL CHECK (rank_order > 0),
    rank_score DECIMAL(20,8) NOT NULL,
    ref_close_unadj DECIMAL(20,4) NOT NULL,
    rule_reason_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id, ticker),
    UNIQUE (run_id, rank_order)
);

CREATE TABLE IF NOT EXISTS ledger_journal_entries (
    entry_id VARCHAR PRIMARY KEY,
    journal_id VARCHAR NOT NULL,
    account_id VARCHAR NOT NULL REFERENCES sim_accounts(account_id),
    trade_date DATE NOT NULL,
    ref_event_id VARCHAR,
    account_code VARCHAR NOT NULL,
    ticker VARCHAR,
    debit_amount DECIMAL(20,4) NOT NULL DEFAULT 0,
    credit_amount DECIMAL(20,4) NOT NULL DEFAULT 0,
    memo VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CHECK ((debit_amount > 0 AND credit_amount = 0) OR
           (debit_amount = 0 AND credit_amount > 0))
);

CREATE TABLE IF NOT EXISTS sim_position_lots (
    lot_id VARCHAR PRIMARY KEY,
    account_id VARCHAR NOT NULL REFERENCES sim_accounts(account_id),
    ticker VARCHAR NOT NULL,
    buy_event_id VARCHAR NOT NULL,
    buy_trade_date DATE NOT NULL,
    available_from_date DATE NOT NULL,
    orig_shares BIGINT NOT NULL CHECK (orig_shares > 0),
    unadj_unit_cost DECIMAL(20,4) NOT NULL CHECK (unadj_unit_cost > 0),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_order_intents (
    intent_id VARCHAR PRIMARY KEY,
    recommendation_item_id VARCHAR REFERENCES recommendation_items(item_id),
    account_id VARCHAR NOT NULL REFERENCES sim_accounts(account_id),
    ticker VARCHAR NOT NULL,
    target_trade_date DATE NOT NULL,
    direction VARCHAR NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    target_shares BIGINT NOT NULL CHECK (target_shares > 0 AND target_shares % 100 = 0),
    pricing_model VARCHAR NOT NULL,
    order_status VARCHAR NOT NULL CHECK (order_status IN ('PENDING', 'FILLED', 'REJECTED', 'CANCELLED')),
    reject_reason_code VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (recommendation_item_id)
);

CREATE TABLE IF NOT EXISTS sim_executions (
    exec_id VARCHAR PRIMARY KEY,
    intent_id VARCHAR NOT NULL REFERENCES sim_order_intents(intent_id),
    account_id VARCHAR NOT NULL REFERENCES sim_accounts(account_id),
    trade_date DATE NOT NULL,
    ticker VARCHAR NOT NULL,
    direction VARCHAR NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    deal_price_unadj DECIMAL(20,4) NOT NULL,
    deal_shares BIGINT NOT NULL CHECK (deal_shares > 0),
    gross_amount DECIMAL(20,4) NOT NULL,
    commission DECIMAL(20,4) NOT NULL,
    stamp_duty DECIMAL(20,4) NOT NULL,
    transfer_fee DECIMAL(20,4) NOT NULL,
    price_cap_applied BOOLEAN NOT NULL,
    cost_model_version VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_exit_signals (
    exit_signal_id VARCHAR PRIMARY KEY,
    intent_id VARCHAR NOT NULL UNIQUE REFERENCES sim_order_intents(intent_id),
    account_id VARCHAR NOT NULL REFERENCES sim_accounts(account_id),
    ticker VARCHAR NOT NULL,
    signal_trade_date DATE NOT NULL,
    target_trade_date DATE NOT NULL,
    trigger_code VARCHAR NOT NULL,
    reference_close DECIMAL(20,4) NOT NULL,
    peak_close DECIMAL(20,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_lot_disposal_events (
    disposal_id VARCHAR PRIMARY KEY,
    sell_event_id VARCHAR NOT NULL,
    lot_id VARCHAR NOT NULL REFERENCES sim_position_lots(lot_id),
    trade_date DATE NOT NULL,
    shares_deducted BIGINT NOT NULL CHECK (shares_deducted > 0),
    unit_cost_basis DECIMAL(20,4) NOT NULL CHECK (unit_cost_basis > 0),
    total_cost_relieved DECIMAL(20,4) NOT NULL CHECK (total_cost_relieved > 0),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_corporate_action_events (
    action_id VARCHAR PRIMARY KEY,
    ticker VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL CHECK (action_type IN ('CASH_DIVIDEND', 'STOCK_ADJUSTMENT')),
    record_date DATE,
    ex_date DATE,
    payment_date DATE,
    cash_per_share DECIMAL(20,8) NOT NULL DEFAULT 0,
    share_ratio DECIMAL(20,8) NOT NULL DEFAULT 0,
    fractional_cash_price DECIMAL(20,4),
    tax_assumption_version VARCHAR NOT NULL,
    source_payload_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_dividend_entitlements (
    entitlement_id VARCHAR PRIMARY KEY,
    action_id VARCHAR NOT NULL REFERENCES sim_corporate_action_events(action_id),
    account_id VARCHAR NOT NULL REFERENCES sim_accounts(account_id),
    eligible_shares BIGINT NOT NULL CHECK (eligible_shares > 0),
    gross_cash DECIMAL(20,4) NOT NULL CHECK (gross_cash > 0),
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (action_id, account_id)
);

CREATE TABLE IF NOT EXISTS sim_lot_adjustment_events (
    adjustment_id VARCHAR PRIMARY KEY,
    action_id VARCHAR NOT NULL REFERENCES sim_corporate_action_events(action_id),
    lot_id VARCHAR NOT NULL REFERENCES sim_position_lots(lot_id),
    effective_date DATE NOT NULL,
    pre_shares BIGINT NOT NULL CHECK (pre_shares > 0),
    post_shares BIGINT NOT NULL CHECK (post_shares >= 0),
    pre_unit_cost DECIMAL(20,4) NOT NULL,
    post_unit_cost DECIMAL(20,4) NOT NULL,
    fractional_cash_payout DECIMAL(20,4) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (action_id, lot_id)
);

CREATE TABLE IF NOT EXISTS sim_positions_daily (
    account_id VARCHAR NOT NULL REFERENCES sim_accounts(account_id),
    trade_date DATE NOT NULL,
    ticker VARCHAR NOT NULL,
    total_shares BIGINT NOT NULL CHECK (total_shares >= 0),
    available_shares BIGINT NOT NULL CHECK (available_shares >= 0),
    book_cost_balance DECIMAL(20,4) NOT NULL,
    PRIMARY KEY (account_id, trade_date, ticker)
);

CREATE TABLE IF NOT EXISTS sim_nav_daily (
    account_id VARCHAR NOT NULL REFERENCES sim_accounts(account_id),
    trade_date DATE NOT NULL,
    cash_balance DECIMAL(20,4) NOT NULL,
    securities_value DECIMAL(20,4) NOT NULL,
    total_equity DECIMAL(20,4) NOT NULL,
    unit_nav DECIMAL(20,6) NOT NULL,
    max_drawdown DECIMAL(20,6) NOT NULL,
    PRIMARY KEY (account_id, trade_date)
);

CREATE TABLE IF NOT EXISTS performance_evaluations (
    evaluation_id VARCHAR PRIMARY KEY,
    recommendation_item_id VARCHAR NOT NULL REFERENCES recommendation_items(item_id),
    evaluation_version VARCHAR NOT NULL,
    is_executed BOOLEAN NOT NULL,
    t1_abs_return DECIMAL(20,8),
    t1_excess_return DECIMAL(20,8),
    t5_abs_return DECIMAL(20,8),
    t5_excess_return DECIMAL(20,8),
    t20_abs_return DECIMAL(20,8),
    t20_excess_return DECIMAL(20,8),
    max_close_drawdown DECIMAL(20,8),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (recommendation_item_id, evaluation_version)
);

CREATE TABLE IF NOT EXISTS news_documents (
    document_id VARCHAR PRIMARY KEY,
    source_channel VARCHAR NOT NULL,
    external_id VARCHAR,
    scope VARCHAR NOT NULL CHECK (scope IN ('STOCK', 'INDUSTRY', 'MACRO')),
    ticker VARCHAR,
    published_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    headline VARCHAR NOT NULL,
    body TEXT NOT NULL,
    source_url VARCHAR,
    content_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (source_channel, content_sha256)
);

CREATE TABLE IF NOT EXISTS news_assessments (
    assessment_id VARCHAR PRIMARY KEY,
    document_id VARCHAR NOT NULL REFERENCES news_documents(document_id),
    task_type VARCHAR NOT NULL CHECK (task_type IN ('MACRO_EVENT_MAPPING', 'RISK_VETO')),
    provider_name VARCHAR NOT NULL,
    model_name VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    result_json VARCHAR NOT NULL,
    result_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
