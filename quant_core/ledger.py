from datetime import date
from decimal import Decimal
from typing import Iterable, List
from uuid import uuid4

from .lots import money
from .models import JournalEntry

ZERO = Decimal("0.0000")


def validate_entries(entries: Iterable[JournalEntry]) -> List[JournalEntry]:
    checked = list(entries)
    if not checked:
        raise ValueError("journal requires entries")
    for entry in checked:
        debit, credit = money(entry.debit), money(entry.credit)
        if debit < ZERO or credit < ZERO or ((debit > ZERO) == (credit > ZERO)):
            raise ValueError("each journal entry needs exactly one positive side")
    if sum((money(e.debit) for e in checked), ZERO) != sum((money(e.credit) for e in checked), ZERO):
        raise ValueError("journal is not balanced")
    return checked


def initial_funding(amount: Decimal) -> List[JournalEntry]:
    amount = money(amount)
    if amount <= ZERO:
        raise ValueError("initial funding must be positive")
    return validate_entries([
        JournalEntry("1001", amount, ZERO, "initial capital funding"),
        JournalEntry("3001", ZERO, amount, "paid-in capital"),
    ])


def buy_entries(gross: Decimal, commission: Decimal, transfer_fee: Decimal, ticker: str) -> List[JournalEntry]:
    gross, commission, transfer_fee = map(money, (gross, commission, transfer_fee))
    total = gross + commission + transfer_fee
    entries = [
        JournalEntry("1101", gross, ZERO, "buy securities", ticker),
        JournalEntry("1001", ZERO, total, "cash paid for buy"),
    ]
    if commission > ZERO:
        entries.append(JournalEntry("6001", commission, ZERO, "buy commission"))
    if transfer_fee > ZERO:
        entries.append(JournalEntry("6003", transfer_fee, ZERO, "buy transfer fee"))
    return validate_entries(entries)


def sell_entries(gross: Decimal, cost_relieved: Decimal, commission: Decimal,
                 stamp_duty: Decimal, transfer_fee: Decimal, ticker: str) -> List[JournalEntry]:
    gross, cost_relieved, commission, stamp_duty, transfer_fee = map(
        money, (gross, cost_relieved, commission, stamp_duty, transfer_fee)
    )
    if gross <= ZERO or cost_relieved < ZERO:
        raise ValueError("gross must be positive and cost cannot be negative")
    net_cash = gross - commission - stamp_duty - transfer_fee
    if net_cash < ZERO:
        raise ValueError("fees cannot exceed gross sale proceeds")
    pnl = gross - cost_relieved
    entries = [
        JournalEntry("1001", net_cash, ZERO, "cash received from sell"),
        JournalEntry("1101", ZERO, cost_relieved, "relieve securities cost", ticker),
    ]
    if commission > ZERO:
        entries.append(JournalEntry("6001", commission, ZERO, "sell commission"))
    if stamp_duty > ZERO:
        entries.append(JournalEntry("6002", stamp_duty, ZERO, "sell stamp duty"))
    if transfer_fee > ZERO:
        entries.append(JournalEntry("6003", transfer_fee, ZERO, "sell transfer fee"))
    entries.append(JournalEntry("5001", ZERO, pnl, "realized gain", ticker) if pnl >= ZERO
                   else JournalEntry("5001", -pnl, ZERO, "realized loss", ticker))
    return validate_entries(entries)


def dividend_entries(gross_cash: Decimal, ticker: str) -> List[JournalEntry]:
    gross_cash = money(gross_cash)
    return validate_entries([
        JournalEntry("1001", gross_cash, ZERO, "pretax cash dividend", ticker),
        JournalEntry("5002", ZERO, gross_cash, "dividend income", ticker),
    ])


def fractional_settlement_entries(cash: Decimal, cost: Decimal, ticker: str) -> List[JournalEntry]:
    cash, cost = money(cash), money(cost)
    pnl = cash - cost
    entries = [JournalEntry("1001", cash, ZERO, "fractional share cash settlement", ticker),
               JournalEntry("1101", ZERO, cost, "relieve fractional share cost", ticker)]
    entries.append(JournalEntry("5001", ZERO, pnl, "fractional share gain", ticker) if pnl >= ZERO
                   else JournalEntry("5001", -pnl, ZERO, "fractional share loss", ticker))
    return validate_entries(entries)


def journal_rows(journal_id: str, account_id: str, trade_date: date,
                 entries: Iterable[JournalEntry], now, event_id=None) -> list:
    return [
        (str(uuid4()), journal_id, account_id, trade_date, event_id, entry.account_code,
         entry.ticker, money(entry.debit), money(entry.credit), entry.memo, now)
        for entry in validate_entries(entries)
    ]
