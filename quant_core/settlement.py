"""Database shell for settlement. Matching itself stays in quant_core.matching."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, Iterable, List
from uuid import uuid4

from .ledger import buy_entries, initial_funding, journal_rows, sell_entries
from .lots import fifo_allocate, money, remaining_shares
from .matching import match_next_open
from .models import DayBar, Disposal, FeeModel, Lot, OrderIntent


class SettlementService:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN TRANSACTION")
        try:
            yield
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def create_account(self, account_id: str, name: str, initial_cash: Decimal, trade_date: date) -> None:
        now = datetime.now(timezone.utc)
        with self._transaction():
            self.connection.execute(
                "INSERT INTO sim_accounts VALUES (?, ?, 'CNY', ?, 'cash_rule_cn_t0_reinvest_v1', 'cogs_fifo_v1', 'ACTIVE', ?)",
                [account_id, name, initial_cash, now],
            )
            self._insert_journal(f"J_INIT_{account_id}", account_id, trade_date, initial_funding(initial_cash), now)

    def create_intent(self, intent: OrderIntent) -> None:
        self.connection.execute(
            "INSERT INTO sim_order_intents VALUES (?, ?, ?, ?, ?, ?, ?, 'NEXT_OPEN_WITH_SLIPPAGE', 'PENDING', NULL, ?)",
            [intent.intent_id, intent.recommendation_item_id, intent.account_id, intent.ticker,
             intent.target_trade_date, intent.direction, intent.shares, datetime.now(timezone.utc)],
        )

    def settle(self, intent: OrderIntent, bar: DayBar, next_trading_day: date, fee: FeeModel) -> str:
        self._validate_pending_intent(intent)
        result = match_next_open(intent, bar, fee)
        if not result.accepted:
            self._reject(intent.intent_id, result.reason)
            return "REJECTED"
        if intent.direction == "BUY":
            return self._settle_buy(intent, result, next_trading_day, fee.version)
        return self._settle_sell(intent, result, fee.version)

    def value_day(self, account_id: str, trade_date: date, closing_prices: Dict[str, Decimal]) -> None:
        lots, disposals = self._load_inventory(account_id)
        by_ticker: Dict[str, List[Lot]] = {}
        for lot in lots:
            by_ticker.setdefault(lot.ticker, []).append(lot)
        cash = self._cash(account_id)
        securities_value = Decimal("0")
        with self._transaction():
            for ticker, ticker_lots in by_ticker.items():
                remaining_by_lot = {
                    lot.lot_id: remaining_shares(lot, disposals) for lot in ticker_lots
                }
                remaining = sum(remaining_by_lot.values())
                if remaining <= 0:
                    continue
                price = closing_prices[ticker]
                available = sum(remaining_by_lot[lot.lot_id] for lot in ticker_lots
                                if lot.available_from_date <= trade_date)
                cost = money(sum(Decimal(remaining_by_lot[lot.lot_id]) * lot.unit_cost
                                 for lot in ticker_lots))
                market_value = money(Decimal(remaining) * price)
                securities_value += market_value
                self.connection.execute(
                    "INSERT OR REPLACE INTO sim_positions_daily VALUES (?, ?, ?, ?, ?, ?)",
                    [account_id, trade_date, ticker, remaining, available, cost],
                )
            total = money(cash + securities_value)
            initial = self.connection.execute("SELECT initial_cash FROM sim_accounts WHERE account_id = ?", [account_id]).fetchone()[0]
            previous = self.connection.execute("SELECT MAX(total_equity) FROM sim_nav_daily WHERE account_id = ?", [account_id]).fetchone()[0]
            high_water = max(Decimal(str(previous)) if previous is not None else total, total)
            drawdown = Decimal("0") if high_water == 0 else (high_water - total) / high_water
            self.connection.execute(
                "INSERT OR REPLACE INTO sim_nav_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
                [account_id, trade_date, cash, securities_value, total, total / Decimal(str(initial)), drawdown],
            )

    def _settle_buy(self, intent, result, next_trading_day, cost_version) -> str:
        required = result.gross_amount + result.commission + result.transfer_fee
        if self._cash(intent.account_id) < required:
            self._reject(intent.intent_id, "INSUFFICIENT_CASH")
            return "REJECTED"
        event_id, now = str(uuid4()), datetime.now(timezone.utc)
        with self._transaction():
            self._insert_execution(event_id, intent, result, cost_version, now)
            self.connection.execute(
                "INSERT INTO sim_position_lots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [str(uuid4()), intent.account_id, intent.ticker, event_id, intent.target_trade_date,
                 next_trading_day, intent.shares, result.price, now],
            )
            self._insert_journal(f"J_BUY_{event_id}", intent.account_id, intent.target_trade_date,
                                 buy_entries(result.gross_amount, result.commission, result.transfer_fee, intent.ticker), now, event_id)
            self._fill(intent.intent_id)
        return "FILLED"

    def _settle_sell(self, intent, result, cost_version) -> str:
        lots, disposals = self._load_inventory(intent.account_id, intent.ticker)
        try:
            allocations = fifo_allocate(lots, disposals, intent.target_trade_date, intent.shares)
        except ValueError:
            self._reject(intent.intent_id, "T1_LOCKED")
            return "REJECTED"
        event_id, now = str(uuid4()), datetime.now(timezone.utc)
        cost = money(sum(item.total_cost for item in allocations))
        with self._transaction():
            self._insert_execution(event_id, intent, result, cost_version, now)
            for item in allocations:
                self.connection.execute(
                    "INSERT INTO sim_lot_disposal_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [str(uuid4()), event_id, item.lot_id, intent.target_trade_date, item.shares,
                     item.unit_cost, item.total_cost, now],
                )
            self._insert_journal(f"J_SELL_{event_id}", intent.account_id, intent.target_trade_date,
                                 sell_entries(result.gross_amount, cost, result.commission, result.stamp_duty,
                                              result.transfer_fee, intent.ticker), now, event_id)
            self._fill(intent.intent_id)
        return "FILLED"

    def _validate_pending_intent(self, intent: OrderIntent) -> None:
        row = self.connection.execute(
            "SELECT account_id, ticker, target_trade_date, direction, target_shares, order_status FROM sim_order_intents WHERE intent_id = ?",
            [intent.intent_id],
        ).fetchone()
        if row != (intent.account_id, intent.ticker, intent.target_trade_date, intent.direction, intent.shares, "PENDING"):
            raise ValueError("intent does not match a pending persisted order")

    def _reject(self, intent_id: str, reason: str) -> None:
        self.connection.execute("UPDATE sim_order_intents SET order_status = 'REJECTED', reject_reason_code = ? WHERE intent_id = ?", [reason, intent_id])

    def _fill(self, intent_id: str) -> None:
        self.connection.execute("UPDATE sim_order_intents SET order_status = 'FILLED', reject_reason_code = NULL WHERE intent_id = ?", [intent_id])

    def _cash(self, account_id: str) -> Decimal:
        row = self.connection.execute("SELECT COALESCE(SUM(debit_amount - credit_amount), 0) FROM ledger_journal_entries WHERE account_id = ? AND account_code = '1001'", [account_id]).fetchone()[0]
        return Decimal(str(row))

    def _load_inventory(self, account_id: str, ticker: str = None):
        where, params = "WHERE account_id = ?", [account_id]
        if ticker:
            where += " AND ticker = ?"
            params.append(ticker)
        rows = self.connection.execute(f"SELECT lot_id, account_id, ticker, buy_trade_date, available_from_date, orig_shares, unadj_unit_cost FROM sim_position_lots {where}", params).fetchall()
        lots = [Lot(*row) for row in rows]
        disposal_rows = self.connection.execute(
            "SELECT lot_id, trade_date, shares_deducted FROM sim_lot_disposal_events WHERE lot_id IN (SELECT lot_id FROM sim_position_lots " + where + ")", params
        ).fetchall()
        return lots, [Disposal(*row) for row in disposal_rows]

    def _insert_execution(self, event_id, intent, result, cost_version, now) -> None:
        self.connection.execute(
            "INSERT INTO sim_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [event_id, intent.intent_id, intent.account_id, intent.target_trade_date, intent.ticker, intent.direction,
             result.price, intent.shares, result.gross_amount, result.commission, result.stamp_duty,
             result.transfer_fee, result.price_cap_applied, cost_version, now],
        )

    def _insert_journal(self, journal_id, account_id, trade_date, entries, now, event_id=None) -> None:
        self.connection.executemany("INSERT INTO ledger_journal_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    journal_rows(journal_id, account_id, trade_date, entries, now, event_id))
