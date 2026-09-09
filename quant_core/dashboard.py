"""Read-only dashboard queries for the paper-trading monitor."""

from decimal import Decimal


def load_dashboard(connection, account_id: str) -> dict:
    """Return a serializable current-state view without changing ledger or trade state."""
    account = connection.execute(
        "SELECT account_name, initial_cash FROM sim_accounts WHERE account_id = ?", [account_id]
    ).fetchone()
    if account is None:
        raise ValueError("simulation account is missing")
    positions = connection.execute(
        "WITH disposed AS ("
        "  SELECT lot_id, SUM(shares_deducted) AS shares FROM sim_lot_disposal_events GROUP BY lot_id"
        "), inventory AS ("
        "  SELECT l.ticker, SUM(l.orig_shares - COALESCE(d.shares, 0)) AS shares, "
        "  SUM((l.orig_shares - COALESCE(d.shares, 0)) * l.unadj_unit_cost) AS cost "
        "  FROM sim_position_lots l LEFT JOIN disposed d ON d.lot_id = l.lot_id "
        "  WHERE l.account_id = ? GROUP BY l.ticker"
        "), latest_price AS ("
        "  SELECT ticker, close, trade_date, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date DESC) AS row_no "
        "  FROM daily_bars"
        ") "
        "SELECT i.ticker, i.shares, i.cost, p.close, p.trade_date "
        "FROM inventory i LEFT JOIN latest_price p ON p.ticker = i.ticker AND p.row_no = 1 "
        "WHERE i.shares > 0 ORDER BY i.ticker",
        [account_id],
    ).fetchall()
    pending = connection.execute(
        "SELECT o.ticker, e.trigger_code, o.target_trade_date FROM sim_order_intents o "
        "JOIN sim_exit_signals e ON e.intent_id = o.intent_id "
        "WHERE o.account_id = ? AND o.order_status = 'PENDING' ORDER BY o.target_trade_date, o.ticker",
        [account_id],
    ).fetchall()
    nav = connection.execute(
        "SELECT trade_date, cash_balance, securities_value, total_equity, unit_nav, max_drawdown FROM sim_nav_daily "
        "WHERE account_id = ? ORDER BY trade_date DESC LIMIT 60", [account_id]
    ).fetchall()
    recommendations = connection.execute(
        "SELECT r.target_trade_date, i.ticker, i.rank_order, i.rank_score, i.ref_close_unadj, "
        "COALESCE(m.execution_mode, 'PRODUCTION') "
        "FROM recommendation_runs r JOIN recommendation_items i ON i.run_id = r.run_id "
        "LEFT JOIN recommendation_run_modes m ON m.run_id = r.run_id "
        "WHERE r.run_status = 'FROZEN' ORDER BY r.target_trade_date DESC, i.rank_order LIMIT 20"
    ).fetchall()
    return {
        "account": {"id": account_id, "name": account[0], "initial_cash": _number(account[1])},
        "summary": _summary(connection, account_id, nav),
        "positions": [
            {
                "ticker": ticker, "shares": shares, "cost": _number(cost), "last_close": _number(close),
                "price_date": str(price_date) if price_date else None,
                "unrealized_return": _return(close, cost, shares),
            }
            for ticker, shares, cost, close, price_date in positions
        ],
        "pending_exits": [
            {"ticker": ticker, "trigger": trigger, "target_date": str(target_date)}
            for ticker, trigger, target_date in pending
        ],
        "nav": [
            {"date": str(day), "cash": _number(cash), "market_value": _number(value), "equity": _number(equity), "unit_nav": _number(unit_nav), "drawdown": _number(drawdown)}
            for day, cash, value, equity, unit_nav, drawdown in reversed(nav)
        ],
        "recommendations": [
            {"scope": "事件影子" if mode == "SHADOW" else "生产基线", "target_date": str(day), "ticker": ticker, "rank": rank, "score": _number(score), "reference_close": _number(close)}
            for day, ticker, rank, score, close, mode in recommendations
        ],
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


def load_activity(connection, account_id: str) -> dict:
    """Return immutable order, execution and rejection records for presentation."""
    orders = connection.execute(
        "SELECT target_trade_date, ticker, direction, target_shares, order_status, reject_reason_code "
        "FROM sim_order_intents WHERE account_id = ? ORDER BY created_at DESC LIMIT 100", [account_id]
    ).fetchall()
    executions = connection.execute(
        "SELECT trade_date, ticker, direction, deal_price_unadj, deal_shares, gross_amount "
        "FROM sim_executions WHERE account_id = ? ORDER BY created_at DESC LIMIT 100", [account_id]
    ).fetchall()
    rejects = connection.execute(
        "SELECT COALESCE(reject_reason_code, 'UNKNOWN'), COUNT(*) FROM sim_order_intents "
        "WHERE account_id = ? AND order_status = 'REJECTED' GROUP BY 1 ORDER BY 2 DESC", [account_id]
    ).fetchall()
    return {
        "orders": [dict(zip(("date", "ticker", "direction", "shares", "status", "reason"), map(_display, row))) for row in orders],
        "executions": [dict(zip(("date", "ticker", "direction", "price", "shares", "amount"), map(_display, row))) for row in executions],
        "reject_reasons": [{"reason": reason, "count": count} for reason, count in rejects],
    }


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
    }


def _return(close, cost, shares):
    if close is None or not shares or not cost:
        return None
    return _number((Decimal(str(close)) * Decimal(shares) / Decimal(str(cost))) - Decimal("1"))


def _number(value):
    return None if value is None else float(value)


def _display(value):
    return str(value) if hasattr(value, "isoformat") else _number(value) if isinstance(value, Decimal) else value
