"""Read-only dashboard queries for the paper-trading monitor."""

from decimal import Decimal
import json
from pathlib import Path

from .td_sequential import td_sequential_state


MONITORING_GROUPS = (
    "large_cap_momentum",
    "long_term_liquid_candidate",
    "event_elasticity_candidate",
)

BUY_COST_RATE = Decimal("0.00025")
SELL_COST_RATE = Decimal("0.00075")  # commission + stamp duty; conservative display estimate
STRESS_SHOCKS = (Decimal("-0.03"), Decimal("-0.05"), Decimal("-0.10"))


def _is_historical_account(account_id: str, account_name: str) -> bool:
    """Identify replay-only accounts whose display must not use later market facts.

    Backtests are created by the replay and walk-forward scripts with these
    stable account identifiers/names.  Forward and imported personal accounts
    remain live-monitoring views and may show the latest archived close.
    """
    return ("回测" in account_name or account_id.startswith(("wf_", "ml_", "experiment_")))


def _margin_rules() -> dict:
    return json.loads((Path(__file__).resolve().parent.parent / "config" / "citic_margin_risk_rules.json").read_text())


def _stress_ratio(collateral, debt, shock):
    if debt <= 0:
        return None
    return _number((collateral * (Decimal("1") + shock)) / debt)


def _t_decision(shares, sellable, cost_total, last_close, risk, ticker):
    """Return only the fully constrained, board-lot final observation limit."""
    if last_close is None or not shares:
        return {"label": "禁止：价格或持仓数据不足", "final_add_shares": 0, "post_add_cost": None, "sell_break_even": None,
                "prohibition_reasons": ["价格或持仓数据不足"]}
    if risk is None:
        return {"label": "禁止：未导入完整账户快照", "final_add_shares": 0, "post_add_cost": None, "sell_break_even": None,
                "prohibition_reasons": ["未导入完整账户快照（现金、融资额度、可用保证金、负债）"]}
    if risk["block_reasons"]:
        return {"label": "禁止补仓", "final_add_shares": 0, "post_add_cost": None, "sell_break_even": None,
                "prohibition_reasons": risk["block_reasons"], "constraint_caps": {}, "stress_tests": []}
    close, average = Decimal(str(last_close)), Decimal(str(cost_total)) / Decimal(shares)
    lot = 100
    # Keep the 10%-of-position observation rule, but never turn a valid
    # high-priced small holding into a false prohibition solely because it is
    # below 1,000 shares.  A-share orders still require a full board lot.
    requested_cap = max(lot, int((Decimal(shares) * Decimal("0.10")) / lot) * lot)
    costs_per_share = close * (Decimal("1") + BUY_COST_RATE)
    cash_cap = int(risk["available_cash"] / costs_per_share / lot) * lot
    margin_cap = int(risk["available_margin"] / costs_per_share / lot) * lot
    funding_cap = int((risk["available_cash"] + risk["available_margin"]) / costs_per_share / lot) * lot
    concentration_room = max(Decimal("0"), risk["single_stock_limit"] * risk["collateral_assets"] - close * Decimal(shares))
    concentration_cap = int(concentration_room / close / lot) * lot
    caps = {"现金": cash_cap, "可用保证金": margin_cap, "资金合计": funding_cap, "单票集中度": concentration_cap}
    final_shares = min(requested_cap, funding_cap, concentration_cap)
    reasons = [f"{name}约束为 0 股" for name, cap in caps.items()
               if name in ("资金合计", "单票集中度") and cap <= 0]
    prospective_debt = risk["debt_balance"] + max(Decimal("0"), Decimal(final_shares) * costs_per_share - risk["available_cash"])
    prospective_collateral = risk["collateral_assets"]  # cash purchase only changes collateral composition.
    stress = [{"shock": _number(shock), "maintenance_ratio": _stress_ratio(prospective_collateral, prospective_debt, shock),
               "required_ratio": _number(risk["safety_ratio"])} for shock in STRESS_SHOCKS]
    stress_breaches = [item for item in stress if item["maintenance_ratio"] is not None and item["maintenance_ratio"] < item["required_ratio"]]
    if stress_breaches:
        final_shares = 0
        reasons.append("−3%/−5%/−10% 压力测试未满足维持担保安全线")
    if final_shares <= 0 and not reasons:
        reasons.append("全部账户风控约束后的上限不足 100 股")
    if final_shares <= 0:
        return {"label": "禁止补仓", "final_add_shares": 0, "post_add_cost": None, "sell_break_even": None,
                "prohibition_reasons": reasons, "constraint_caps": caps, "stress_tests": stress}
    post_cost = (Decimal(str(cost_total)) + close * Decimal(final_shares) * (Decimal("1") + BUY_COST_RATE)) / Decimal(shares + final_shares)
    break_even = post_cost / (Decimal("1") - SELL_COST_RATE)
    label = "允许观察" if sellable >= 100 else "T+1锁定，不能做当日卖出"
    return {"label": label, "final_add_shares": final_shares, "post_add_cost": _number(post_cost), "sell_break_even": _number(break_even),
            "prohibition_reasons": [], "constraint_caps": caps, "stress_tests": stress}


def _apply_portfolio_budget(positions, risk):
    """Reject a simultaneous plan when independently computed limits share insufficient funds."""
    if risk is None or risk["block_reasons"]:
        return positions
    total_cost = sum(Decimal(str(row["last_close"])) * Decimal(row["final_add_shares"]) * (Decimal("1") + BUY_COST_RATE)
                     for row in positions if row["last_close"] is not None)
    budget = risk["available_cash"] + risk["available_margin"]
    if total_cost <= budget:
        return positions
    reason = "组合补仓合计超过共享现金与可用保证金；未设置优先级，不生成可执行上限"
    for row in positions:
        if row["final_add_shares"]:
            row.update({"label": "禁止补仓", "final_add_shares": 0, "post_add_cost": None, "sell_break_even": None,
                        "prohibition_reasons": [reason]})
    return positions


def _ema(values, span):
    factor, value = Decimal("2") / Decimal(span + 1), Decimal(str(values[0]))
    for item in values[1:]:
        value += factor * (Decimal(str(item)) - value)
    return value


def _recursive_kdj(highs, lows, closes, window=9):
    """Standard KDJ: initialise K/D at 50 then recurse over every completed bar."""
    if len(closes) < window:
        return None
    k, d = Decimal("50"), Decimal("50")
    for index in range(window - 1, len(closes)):
        period_low, period_high = min(lows[index - window + 1:index + 1]), max(highs[index - window + 1:index + 1])
        rsv = Decimal("50") if period_high == period_low else (closes[index] - period_low) / (period_high - period_low) * 100
        k = Decimal("2") / 3 * k + rsv / 3
        d = Decimal("2") / 3 * d + k / 3
    return k, d, 3 * k - 2 * d


def _priority_scores(connection, positions):
    """Daily-bar timing score for ordering already risk-approved observations only."""
    tickers = [row["ticker"] for row in positions]
    if not tickers:
        return
    placeholders = ",".join("?" for _ in tickers)
    rows = connection.execute(
        "WITH latest_fact AS (SELECT b.*, ROW_NUMBER() OVER (PARTITION BY ticker, trade_date ORDER BY market_snapshot_id DESC) AS fact_no "
        "FROM daily_bars b WHERE ticker IN (" + placeholders + ")), ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date DESC) AS row_no "
        "FROM latest_fact WHERE fact_no = 1) SELECT ticker, trade_date, high, low, close, volume FROM ranked WHERE row_no <= 252 ORDER BY ticker, trade_date", tickers).fetchall()
    bars = {}
    for ticker, day, high, low, close, volume in rows:
        bars.setdefault(ticker, []).append((day, Decimal(str(high)), Decimal(str(low)), Decimal(str(close)), Decimal(str(volume))))
    high_risk = {row[0] for row in connection.execute(
        "SELECT DISTINCT n.ticker FROM news_assessments a JOIN news_documents n ON n.document_id = a.document_id "
        "WHERE a.task_type = 'RISK_VETO' AND json_extract_string(a.result_json, '$.risk_level') = 'HIGH' AND n.ticker IN (" + placeholders + ")", tickers).fetchall()}
    news_rules = json.loads((Path(__file__).resolve().parent.parent / "config" / "news_priority_terms.json").read_text())
    news_rows = connection.execute(
        "SELECT ticker, headline, body, received_at FROM news_documents WHERE ticker IN (" + placeholders + ") ORDER BY received_at DESC", tickers).fetchall()
    latest_received = max((row[3] for row in news_rows), default=None)
    news_by_ticker = {}
    for ticker, headline, body, received_at in news_rows:
        if latest_received is None:
            continue
        age_hours = (latest_received - received_at).total_seconds() / 3600
        weight = Decimal("1") if age_hours <= news_rules["recency_hours"]["fresh"] else Decimal("0.5") if age_hours <= news_rules["recency_hours"]["recent"] else Decimal("0")
        if not weight:
            continue
        text = (headline or "") + " " + (body or "")
        positive = [term for term in news_rules["positive_terms"] if term in text]
        negative = [term for term in news_rules["negative_terms"] if term in text]
        entry = news_by_ticker.setdefault(ticker, {"adjustment": Decimal("0"), "evidence": []})
        entry["adjustment"] += weight * Decimal(4 * len(positive) - 5 * len(negative))
        if positive or negative:
            entry["evidence"].append((received_at, positive, negative))
    for row in positions:
        series = bars.get(row["ticker"], [])
        if len(series) < 27:
            row.update({"priority_score": None, "priority_basis": "日线不足 27 根", "technical_as_of": None})
            continue
        highs, lows, closes, volumes = zip(*[(bar[1], bar[2], bar[3], bar[4]) for bar in series])
        macd, signal = _ema(closes, 12) - _ema(closes, 26), _ema([_ema(closes[:index], 12) - _ema(closes[:index], 26) for index in range(26, len(closes) + 1)], 9)
        prev_macd = _ema(closes[:-1], 12) - _ema(closes[:-1], 26)
        macd_score = (18 if macd > signal else 0) + (6 if macd > 0 else 0) + (6 if macd > prev_macd else 0)
        volume_score = (10 if closes[-1] > closes[-2] else 0) + (10 if volumes[-1] > sum(volumes[-21:-1]) / 20 * Decimal("1.2") else 0) + (5 if closes[-1] >= sum(closes[-20:]) / 20 else 0)
        k, d, j = _recursive_kdj(highs, lows, closes)
        kdj_score = (8 if k > d else 0) + (4 if k < 80 else 0) + (3 if j < 100 else 0)
        td = td_sequential_state(closes)
        td_score = 15 if td.direction == "DOWN" and td.count >= 7 else 8 if td.direction == "DOWN" else 0
        news = news_by_ticker.get(row["ticker"], {"adjustment": Decimal("0"), "evidence": []})
        if row["ticker"] in high_risk:
            news_score, news_basis = 0, "高风险新闻否决"
        else:
            news_score = int(max(Decimal(news_rules["score_bounds"]["min"]), min(Decimal(news_rules["score_bounds"]["max"]), Decimal(news_rules["neutral_score"]) + news["adjustment"])))
            evidence = news["evidence"][:2]
            news_basis = "无近 72 小时归档新闻（中性）" if not evidence else "；".join(
                f"{item[0]:%m-%d %H:%M} +{','.join(item[1]) or '—'} -{','.join(item[2]) or '—'}" for item in evidence)
        row.update({"priority_score": macd_score + volume_score + kdj_score + td_score + news_score,
                    "priority_basis": f"MACD {macd_score}/30 · 量价 {volume_score}/25 · KDJ {kdj_score}/15 · 九转 {td_score}/15 · 新闻 {news_score}/15",
                    "news_basis": news_basis, "technical_as_of": str(series[-1][0]), "news_veto": row["ticker"] in high_risk})


def _assign_add_plan(positions):
    """Convert constrained capacity plus timing evidence into a non-executing action tier."""
    for row in positions:
        if not row["final_add_shares"]:
            row.update({"plan_action": "不补", "plan_reason": "未通过账户硬风控或最终上限为 0"})
        elif row["priority_score"] is None:
            row.update({"plan_action": "观察", "plan_reason": "日线技术数据不足，不进入执行队列"})
        elif row.get("news_veto"):
            row.update({"plan_action": "不补", "plan_reason": "存在高风险新闻否决"})
        elif row["priority_score"] >= 70:
            row.update({"plan_action": "优先补仓", "plan_reason": "评分 ≥ 70，且通过全部账户硬风控"})
        elif row["priority_score"] >= 50:
            row.update({"plan_action": "观察", "plan_reason": "评分 50–69，等待技术确认"})
        else:
            row.update({"plan_action": "不补", "plan_reason": "评分 < 50，技术面未确认"})


def load_dashboard(connection, account_id: str) -> dict:
    """Return a serializable current-state view without changing ledger or trade state."""
    account = connection.execute(
        "SELECT account_name, initial_cash FROM sim_accounts WHERE account_id = ?", [account_id]
    ).fetchone()
    if account is None:
        raise ValueError("simulation account is missing")
    account_name, initial_cash = account
    is_historical_account = _is_historical_account(account_id, account_name)
    nav = connection.execute(
        "SELECT trade_date, cash_balance, securities_value, total_equity, unit_nav, max_drawdown FROM sim_nav_daily "
        "WHERE account_id = ? ORDER BY trade_date DESC LIMIT 60", [account_id]
    ).fetchall()
    nav_as_of_date = nav[0][0] if nav else None
    market_data_as_of_date = connection.execute("SELECT MAX(trade_date) FROM daily_bars").fetchone()[0]
    price_cutoff_date = nav_as_of_date if is_historical_account else None
    margin = connection.execute(
        "SELECT m.annual_financing_rate, COALESCE(SUM(d.outstanding_balance), 0) "
        "FROM sim_margin_accounts m LEFT JOIN sim_margin_debts d ON d.account_id = m.account_id AND d.status = 'OPEN' "
        "WHERE m.account_id = ? GROUP BY m.annual_financing_rate", [account_id]
    ).fetchone()
    price_filter = "WHERE trade_date <= ?" if price_cutoff_date is not None else ""
    position_parameters = [account_id]
    if price_cutoff_date is not None:
        position_parameters.append(price_cutoff_date)
    positions_sql = (
        "WITH disposed AS ("
        "  SELECT lot_id, SUM(shares_deducted) AS shares FROM sim_lot_disposal_events GROUP BY lot_id"
        "), inventory AS ("
        "  SELECT l.ticker, SUM(l.orig_shares - COALESCE(d.shares, 0)) AS shares, "
        "  SUM(CASE WHEN l.available_from_date <= (SELECT MAX(trade_date) FROM daily_bars) "
        "           THEN l.orig_shares - COALESCE(d.shares, 0) ELSE 0 END) AS sellable_shares, "
        "  SUM((l.orig_shares - COALESCE(d.shares, 0)) * l.unadj_unit_cost) AS cost "
        "  FROM sim_position_lots l LEFT JOIN disposed d ON d.lot_id = l.lot_id "
        "  WHERE l.account_id = ? GROUP BY l.ticker"
        "), latest_price AS ("
        "  SELECT ticker, close, trade_date, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date DESC) AS row_no "
        "  FROM daily_bars " + price_filter +
        ") "
        "SELECT i.ticker, COALESCE(s.security_name, i.ticker), i.shares, i.sellable_shares, i.cost, p.close, p.trade_date "
        "FROM inventory i LEFT JOIN latest_price p ON p.ticker = i.ticker AND p.row_no = 1 "
        "LEFT JOIN security_master s ON s.ticker = i.ticker "
        "WHERE i.shares > 0 ORDER BY i.ticker"
    )
    positions = connection.execute(positions_sql, position_parameters).fetchall()
    # A user-verified broker snapshot supplies real quantities/costs; prices
    # remain dynamically marked to the latest local daily close.
    broker_positions = connection.execute(
        "WITH latest_snapshot AS (SELECT snapshot_id, observed_at FROM external_account_snapshots WHERE account_id = ? "
        "ORDER BY observed_at DESC, created_at DESC LIMIT 1) "
        "SELECT h.ticker, COALESCE(s.security_name,h.ticker), h.shares, h.sellable_shares, "
        "h.shares*h.unit_cost, h.market_price, CAST(x.observed_at AS DATE) "
        "FROM external_account_snapshot_holdings h JOIN latest_snapshot x USING(snapshot_id) "
        "LEFT JOIN security_master s ON s.ticker=h.ticker "
        "ORDER BY h.ticker", [account_id]
    ).fetchall()
    snapshot_columns = {row[1] for row in connection.execute("PRAGMA table_info('external_account_snapshots')").fetchall()}
    broker_snapshot = None if not {"available_cash", "available_margin"}.issubset(snapshot_columns) else connection.execute(
        "SELECT collateral_assets, debt_balance, financing_limit, financing_used, accrued_financing_interest, available_cash, available_margin, "
        "maintenance_ratio, chinext_star_concentration, observed_at, source_reference "
        "FROM external_account_snapshots WHERE account_id = ? ORDER BY observed_at DESC, created_at DESC LIMIT 1",
        [account_id],
    ).fetchone()
    if broker_positions:
        positions = broker_positions
    pending = connection.execute(
        "SELECT o.ticker, COALESCE(s.security_name, o.ticker), e.trigger_code, o.target_trade_date FROM sim_order_intents o "
        "JOIN sim_exit_signals e ON e.intent_id = o.intent_id "
        "LEFT JOIN security_master s ON s.ticker = o.ticker "
        "WHERE o.account_id = ? AND o.order_status = 'PENDING' ORDER BY o.target_trade_date, o.ticker",
        [account_id],
    ).fetchall()
    refresh = connection.execute(
        "SELECT trade_date, completed_at, status, portfolio_snapshot_id, top50_snapshot_id, detail_json "
        "FROM local_refresh_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    recommendations = connection.execute(
        "SELECT r.target_trade_date, i.ticker, COALESCE(s.security_name, i.ticker), i.rank_order, i.rank_score, i.ref_close_unadj, "
        "COALESCE(m.execution_mode, 'PRODUCTION') "
        "FROM recommendation_runs r JOIN recommendation_items i ON i.run_id = r.run_id "
        "LEFT JOIN recommendation_run_modes m ON m.run_id = r.run_id "
        "LEFT JOIN security_master s ON s.ticker = i.ticker "
        "WHERE r.run_status = 'FROZEN' ORDER BY r.target_trade_date DESC, i.rank_order LIMIT 20"
    ).fetchall()
    ml_model = connection.execute(
        "SELECT model_sha256, model_type, artifact_path, trained_through_date, training_rows, created_at "
        "FROM ml_model_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    ml_recommendations = connection.execute(
        "SELECT r.target_trade_date, i.ticker, COALESCE(s.security_name, i.ticker), i.rank_order, i.rank_score "
        "FROM recommendation_runs r JOIN recommendation_items i ON i.run_id = r.run_id "
        "LEFT JOIN security_master s ON s.ticker = i.ticker WHERE r.strategy_id = 'ml_excess_return_shadow_v1' "
        "AND r.run_status = 'FROZEN' ORDER BY r.target_trade_date DESC, i.rank_order LIMIT 5"
    ).fetchall()
    summary = _summary(connection, account_id, nav)
    account_risk = None
    if broker_snapshot and all(value is not None for value in broker_snapshot[:7]):
        rules = _margin_rules()
        collateral, debt, limit, used, accrued_interest, cash, available_margin, ratio, chinext_star, observed_at, reference = broker_snapshot
        collateral, debt, limit, used, accrued_interest, cash, available_margin = (Decimal(str(value)) for value in (collateral, debt, limit, used, accrued_interest, cash, available_margin))
        concentration = Decimal(str(chinext_star or 0))
        tier = next(item for item in rules["chinext_star_concentration_tiers"]
                    if item["max_concentration"] is None or concentration <= Decimal(item["max_concentration"]))
        reconciliation_difference = abs(debt - used - accrued_interest)
        latest_market_date = connection.execute("SELECT MAX(trade_date) FROM daily_bars").fetchone()[0]
        block_reasons = []
        if reconciliation_difference > Decimal(rules["financing_used_debt_reconciliation_tolerance"]):
            block_reasons.append(f"负债与已用融资额度差额 ¥{reconciliation_difference:,.2f} 未完成对账")
        if latest_market_date and observed_at.date() < latest_market_date and (latest_market_date - observed_at.date()).days > int(rules["snapshot_max_market_days"]):
            block_reasons.append("账户快照已超过允许的市场数据时效")
        account_risk = {
            "collateral_assets": collateral, "debt_balance": debt, "financing_limit": limit, "financing_used": used, "accrued_financing_interest": accrued_interest,
            "available_cash": cash, "available_margin": min(available_margin, max(Decimal("0"), limit - used)),
            "maintenance_ratio": None if ratio is None else Decimal(str(ratio)), "chinext_star_concentration": concentration,
            "single_stock_limit": Decimal(rules["single_stock_concentration_limit"]), "safety_ratio": Decimal(tier["safety_ratio"]),
            "debt_reconciliation_difference": reconciliation_difference, "block_reasons": block_reasons,
            "observed_at": str(observed_at), "source_reference": reference,
        }
    margin_view = None
    if margin is not None:
        accrued_interest = connection.execute(
            "SELECT COALESCE(SUM(e.interest_amount), 0) FROM sim_margin_interest_events e "
            "JOIN sim_margin_debts d ON d.debt_id = e.debt_id WHERE d.account_id = ?", [account_id]
        ).fetchone()[0]
        debt_balance = _number(margin[1])
        # A collateral ratio is meaningful only after a daily valuation has priced every position.
        collateral_assets = None if not nav else summary["equity"] + debt_balance
        margin_view = {
            "annual_financing_rate": _number(margin[0]),
            "debt_balance": debt_balance,
            "accrued_interest": _number(accrued_interest),
            "net_assets": None if not nav else summary["equity"],
            "collateral_assets": collateral_assets,
            "maintenance_ratio": None if not debt_balance or collateral_assets is None else collateral_assets / debt_balance,
        }
    position_rows = [
        ({
            "ticker": ticker, "security_name": name, "shares": shares, "sellable_shares": sellable,
            "locked_shares": shares - sellable, "cost": _number(cost), "last_close": _number(close),
            "price_date": str(price_date) if price_date else None,
            "unrealized_return": _return(close, cost, shares),
        } | _t_decision(shares, sellable, cost, close, account_risk, ticker))
        for ticker, name, shares, sellable, cost, close, price_date in positions
    ]
    position_rows = _apply_portfolio_budget(position_rows, account_risk)
    _priority_scores(connection, position_rows)
    _assign_add_plan(position_rows)
    return {
        "account": {"id": account_id, "name": account_name, "initial_cash": _number(initial_cash)},
        "is_personal_account": account_id.startswith("personal_"),
        "is_historical_account": is_historical_account,
        "account_nav_as_of_date": None if nav_as_of_date is None else str(nav_as_of_date),
        "market_data_as_of_date": None if market_data_as_of_date is None else str(market_data_as_of_date),
        "margin": margin_view,
        "account_risk": None if account_risk is None else {key: _number(value) if isinstance(value, Decimal) else value
                                                              for key, value in account_risk.items()},
        "local_refresh": None if refresh is None else {
            "trade_date": str(refresh[0]), "completed_at": str(refresh[1]) if refresh[1] else None,
            "status": refresh[2], "portfolio_snapshot_id": refresh[3], "top50_snapshot_id": refresh[4],
            "detail": refresh[5],
        },
        "summary": summary,
        "positions": position_rows,
        "pending_exits": [
            {"ticker": ticker, "security_name": name, "trigger": trigger, "target_date": str(target_date)}
            for ticker, name, trigger, target_date in pending
        ],
        "nav": [
            {"date": str(day), "cash": _number(cash), "market_value": _number(value), "equity": _number(equity), "unit_nav": _number(unit_nav), "drawdown": _number(drawdown)}
            for day, cash, value, equity, unit_nav, drawdown in reversed(nav)
        ],
        "recommendations": [
            {"scope": "事件影子" if mode == "SHADOW" else "生产基线", "target_date": str(day), "ticker": ticker, "security_name": name, "rank": rank, "score": _number(score), "reference_close": _number(close)}
            for day, ticker, name, rank, score, close, mode in recommendations
        ],
        "ml_shadow": {"model": None if ml_model is None else {"sha256": ml_model[0], "type": ml_model[1], "artifact_path": ml_model[2],
                       "trained_through_date": str(ml_model[3]), "training_rows": ml_model[4], "created_at": str(ml_model[5])},
                      "recommendations": [{"target_date": str(day), "ticker": ticker, "security_name": name, "rank": rank, "score": _number(score)}
                                          for day, ticker, name, rank, score in ml_recommendations]},
        "exit_rules": {"stop_loss": -0.10, "take_profit_gate": 0.15, "trailing_drawdown": 0.05, "max_holding_days": 60},
    }


def _summary(connection, account_id, nav):
    cash = connection.execute(
        "SELECT COALESCE(SUM(debit_amount - credit_amount), 0) FROM ledger_journal_entries "
        "WHERE account_id = ? AND account_code = '1001'", [account_id]
    ).fetchone()[0]
    if not nav:
        return {"cash": _number(cash), "market_value": 0.0, "equity": _number(cash), "max_drawdown": 0.0}
    _, _, market_value, equity, _, drawdown = nav[0]
    return {"cash": _number(cash), "market_value": _number(market_value), "equity": _number(equity), "max_drawdown": _number(drawdown)}


def load_monitoring_research(connection) -> dict:
    """Return the latest immutable snapshots and recommendations for research groups."""
    placeholders = ",".join("?" for _ in MONITORING_GROUPS)
    snapshots = connection.execute(
        "WITH ranked AS ("
        "SELECT u.universe_snapshot_id, u.group_name, u.as_of_trade_date, u.market_cap_snapshot_id, "
        "u.listing_snapshot_id, u.st_backfill_run_id, u.rule_version, u.rule_json, u.created_at, "
        "ROW_NUMBER() OVER (PARTITION BY u.group_name ORDER BY u.as_of_trade_date DESC, u.created_at DESC) AS row_no "
        "FROM universe_snapshots u WHERE u.group_name IN (" + placeholders + ")"
        ") SELECT r.universe_snapshot_id, r.group_name, r.as_of_trade_date, r.market_cap_snapshot_id, "
        "r.listing_snapshot_id, r.st_backfill_run_id, r.rule_version, r.rule_json, r.created_at, COUNT(m.ticker) "
        "FROM ranked r LEFT JOIN universe_members m ON m.universe_snapshot_id = r.universe_snapshot_id "
        "WHERE r.row_no = 1 GROUP BY 1,2,3,4,5,6,7,8,9 ORDER BY r.group_name",
        list(MONITORING_GROUPS),
    ).fetchall()
    strategy_ids = [f"momentum_trend_{name}" for name in MONITORING_GROUPS]
    recommendations = connection.execute(
        "WITH ranked_runs AS ("
        "SELECT r.run_id, r.strategy_id, r.target_trade_date, r.effective_as_of_timestamp, r.created_at, "
        "ROW_NUMBER() OVER (PARTITION BY r.strategy_id ORDER BY r.target_trade_date DESC, r.created_at DESC) AS row_no "
        "FROM recommendation_runs r WHERE r.run_status = 'FROZEN' AND r.strategy_id IN ("
        + ",".join("?" for _ in strategy_ids) + ")"
        ") SELECT r.strategy_id, r.run_id, r.target_trade_date, r.effective_as_of_timestamp, i.rank_order, i.ticker, "
        "COALESCE(s.security_name, i.ticker), i.rank_score, i.ref_close_unadj "
        "FROM ranked_runs r JOIN recommendation_items i ON i.run_id = r.run_id "
        "LEFT JOIN security_master s ON s.ticker = i.ticker WHERE r.row_no = 1 "
        "ORDER BY r.strategy_id, i.rank_order",
        strategy_ids,
    ).fetchall()
    snapshot_rows = []
    for row in snapshots:
        (snapshot_id, group_name, as_of_date, cap_id, listing_id, st_id, rule_version,
         rule_json, created_at, member_count) = row
        snapshot_rows.append({
            "group": group_name, "snapshot_id": snapshot_id, "as_of_date": str(as_of_date),
            "market_cap_snapshot_id": cap_id, "listing_snapshot_id": listing_id,
            "st_backfill_run_id": st_id, "rule_version": rule_version,
            "rule": json.loads(rule_json), "created_at": str(created_at), "member_count": member_count,
        })
    recommendation_rows = []
    for strategy_id, run_id, target_date, effective_as_of, rank, ticker, name, score, close in recommendations:
        recommendation_rows.append({
            "group": strategy_id.removeprefix("momentum_trend_"), "run_id": run_id,
            "target_date": str(target_date), "effective_as_of": str(effective_as_of), "rank": rank,
            "ticker": ticker, "security_name": name, "score": _number(score), "reference_close": _number(close),
        })
    return {"snapshots": snapshot_rows, "recommendations": recommendation_rows}


def load_activity(connection, account_id: str) -> dict:
    """Return immutable order, execution and rejection records for presentation."""
    orders = connection.execute(
        "SELECT o.target_trade_date, o.ticker, COALESCE(s.security_name, o.ticker), o.direction, o.target_shares, o.order_status, o.reject_reason_code "
        "FROM sim_order_intents o LEFT JOIN security_master s ON s.ticker = o.ticker "
        "WHERE o.account_id = ? ORDER BY o.created_at DESC LIMIT 100", [account_id]
    ).fetchall()
    executions = connection.execute(
        "SELECT e.trade_date, e.ticker, COALESCE(s.security_name, e.ticker), e.direction, e.deal_price_unadj, e.deal_shares, e.gross_amount "
        "FROM sim_executions e LEFT JOIN security_master s ON s.ticker = e.ticker "
        "WHERE e.account_id = ? ORDER BY e.created_at DESC LIMIT 100", [account_id]
    ).fetchall()
    rejects = connection.execute(
        "SELECT COALESCE(reject_reason_code, 'UNKNOWN'), COUNT(*) FROM sim_order_intents "
        "WHERE account_id = ? AND order_status = 'REJECTED' GROUP BY 1 ORDER BY 2 DESC", [account_id]
    ).fetchall()
    return {
        "orders": [dict(zip(("date", "ticker", "security_name", "direction", "shares", "status", "reason"), map(_display, row))) for row in orders],
        "executions": [dict(zip(("date", "ticker", "security_name", "direction", "price", "shares", "amount"), map(_display, row))) for row in executions],
        "reject_reasons": [{"reason": reason, "count": count} for reason, count in rejects],
    }


def load_track_evaluations(connection, limit: int = 20) -> list:
    """Aggregate persisted, cost-aware track evaluations for visual comparison."""
    if limit <= 0:
        raise ValueError("track evaluation limit must be positive")
    rows = connection.execute(
        "SELECT r.target_trade_date, p.evaluation_version, COALESCE(m.execution_mode, 'PRODUCTION'), "
        "COUNT(*), AVG(CASE WHEN p.is_executed THEN 1.0 ELSE 0.0 END), AVG(p.t1_abs_return), AVG(p.t1_excess_return), "
        "AVG(p.t5_abs_return), AVG(p.t5_excess_return), AVG(p.t20_abs_return), AVG(p.t20_excess_return), AVG(p.max_close_drawdown) "
        "FROM performance_evaluations p JOIN recommendation_items i ON i.item_id = p.recommendation_item_id "
        "JOIN recommendation_runs r ON r.run_id = i.run_id LEFT JOIN recommendation_run_modes m ON m.run_id = r.run_id "
        "GROUP BY 1, 2, 3 ORDER BY r.target_trade_date DESC, p.evaluation_version DESC, COALESCE(m.execution_mode, 'PRODUCTION') LIMIT ?", [limit]
    ).fetchall()
    fields = ("target_date", "evaluation_version", "mode", "recommendation_count", "execution_rate", "t1_return", "t1_excess",
              "t5_return", "t5_excess", "t20_return", "t20_excess", "max_drawdown")
    return [dict(zip(fields, (_display(value) for value in row))) for row in rows]


def load_news_monitor(connection, limit: int = 50) -> dict:
    """Return archived news and gated event hypotheses for the read-only monitor."""
    if limit <= 0:
        raise ValueError("news monitor limit must be positive")
    documents = connection.execute(
        "SELECT document_id, source_channel, published_at, received_at, headline, source_url, raw_artifact_sha256 "
        "FROM news_documents ORDER BY received_at DESC LIMIT ?", [limit]
    ).fetchall()
    requests = connection.execute(
        "SELECT source_channel, requested_at, http_status, cache_hit, backoff_seconds, error_code "
        "FROM external_request_audit ORDER BY requested_at DESC LIMIT ?", [limit],
    ).fetchall()
    hypotheses = connection.execute(
        "SELECT hypothesis_id, effective_as_of_timestamp, evidence_quality, evidence_domain_count, status, rejection_reason "
        "FROM macro_event_hypotheses ORDER BY created_at DESC LIMIT ?", [limit],
    ).fetchall()
    impacts = connection.execute(
        "SELECT i.hypothesis_id, i.industry_code, t.industry_name, i.impact_direction, i.event_score, "
        "i.expected_duration_days, i.uncertainty_text "
        "FROM macro_event_impacts i JOIN sw_industry_taxonomy t "
        "ON t.taxonomy_version = i.taxonomy_version AND t.industry_code = i.industry_code "
        "ORDER BY i.hypothesis_id, i.industry_code"
    ).fetchall()
    impacts_by_hypothesis = {}
    for hypothesis_id, code, name, direction, score, duration, uncertainty in impacts:
        impacts_by_hypothesis.setdefault(hypothesis_id, []).append({
            "industry_code": code, "industry_name": name, "direction": direction, "score": _number(score),
            "duration_days": duration, "uncertainty": uncertainty,
        })
    clusters = connection.execute(
        "SELECT c.event_cluster_id, c.algorithm_version, c.earliest_published_at, c.latest_published_at, "
        "COUNT(m.document_id), SUM(CASE WHEN m.membership_role = 'REPRESENTATIVE' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN m.membership_role IN ('DUPLICATE', 'SYNDICATED') THEN 1 ELSE 0 END) "
        "FROM news_event_clusters c LEFT JOIN news_event_cluster_members m ON m.event_cluster_id = c.event_cluster_id "
        "GROUP BY 1, 2, 3, 4 ORDER BY c.latest_published_at DESC LIMIT ?", [limit]
    ).fetchall()
    cluster_hypotheses = connection.execute(
        "SELECT event_cluster_id, hypothesis_id FROM macro_event_hypothesis_clusters"
    ).fetchall()
    hypotheses_by_cluster = {}
    for cluster_id, hypothesis_id in cluster_hypotheses:
        hypotheses_by_cluster.setdefault(cluster_id, []).append(hypothesis_id)
    risk_rows = connection.execute(
        "WITH latest_review AS ("
        " SELECT assessment_id, review_label, reviewer, rationale, created_at, "
        " ROW_NUMBER() OVER (PARTITION BY assessment_id ORDER BY created_at DESC, review_event_id DESC) AS row_no "
        " FROM news_risk_review_events"
        ") SELECT a.assessment_id, n.ticker, n.headline, a.result_json, a.created_at, "
        " r.review_label, r.reviewer, r.rationale, r.created_at FROM news_assessments a "
        "JOIN news_documents n ON n.document_id = a.document_id "
        "LEFT JOIN latest_review r ON r.assessment_id = a.assessment_id AND r.row_no = 1 "
        "WHERE a.task_type = 'RISK_VETO' ORDER BY a.created_at DESC LIMIT ?", [limit]
    ).fetchall()
    review_summary = _risk_review_summary(connection)
    health = _news_health(connection)
    return {
        "documents": [{"id": doc_id, "source": source, "published_at": str(published), "received_at": str(received),
                       "headline": headline, "url": url, "artifact_sha256": artifact}
                      for doc_id, source, published, received, headline, url, artifact in documents],
        "requests": [{"source": source, "requested_at": str(requested), "status": status, "cache_hit": cache_hit,
                      "backoff_seconds": _number(backoff), "error": error}
                     for source, requested, status, cache_hit, backoff, error in requests],
        "hypotheses": [{"id": hyp_id, "effective_as_of": str(as_of), "evidence_quality": quality,
                         "domain_count": domain_count, "status": status, "rejection_reason": reason,
                         "impacts": impacts_by_hypothesis.get(hyp_id, [])}
                        for hyp_id, as_of, quality, domain_count, status, reason in hypotheses],
        "clusters": [{"id": cluster_id, "algorithm": algorithm, "earliest_published_at": str(earliest),
                      "latest_published_at": str(latest), "document_count": document_count,
                      "representative_count": representative_count, "excluded_count": excluded_count,
                      "hypothesis_ids": hypotheses_by_cluster.get(cluster_id, [])}
                     for cluster_id, algorithm, earliest, latest, document_count, representative_count, excluded_count in clusters],
        "risk_assessments": [{"assessment_id": assessment_id, "ticker": ticker, "headline": headline,
                              "created_at": str(created), "result": json.loads(result_json),
                              "review": None if label is None else {"label": label, "reviewer": reviewer,
                              "rationale": rationale, "created_at": str(reviewed_at)}}
                             for assessment_id, ticker, headline, result_json, created, label, reviewer, rationale, reviewed_at in risk_rows],
        "risk_review_summary": review_summary,
        "health": health,
    }


def _news_health(connection) -> dict:
    """Expose read-only coverage and source-failure signals for the news pipeline."""
    rows = connection.execute(
        "SELECT source_channel, scope, COUNT(*), MAX(received_at) FROM news_documents "
        "GROUP BY source_channel, scope ORDER BY source_channel, scope"
    ).fetchall()
    sources = [{"source": source, "scope": scope, "document_count": count, "latest_received_at": str(latest)}
               for source, scope, count, latest in rows]
    latest_received = connection.execute("SELECT MAX(received_at) FROM news_documents").fetchone()[0]
    stock_documents = connection.execute("SELECT COUNT(*) FROM news_documents WHERE scope = 'STOCK'").fetchone()[0]
    failed_sources = connection.execute(
        "WITH latest AS (SELECT source_channel, requested_at, http_status, "
        "ROW_NUMBER() OVER (PARTITION BY source_channel ORDER BY requested_at DESC, request_id DESC) AS row_no "
        "FROM external_request_audit) "
        "SELECT source_channel, requested_at FROM latest WHERE row_no = 1 "
        "AND (http_status IS NULL OR http_status < 200 OR http_status >= 300) ORDER BY source_channel"
    ).fetchall()
    pdf_row = connection.execute(
        "SELECT COUNT(*), SUM(CASE WHEN http_status BETWEEN 200 AND 299 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN http_status IS NULL OR http_status < 200 OR http_status >= 300 THEN 1 ELSE 0 END), MAX(requested_at) "
        "FROM external_request_audit WHERE source_channel = 'cninfo_pdf'"
    ).fetchone()
    ocr_row = connection.execute(
        "SELECT COUNT(*), SUM(CASE WHEN extraction_status = 'SUCCESS' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN extraction_status = 'RETRYABLE_FAILURE' THEN 1 ELSE 0 END), MAX(created_at) "
        "FROM news_document_text_extractions WHERE extractor_version = 'cninfo_pdf_tesseract_chi_sim_v1'"
    ).fetchone()
    ocr_pending = connection.execute(
        "WITH latest_pdf AS ("
        " SELECT document_id, extraction_status, created_at, "
        " ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY created_at DESC, extraction_id DESC) AS row_no "
        " FROM news_document_text_extractions WHERE extractor_version = 'cninfo_pdf_pypdf_v1'"
        "), attempted_ocr AS ("
        " SELECT DISTINCT document_id FROM news_document_text_extractions "
        " WHERE extractor_version = 'cninfo_pdf_tesseract_chi_sim_v1'"
        ") SELECT COUNT(*) FROM latest_pdf p LEFT JOIN attempted_ocr o USING(document_id) "
        "WHERE p.row_no = 1 AND p.extraction_status = 'OCR_REQUIRED' AND o.document_id IS NULL"
    ).fetchone()[0]
    ocr_failures = connection.execute(
        "WITH latest_ocr AS ("
        " SELECT error_code, created_at, ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY created_at DESC, extraction_id DESC) AS row_no "
        " FROM news_document_text_extractions WHERE extractor_version = 'cninfo_pdf_tesseract_chi_sim_v1'"
        ") SELECT COALESCE(error_code, 'UNKNOWN'), COUNT(*) FROM latest_ocr "
        "WHERE row_no = 1 AND error_code IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1"
    ).fetchall()
    coverage = _latest_news_coverage(connection)
    alerts = []
    if stock_documents == 0:
        alerts.append("NO_STOCK_NEWS")
    if latest_received is None:
        alerts.append("NO_NEWS_ARCHIVED")
    if failed_sources:
        alerts.append("SOURCE_FAILURES")
    return {"latest_received_at": None if latest_received is None else str(latest_received),
            "stock_document_count": stock_documents, "failed_sources": [{"source": source, "last_failed_at": str(at)}
                                                                          for source, at in failed_sources],
            "cninfo_pdf": {"attempted": pdf_row[0], "succeeded": int(pdf_row[1] or 0), "failed": int(pdf_row[2] or 0),
                           "last_attempt_at": None if pdf_row[3] is None else str(pdf_row[3])},
            "cninfo_ocr": {"attempted": ocr_row[0], "succeeded": int(ocr_row[1] or 0),
                           "failed": int(ocr_row[2] or 0), "pending": ocr_pending,
                           "success_rate": None if ocr_row[0] == 0 else float(ocr_row[1] or 0) / ocr_row[0],
                           "last_attempt_at": None if ocr_row[3] is None else str(ocr_row[3]),
                           "failure_reasons": [{"error": error, "count": count} for error, count in ocr_failures]},
            "coverage": coverage,
            "alerts": alerts, "sources": sources}


def _latest_news_coverage(connection) -> dict:
    row = connection.execute(
        "SELECT s.artifact_reference FROM pipeline_run_stages s JOIN pipeline_runs r USING(pipeline_run_id) "
        "WHERE s.stage_name = 'news' AND s.stage_status = 'SUCCEEDED' ORDER BY r.trade_date DESC, s.completed_at DESC LIMIT 1"
    ).fetchone()
    if row is None or not row[0]:
        return {}
    try:
        payload = json.loads(Path(row[0]).read_text(encoding="utf-8"))
        return payload.get("coverage", {})
    except (OSError, ValueError, TypeError):
        return {}


def _risk_review_summary(connection) -> dict:
    """Dashboard-only aggregate; facts and review events remain immutable."""
    table_exists = connection.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'news_risk_review_events'"
    ).fetchone()
    if table_exists is None:
        return {"high_assessment_count": 0, "reviewed_high_count": 0, "confirmed_risk_count": 0,
                "false_positive_count": 0, "uncertain_count": 0, "review_precision": None}
    from quant_core.news_risk_review import review_metrics
    return review_metrics(connection)


def _return(close, cost, shares):
    if close is None or not shares or not cost:
        return None
    return _number((Decimal(str(close)) * Decimal(shares) / Decimal(str(cost))) - Decimal("1"))


def _number(value):
    return None if value is None else float(value)


def _display(value):
    return str(value) if hasattr(value, "isoformat") else _number(value) if isinstance(value, Decimal) else value
