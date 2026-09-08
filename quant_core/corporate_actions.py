"""Immutable corporate-action events for pretax dividends and stock adjustments."""

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import json
from uuid import uuid4

from .ledger import dividend_entries, fractional_settlement_entries
from .lots import money, remaining_shares
from .models import Disposal, Lot
from .settlement import SettlementService


PRETAX_VERSION = "dividend_tax_assumption_pretax_v1"


class CorporateActionService:
    def __init__(self, connection):
        self.connection = connection

    def register(self, ticker, action_type, record_date=None, ex_date=None, payment_date=None,
                 cash_per_share=Decimal("0"), share_ratio=Decimal("0"), fractional_cash_price=None, source=None):
        if action_type not in {"CASH_DIVIDEND", "STOCK_ADJUSTMENT"}:
            raise ValueError("unsupported corporate action")
        action_id, now = str(uuid4()), datetime.now(timezone.utc)
        self.connection.execute("INSERT INTO sim_corporate_action_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [action_id, ticker, action_type, record_date, ex_date, payment_date, cash_per_share, share_ratio,
             fractional_cash_price, PRETAX_VERSION, json.dumps(source or {}, sort_keys=True), now])
        return action_id

    def capture_dividend_entitlements(self, action_id, account_id):
        action = self.connection.execute("SELECT ticker, record_date, cash_per_share FROM sim_corporate_action_events WHERE action_id = ? AND action_type = 'CASH_DIVIDEND'", [action_id]).fetchone()
        if not action or action[1] is None:
            raise ValueError("cash dividend requires a record date")
        ticker, record_date, per_share = action
        shares = self._remaining(account_id, ticker, record_date)
        if shares <= 0:
            return None
        gross, now = money(Decimal(shares) * Decimal(str(per_share))), datetime.now(timezone.utc)
        self.connection.execute("INSERT INTO sim_dividend_entitlements VALUES (?, ?, ?, ?, ?, NULL, ?)",
                                [str(uuid4()), action_id, account_id, shares, gross, now])
        return gross

    def pay_dividends(self, action_id, account_id, payment_date):
        row = self.connection.execute("SELECT e.entitlement_id, e.gross_cash, a.ticker FROM sim_dividend_entitlements e JOIN sim_corporate_action_events a ON a.action_id=e.action_id WHERE e.action_id=? AND e.account_id=? AND e.paid_at IS NULL", [action_id, account_id]).fetchone()
        if row is None:
            return None
        entitlement_id, cash, ticker = row
        now = datetime.now(timezone.utc)
        SettlementService(self.connection)._insert_journal("J_DIV_" + entitlement_id, account_id, payment_date,
                                                             dividend_entries(Decimal(str(cash)), ticker), now, action_id)
        self.connection.execute("UPDATE sim_dividend_entitlements SET paid_at=? WHERE entitlement_id=?", [now, entitlement_id])
        return Decimal(str(cash))

    def apply_stock_adjustment(self, action_id, account_id):
        action = self.connection.execute("SELECT ticker, ex_date, share_ratio, fractional_cash_price FROM sim_corporate_action_events WHERE action_id=? AND action_type='STOCK_ADJUSTMENT'", [action_id]).fetchone()
        if not action or action[1] is None:
            raise ValueError("stock adjustment requires an ex date")
        ticker, ex_date, ratio, cash_price = action
        lots, disposals = self._lots(account_id, ticker)
        now = datetime.now(timezone.utc)
        for lot in lots:
            pre = remaining_shares(lot, disposals)
            if not pre:
                continue
            exact = Decimal(pre) * (Decimal("1") + Decimal(str(ratio)))
            post = int(exact.to_integral_value(rounding=ROUND_FLOOR))
            fractional = exact - post
            cost = Decimal(lot.unit_cost)
            post_cost = money((Decimal(pre) * cost) / Decimal(post)) if post else cost
            payout = money(fractional * Decimal(str(cash_price))) if cash_price is not None else Decimal("0")
            self.connection.execute("INSERT INTO sim_lot_adjustment_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [str(uuid4()), action_id, lot.lot_id, ex_date, pre, post, cost, post_cost, payout, now])
            if payout:
                relieved = money(fractional * cost)
                SettlementService(self.connection)._insert_journal("J_FRAC_" + action_id + lot.lot_id, account_id, ex_date,
                    fractional_settlement_entries(payout, relieved, ticker), now, action_id)

    def _lots(self, account_id, ticker):
        service = SettlementService(self.connection)
        return service._load_inventory(account_id, ticker)

    def _remaining(self, account_id, ticker, as_of):
        lots, disposals = self._lots(account_id, ticker)
        return sum(remaining_shares(lot, disposals) for lot in lots if lot.buy_trade_date <= as_of)
