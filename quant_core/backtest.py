"""Leak-free daily-bar replay using the same orders, matching, ledger and exit rules as paper trading."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping, Optional, Sequence, Set
from uuid import uuid4

from .daily_risk import create_exit_intents
from .features import build_features
from .models import DayBar, FeeModel, OrderIntent
from .risk import ExitRule
from .settlement import SettlementService
from .strategy_research import ScoreProvider, StrategySpec, baseline_strategy_spec, resolve_score_provider


@dataclass(frozen=True)
class BacktestConfig:
    account_id: str
    start_date: date
    end_date: date
    shares_per_order: int = 100
    lookback_days: int = 20
    top_n: int = 5
    volume_multiple: Decimal = Decimal("1.0")
    strategy_spec: Optional[StrategySpec] = None


def replay_daily_strategy(connection, bars: Iterable[DayBar], trading_days: Sequence[date],
                          config: BacktestConfig, fee: FeeModel, exit_rule: ExitRule,
                          allowed_tickers: Optional[Set[str]] = None,
                          universe_by_date: Optional[Mapping[date, Set[str]]] = None,
                          score_provider: Optional[ScoreProvider] = None) -> dict:
    """Replay decisions at each close and execute them at the following open.

    `bars` must already contain provider-supplied limits. This function intentionally
    refuses to manufacture missing bounds; matching records those orders as rejected.
    """
    all_bars = tuple(bars)
    calendar = tuple(day for day in sorted(set(trading_days)) if config.start_date <= day <= config.end_date)
    if len(calendar) < 2:
        raise ValueError("backtest range needs at least two trading days")
    if universe_by_date is not None:
        missing = [day.isoformat() for day in calendar[:-1] if day not in universe_by_date]
        if missing:
            raise ValueError(f"point-in-time universe snapshot is missing for: {', '.join(missing[:5])}")
    by_day = {day: {bar.ticker: bar for bar in all_bars if bar.trade_date == day} for day in calendar}
    service = SettlementService(connection)
    spec = config.strategy_spec or baseline_strategy_spec(config.volume_multiple, config.top_n)
    provider = score_provider or resolve_score_provider(spec)
    if provider.spec != spec:
        raise ValueError("score provider spec does not match backtest config")
    buys_submitted = 0
    exits_signalled = 0
    for index, trade_date in enumerate(calendar):
        next_day = calendar[index + 1] if index + 1 < len(calendar) else None
        _settle_pending(connection, service, config.account_id, by_day[trade_date], trade_date, next_day, fee)
        prices = {ticker: bar.close for ticker, bar in by_day[trade_date].items()}
        service.value_day(config.account_id, trade_date, prices)
        if next_day is None:
            continue
        exits_signalled += len(create_exit_intents(
            connection, config.account_id, trade_date, next_day, all_bars, exit_rule
        ))
        features = build_features(all_bars, trade_date, config.lookback_days)
        point_in_time_universe = universe_by_date[trade_date] if universe_by_date is not None else allowed_tickers
        if point_in_time_universe is not None:
            features = [row for row in features if row.ticker in point_in_time_universe]
        blocked = _blocked_tickers(connection, config.account_id)
        scored = provider.score(features, trade_date)
        if scored.spec != spec or scored.as_of_trade_date != trade_date:
            raise ValueError("score provider returned an invalid strategy result")
        recommendations = [row for row in scored.recommendations if row.ticker not in blocked]
        for recommendation in recommendations:
            service.create_intent(OrderIntent(
                str(uuid4()), config.account_id, recommendation.ticker, next_day, "BUY", config.shares_per_order
            ))
            buys_submitted += 1
    orders = connection.execute(
        "SELECT direction, order_status, COUNT(*) FROM sim_order_intents WHERE account_id = ? GROUP BY 1, 2",
        [config.account_id],
    ).fetchall()
    return {
        "orders": {f"{direction}_{status}": count for direction, status, count in orders},
        "buy_orders_submitted": buys_submitted,
        "exit_signals": exits_signalled,
    }


def _settle_pending(connection, service: SettlementService, account_id: str,
                    day_bars: Mapping[str, DayBar], trade_date: date, next_day: Optional[date],
                    fee: FeeModel) -> None:
    rows = connection.execute(
        "SELECT intent_id, ticker, direction, target_shares FROM sim_order_intents "
        "WHERE account_id = ? AND target_trade_date = ? AND order_status = 'PENDING' ORDER BY intent_id",
        [account_id, trade_date],
    ).fetchall()
    successor = next_day or trade_date
    for intent_id, ticker, direction, shares in rows:
        bar = day_bars.get(ticker)
        if bar is None:
            connection.execute(
                "UPDATE sim_order_intents SET order_status = 'REJECTED', reject_reason_code = 'DATA_MISSING_BAR' WHERE intent_id = ?",
                [intent_id],
            )
            continue
        service.settle(OrderIntent(intent_id, account_id, ticker, trade_date, direction, shares), bar, successor, fee)


def _blocked_tickers(connection, account_id: str) -> set[str]:
    held = connection.execute(
        "SELECT l.ticker FROM sim_position_lots l LEFT JOIN ("
        " SELECT lot_id, SUM(shares_deducted) AS disposed FROM sim_lot_disposal_events GROUP BY lot_id"
        ") d ON d.lot_id = l.lot_id WHERE l.account_id = ? "
        "GROUP BY l.ticker HAVING SUM(l.orig_shares - COALESCE(d.disposed, 0)) > 0", [account_id]
    ).fetchall()
    pending = connection.execute(
        "SELECT ticker FROM sim_order_intents WHERE account_id = ? AND order_status = 'PENDING'", [account_id]
    ).fetchall()
    return {ticker for ticker, in held + pending}
