"""Auditable financing-debt state for margin simulation accounts."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable
from uuid import uuid4

from .lots import money


@dataclass(frozen=True)
class OpeningMarginDebt:
    ticker: str
    opening_amount: Decimal
    outstanding_balance: Decimal

    def __post_init__(self) -> None:
        if not self.ticker or self.opening_amount < 0 or self.outstanding_balance < 0:
            raise ValueError("margin debt requires ticker and non-negative balances")


def create_margin_account(connection, account_id: str, annual_financing_rate: Decimal, debts: Iterable[OpeningMarginDebt],
                          as_of_date: date) -> None:
    if annual_financing_rate < 0:
        raise ValueError("annual financing rate cannot be negative")
    debts = tuple(debts)
    if len({debt.ticker for debt in debts}) != len(debts):
        raise ValueError("margin debts have duplicate tickers")
    now = datetime.now(timezone.utc)
    connection.execute("INSERT INTO sim_margin_accounts VALUES (?, ?, ?)", [account_id, annual_financing_rate, now])
    connection.executemany("INSERT INTO sim_margin_debts VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)", [
        (str(uuid4()), account_id, debt.ticker, debt.opening_amount, debt.outstanding_balance, as_of_date, now)
        for debt in debts
    ])


def accrue_financing_interest(connection, account_id: str, through_date: date) -> Decimal:
    """Accrue simple daily interest for every calendar day after the last accrual."""
    account = connection.execute("SELECT annual_financing_rate FROM sim_margin_accounts WHERE account_id = ?", [account_id]).fetchone()
    if account is None:
        return Decimal("0")
    rate, total = Decimal(str(account[0])), Decimal("0")
    debts = connection.execute(
        "SELECT debt_id, outstanding_balance, last_interest_accrual_date FROM sim_margin_debts "
        "WHERE account_id = ? AND status = 'OPEN'", [account_id]
    ).fetchall()
    for debt_id, balance, last_date in debts:
        balance = Decimal(str(balance))
        day = last_date + timedelta(days=1)
        while day <= through_date:
            interest = money(balance * rate / Decimal("365"))
            balance = money(balance + interest)
            connection.execute("INSERT INTO sim_margin_interest_events VALUES (?, ?, ?, ?, ?, ?, ?)", [
                str(uuid4()), debt_id, day, rate, interest, balance, datetime.now(timezone.utc),
            ])
            total += interest
            day += timedelta(days=1)
        connection.execute("UPDATE sim_margin_debts SET outstanding_balance = ?, last_interest_accrual_date = ? WHERE debt_id = ?",
                           [balance, through_date if through_date > last_date else last_date, debt_id])
    return money(total)


def margin_debt_balance(connection, account_id: str) -> Decimal:
    row = connection.execute("SELECT COALESCE(SUM(outstanding_balance), 0) FROM sim_margin_debts WHERE account_id = ? AND status = 'OPEN'", [account_id]).fetchone()
    return Decimal(str(row[0]))
