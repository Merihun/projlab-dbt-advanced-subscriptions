"""Fernhill Box practice data: the billing system's and the app's exports, in two batches.

Everything comes from one seeded random generator and the standard library, so every run writes the same
bytes. The faults the project teaches are planted on purpose (see PROJECT.md, "What is wrong with the data"):

- webhook events delivered twice (same event_id, a later received_at), three of them across the batch line;
- events whose webhook arrived after the batch they happened in;
- app events up to three days late, some of them in the next batch;
- duplicate app events (the SDK retries);
- `invoice_lines.amount_cents` renamed `amount_minor` in batch 2, and `app_events.app_version` new in batch 2;
- a few invoice lines with a lower-case currency code;
- mid-period plan and quantity changes (proration lines), pauses, cancellations with refunds, credit notes;
- a price rise for the Classic monthly plan (new price ids, subscribers moved on 1 to 3 May 2026) and the
  Explorer plan renamed Explorer Plus, both in the plan catalogue of batch 2.

This module only generates. What a correct dbt project must produce is worked out by the checks, from the
same rows.
"""

from __future__ import annotations

import csv
import io
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path

SEED = 20250701
LAUNCH = datetime(2025, 7, 1)
END = datetime(2026, 7, 1)
BATCH_ENDS = {1: datetime(2026, 4, 1), 2: datetime(2026, 7, 1)}  # a batch holds what was received before this
BATCHES = (1, 2)
CATALOGUE_CREATED = datetime(2025, 6, 20, 9, 0)
NEW_PRICES_CREATED = datetime(2026, 4, 20, 10, 0)
RENAMED = datetime(2026, 4, 15, 8, 0)
PRICE_RISE = datetime(2026, 5, 1, 0, 0)

CURRENCIES = {  # code: (name, budget rate: GBP per unit, used for MRR)
    "GBP": ("Pound sterling", Decimal("1.0000")),
    "EUR": ("Euro", Decimal("0.8500")),
    "USD": ("US dollar", Decimal("0.7900")),
}
COUNTRIES = (("GB", "GBP", 60), ("IE", "EUR", 6), ("DE", "EUR", 8), ("FR", "EUR", 6), ("NL", "EUR", 5), ("US", "USD", 15))
PLAN_NAMES = {"mini": "Mini", "classic": "Classic", "explorer": "Explorer"}
UNIT_AMOUNTS = {  # (plan, interval): {currency: minor units}; annual amounts divide by 12 exactly
    ("mini", "month"): {"GBP": 1499, "EUR": 1799, "USD": 1899},
    ("classic", "month"): {"GBP": 2499, "EUR": 2899, "USD": 2999},
    ("classic", "year"): {"GBP": 26988, "EUR": 31188, "USD": 32388},
    ("explorer", "month"): {"GBP": 3999, "EUR": 4599, "USD": 4799},
    ("explorer", "year"): {"GBP": 43188, "EUR": 49188, "USD": 51588},
}
NEW_CLASSIC_MONTHLY = {"GBP": 2699, "EUR": 3099, "USD": 3199}
EVENT_TYPES = ("subscription_created", "subscription_updated", "subscription_paused", "subscription_resumed",
               "subscription_cancelled")  # fmt: skip
APP_EVENT_NAMES = (("box_viewed", 55), ("item_rated", 25), ("skip_requested", 8), ("review_written", 12))
APP_VERSIONS = ("5.2.0", "5.2.1", "5.3.0")
CENT = Decimal("0.01")

TABLES = {  # raw table: CSV columns of batch 1 and batch 2 (the loader adds _loaded_at)
    "customers": (("customer_id", "email", "country_code", "currency", "signed_up_at", "updated_at"),) * 2,
    "plan_prices": (("price_id", "plan_id", "plan_name", "billing_interval", "currency", "unit_amount", "active", "updated_at"),) * 2,
    "subscriptions": (("subscription_id", "customer_id", "price_id", "quantity", "status", "created_at", "updated_at"),) * 2,
    "subscription_events": (("event_id", "event_type", "subscription_id", "customer_id", "price_id", "quantity", "status",
                             "occurred_at", "received_at"),) * 2,
    "invoice_lines": (
        ("line_id", "invoice_id", "subscription_id", "customer_id", "line_type", "currency", "amount_cents", "period_start", "period_end", "issued_at"),
        ("line_id", "invoice_id", "subscription_id", "customer_id", "line_type", "currency", "amount_minor", "period_start", "period_end", "issued_at"),
    ),
    "app_events": (
        ("event_id", "customer_id", "event_name", "occurred_at", "received_at"),
        ("event_id", "customer_id", "event_name", "occurred_at", "received_at", "app_version"),
    ),
}  # fmt: skip
REPLACED = ("customers", "plan_prices", "subscriptions")  # a full export each batch; the others are appended


# ---------------------------------------------------------------------------------------------------- rows


@dataclass(frozen=True)
class Price:
    price_id: str
    plan_id: str
    interval: str
    currency: str
    unit_amount: int


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str
    subscription_id: str
    customer_id: str
    price_id: str
    quantity: int
    status: str
    occurred_at: datetime
    received_at: datetime


@dataclass(frozen=True)
class Line:
    line_id: str
    invoice_id: str
    subscription_id: str
    customer_id: str
    line_type: str
    currency: str  # as delivered: a few are lower case
    amount: int  # minor units
    period_start: date
    period_end: date
    issued_at: datetime


@dataclass(frozen=True)
class AppEvent:
    event_id: str
    customer_id: str
    event_name: str
    occurred_at: datetime
    received_at: datetime
    app_version: str | None


def price_id(plan: str, interval: str, currency: str, new: bool = False) -> str:
    return f"price_{plan}_{interval}_{currency.lower()}" + ("_2026" if new else "")


@cache
def prices() -> dict[str, Price]:
    out = {}
    for (plan, interval), amounts in UNIT_AMOUNTS.items():
        for currency, amount in amounts.items():
            out[price_id(plan, interval, currency)] = Price(price_id(plan, interval, currency), plan, interval, currency, amount)
    for currency, amount in NEW_CLASSIC_MONTHLY.items():
        pid = price_id("classic", "month", currency, new=True)
        out[pid] = Price(pid, "classic", "month", currency, amount)
    return out


def plan_price_rows(batch: int) -> list[dict]:
    """The plan catalogue as exported with a batch: batch 2 has the rename, the new prices and the old ones archived."""
    rows = []
    for pid, p in prices().items():
        new = pid.endswith("_2026")
        if new and batch == 1:
            continue
        name, active, updated = PLAN_NAMES[p.plan_id], True, CATALOGUE_CREATED
        if new:
            updated = NEW_PRICES_CREATED
        if batch == 2 and p.plan_id == "explorer":
            name, updated = "Explorer Plus", RENAMED
        if batch == 2 and p.plan_id == "classic" and p.interval == "month" and not new:
            active, updated = False, PRICE_RISE
        rows.append({"price_id": pid, "plan_id": p.plan_id, "plan_name": name, "billing_interval": p.interval,
                     "currency": p.currency, "unit_amount": p.unit_amount, "active": active, "updated_at": updated})  # fmt: skip
    return rows


def _months_after(start: date, months: int) -> date:
    """The same day `months` later, or the month's last day when it has fewer days."""
    year, month = divmod(start.month - 1 + months, 12)
    year, month = start.year + year, month + 1
    return date(year, month, min(start.day, days_in_month(date(year, month, 1))))


def days_in_month(d: date) -> int:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return (nxt - date(d.year, d.month, 1)).days


def month_start(d: date | datetime) -> date:
    return date(d.year, d.month, 1)


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def _weighted(rng: random.Random, options):
    total = sum(w for _, w in options)
    r = rng.uniform(0, total)
    for value, weight in options:
        r -= weight
        if r <= 0:
            return value
    return options[-1][0]


def _at(rng: random.Random, day_start: datetime, hours: float = 24) -> datetime:
    return (day_start + timedelta(seconds=rng.randrange(int(hours * 3600)))).replace(microsecond=0)


@dataclass
class _World:
    customers: list[dict]
    events: list[Event]  # every delivery, duplicates included, sorted by received_at
    lines: list[Line]
    app_events: list[AppEvent]


@cache
def world() -> _World:
    rng = random.Random(SEED)
    customers: list[dict] = []
    timelines: list[list[tuple]] = []  # per subscription: (time, type, price_id, quantity, status)
    sub_owner: list[str] = []
    month = month_start(LAUNCH)
    number = 0
    while month < month_start(END):
        index = (month.year - 2025) * 12 + month.month - 7
        for _ in range(70 + 9 * index + rng.randrange(15)):
            number += 1
            country, currency = _weighted(rng, [((c, cur), w) for c, cur, w in COUNTRIES])
            signed_up = _at(rng, datetime.combine(month, datetime.min.time()), 24 * days_in_month(month))
            email = f"member{number:05d}@fernhill-box.example"
            if rng.random() < 0.05:
                email = (" " + email.upper()) if rng.random() < 0.5 else email.capitalize() + " "
            customer_id = f"cus_{number:05d}"
            customers.append({"customer_id": customer_id, "email": email, "country_code": country, "currency": currency,
                              "signed_up_at": signed_up, "updated_at": signed_up})  # fmt: skip
            start = signed_up + timedelta(minutes=rng.randrange(5, 60 * 72))
            while start is not None and start < END - timedelta(days=1):
                plan = _weighted(rng, [("mini", 30), ("classic", 50), ("explorer", 20)])
                interval = "year" if plan != "mini" and rng.random() < 0.2 else "month"
                quantity = _weighted(rng, [(1, 90), (2, 8), (3, 2)])
                timeline, cancelled_at = _lifecycle(rng, start, plan, interval, currency, quantity)
                timelines.append(timeline)
                sub_owner.append(customer_id)
                start = None
                if cancelled_at is not None and rng.random() < 0.2:
                    start = cancelled_at + timedelta(days=rng.uniform(25, 120))
            if rng.random() < 0.05:  # a gift box: a second subscription next to the first
                gift = signed_up + timedelta(days=rng.uniform(20, 200))
                if gift < END - timedelta(days=1):
                    timeline, _ = _lifecycle(rng, gift, "mini", "month", currency, 1)
                    timelines.append(timeline)
                    sub_owner.append(customer_id)
        month = next_month(month)

    # Subscription ids in order of creation; event ids in order of occurrence.
    order = sorted(range(len(timelines)), key=lambda i: timelines[i][0][0])
    raw = []
    for n, i in enumerate(order, start=1):
        for t, kind, pid, qty, status in timelines[i]:
            raw.append((t, kind, f"sub_{n:05d}", sub_owner[i], pid, qty, status))
    raw.sort(key=lambda e: (e[0], e[2]))
    events = []
    for n, (t, kind, sid, cid, pid, qty, status) in enumerate(raw, start=1):
        received = t + timedelta(seconds=rng.randrange(1, 240))
        events.append(Event(f"evt_{n:06d}", kind, sid, cid, pid, qty, status, t, received))
    # The last event of March arrives in April; three events late on 31 March are delivered again in April.
    edge = [e for e in events if e.occurred_at < BATCH_ENDS[1]][-4:]
    last = edge[-1]
    events[events.index(last)] = Event(**{**last.__dict__, "received_at": BATCH_ENDS[1] + timedelta(seconds=37)})
    copies = [Event(**{**e.__dict__, "received_at": BATCH_ENDS[1] + timedelta(seconds=60 * (k + 2))}) for k, e in enumerate(edge[:3])]
    for e in events:
        if rng.random() < 0.03:
            copies.append(Event(**{**e.__dict__, "received_at": e.received_at + timedelta(seconds=rng.randrange(30, 3600))}))
    deliveries = sorted((e for e in events + copies if e.received_at < END), key=lambda e: (e.received_at, e.event_id))

    by_sub: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        if e.received_at < END:
            by_sub[e.subscription_id].append(e)
    lines = _invoice_lines(rng, by_sub, {c["customer_id"]: c["currency"] for c in customers})
    app_events = _app_events(rng, by_sub)
    return _World(customers, deliveries, lines, app_events)


def _lifecycle(rng, start, plan, interval, currency, quantity):
    """One subscription's changes: (time, event type, price_id, quantity, status after), and when it was cancelled."""

    def current_price(at: datetime, plan_: str) -> str:
        new = plan_ == "classic" and interval == "month" and at >= PRICE_RISE
        return price_id(plan_, interval, currency, new=new)

    pid = current_price(start, plan)
    out = [(start.replace(microsecond=0), "subscription_created", pid, quantity, "active")]
    status, t = "active", start
    while True:
        nxt = t + timedelta(days=rng.uniform(18, 42))
        old_classic = pid == price_id("classic", "month", currency)
        if old_classic and status != "cancelled" and t < PRICE_RISE <= nxt:
            moved = PRICE_RISE + timedelta(seconds=rng.randrange(72 * 3600))
            if moved < END:
                pid = price_id("classic", "month", currency, new=True)
                out.append((moved, "subscription_updated", pid, quantity, status))
                nxt = max(nxt, moved + timedelta(days=2))
        t = nxt.replace(microsecond=0)
        if t >= END:
            return out, None
        r = rng.random()
        if status == "paused":
            if r < 0.75:
                status = "active"
                out.append((t, "subscription_resumed", pid, quantity, status))
            elif r < 0.9:
                out.append((t, "subscription_cancelled", pid, quantity, "cancelled"))
                return out, t
            continue
        p_cancel = 0.012 if interval == "year" else 0.045
        p_pause = 0.0 if interval == "year" else 0.02
        if r < p_cancel:
            out.append((t, "subscription_cancelled", pid, quantity, "cancelled"))
            return out, t
        if r < p_cancel + p_pause:
            status = "paused"
            out.append((t, "subscription_paused", pid, quantity, status))
        elif r < p_cancel + p_pause + 0.03:
            options = [p for p in PLAN_NAMES if p != prices()[pid].plan_id and (p, interval) in UNIT_AMOUNTS]
            pid = current_price(t, rng.choice(options))
            out.append((t, "subscription_updated", pid, quantity, status))
        elif r < p_cancel + p_pause + 0.045:
            quantity = max(1, quantity + rng.choice((-1, 1, 1)))
            out.append((t, "subscription_updated", pid, quantity, status))


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), ROUND_HALF_UP))


def _invoice_lines(rng, by_sub, currency_of) -> list[Line]:
    drafts = []  # (issued_at, subscription, customer, type, currency, amount, start, end)

    def add(issued, e, kind, amount, start, end):
        if issued < END and amount:
            drafts.append((issued, e.subscription_id, e.customer_id, kind, prices()[e.price_id].currency, amount, start, end))

    for sid in sorted(by_sub):
        evs = by_sub[sid]
        status = None
        amount = anchor = start = end = None
        n = 0
        last = None
        for e in evs + [None]:
            until = e.occurred_at if e else END
            while status == "active" and datetime.combine(end, datetime.min.time()) + timedelta(hours=6) <= until:
                n += 1
                start, end = end, _months_after(anchor, n * (12 if prices()[last.price_id].interval == "year" else 1))
                add(datetime.combine(start, datetime.min.time()) + timedelta(hours=6), last, "subscription", amount, start, end)
            if e is None:
                break
            d = e.occurred_at.date()
            new_amount = prices()[e.price_id].unit_amount * e.quantity
            months = 12 if prices()[e.price_id].interval == "year" else 1
            if e.event_type in ("subscription_created", "subscription_resumed"):
                anchor, start, n = d, d, 1
                end = _months_after(anchor, months)
                add(e.occurred_at + timedelta(seconds=60), e, "subscription", new_amount, start, end)
            elif e.event_type == "subscription_updated" and status == "active" and end > d:
                remaining, total = (end - d).days, (end - start).days
                add(e.occurred_at + timedelta(seconds=60), last, "proration", -_round(Decimal(amount * remaining) / total), d, end)
                add(e.occurred_at + timedelta(seconds=61), e, "proration", _round(Decimal(new_amount * remaining) / total), d, end)
            elif e.event_type == "subscription_cancelled" and status == "active" and end > d and rng.random() < 0.15:
                remaining, total = (end - d).days, (end - start).days
                add(e.occurred_at + timedelta(hours=2), e, "refund", -_round(Decimal(amount * remaining) / total), d, end)
            amount, status, last = new_amount, e.status, e
    for draft in list(drafts):
        if draft[3] == "subscription" and rng.random() < 0.02:
            issued, sid, cid, _, currency, _, start, end = draft
            if issued + timedelta(days=7) < END:
                drafts.append((issued + timedelta(days=7), sid, cid, "credit_note", currency, -500, start, end))
    drafts.sort(key=lambda d: (d[0], d[1], d[3]))
    lines, invoice, previous = [], 0, None
    for n, (issued, sid, cid, kind, currency, amount, start, end) in enumerate(drafts, start=1):
        if (issued.replace(second=0), sid) != previous:
            invoice += 1
            previous = (issued.replace(second=0), sid)
        delivered = currency.lower() if rng.random() < 0.004 else currency
        lines.append(Line(f"il_{n:06d}", f"in_{invoice:06d}", sid, cid, kind, delivered, amount, start, end, issued))
    return lines


def _app_events(rng, by_sub) -> list[AppEvent]:
    active: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for evs in by_sub.values():
        for e, nxt in zip(evs, evs[1:] + [None]):
            if e.status == "active":
                active[e.customer_id].append((e.occurred_at, nxt.occurred_at if nxt else END))
    drafts = []
    for cid in sorted(active):
        days = sorted({(s.date() + timedelta(days=k)) for s, f in active[cid] for k in range((f.date() - s.date()).days + 1)})
        for day in days:
            if rng.random() >= 0.07:
                continue
            for _ in range(rng.choice((1, 1, 2, 3))):
                t = _at(rng, datetime.combine(day, datetime.min.time()))
                if not any(s <= t < f for s, f in active[cid]):
                    continue
                delay = timedelta(seconds=rng.randrange(1, 600))
                if rng.random() < 0.03:
                    delay = timedelta(seconds=rng.randrange(3600, 70 * 3600))
                drafts.append((t, cid, _weighted(rng, APP_EVENT_NAMES), t + delay))
    drafts.sort()
    out = []
    for n, (t, cid, name, received) in enumerate(drafts, start=1):
        version = rng.choice(APP_VERSIONS) if received >= BATCH_ENDS[1] else None
        out.append(AppEvent(f"app_{n:07d}", cid, name, t, received, version))
        if rng.random() < 0.01:
            out.append(AppEvent(f"app_{n:07d}", cid, name, t, received + timedelta(seconds=rng.randrange(1, 30)), version))
    return [e for e in out if e.received_at < END]


# ---------------------------------------------------------------------------------------------------- batches


def batch_of(received: datetime) -> int:
    return 1 if received < BATCH_ENDS[1] else 2


def batch_rows(table: str, batch: int) -> list[dict]:
    """The rows of one raw table as delivered in a batch (a full export for customers, plan_prices and
    subscriptions; only the new rows for the appended tables)."""
    w = world()
    end = BATCH_ENDS[batch]
    if table == "customers":
        return [dict(c) for c in w.customers if c["signed_up_at"] < end]
    if table == "plan_prices":
        return plan_price_rows(batch)
    if table == "subscriptions":
        return subscription_states(batch)
    if table == "subscription_events":
        return [{**e.__dict__} for e in w.events if batch_of(e.received_at) == batch]
    if table == "invoice_lines":
        amount = "amount_cents" if batch == 1 else "amount_minor"
        return [{"line_id": x.line_id, "invoice_id": x.invoice_id, "subscription_id": x.subscription_id, "customer_id": x.customer_id,
                 "line_type": x.line_type, "currency": x.currency, amount: x.amount, "period_start": x.period_start,
                 "period_end": x.period_end, "issued_at": x.issued_at}
                for x in w.lines if batch_of(x.issued_at) == batch]  # fmt: skip
    if table == "app_events":
        rows = [{**e.__dict__} for e in w.app_events if batch_of(e.received_at) == batch]
        if batch == 1:
            for r in rows:
                del r["app_version"]
        return rows
    raise KeyError(table)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def csv_text(table: str, batch: int) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    columns = TABLES[table][batch - 1]
    writer.writerow(columns)
    for row in batch_rows(table, batch):
        writer.writerow([_text(row.get(c)) for c in columns])
    return out.getvalue()


def fx_rate_rows() -> list[dict]:
    """Monthly average rates (GBP per unit), valid from the first of a month to the first of the next."""
    rng = random.Random(SEED + 1)
    rows = []
    level = {"EUR": 0.8540, "USD": 0.7820}
    month = date(2025, 6, 1)
    while month < date(2027, 1, 1):
        for currency in CURRENCIES:
            if currency == "GBP":
                rate = Decimal("1.000000")
            else:
                level[currency] *= 1 + rng.uniform(-0.012, 0.012)
                rate = Decimal(str(round(level[currency], 6)))
            rows.append({"currency_code": currency, "valid_from": month, "valid_to": next_month(month), "gbp_per_unit": rate})
        month = next_month(month)
    return rows


def seed_texts() -> dict[str, str]:
    currencies = io.StringIO()
    w = csv.writer(currencies, lineterminator="\n")
    w.writerow(("currency_code", "currency_name", "budget_gbp_rate"))
    for code, (name, rate) in CURRENCIES.items():
        w.writerow((code, name, rate))
    rates = io.StringIO()
    w = csv.writer(rates, lineterminator="\n")
    w.writerow(("currency_code", "valid_from", "valid_to", "gbp_per_unit"))
    for r in fx_rate_rows():
        w.writerow((r["currency_code"], r["valid_from"], r["valid_to"], r["gbp_per_unit"]))
    return {"currencies.csv": currencies.getvalue(), "fx_rates.csv": rates.getvalue()}


def generate(root: str | Path = ".") -> dict[str, int]:
    """Write data/batch_1/*.csv, data/batch_2/*.csv and the two seeds; returns rows per file."""
    root = Path(root)
    counts = {}
    for batch in BATCHES:
        folder = root / "data" / f"batch_{batch}"
        folder.mkdir(parents=True, exist_ok=True)
        for table in TABLES:
            text = csv_text(table, batch)
            (folder / f"{table}.csv").write_text(text, encoding="utf-8")
            counts[f"batch_{batch}/{table}.csv"] = text.count("\n") - 1
    (root / "seeds").mkdir(exist_ok=True)
    for name, text in seed_texts().items():
        (root / "seeds" / name).write_text(text, encoding="utf-8")
        counts[f"seeds/{name}"] = text.count("\n") - 1
    return counts

def events(through: int) -> list[Event]:
    """Subscription events received by the end of a batch, one per event_id (the first delivery), in the order
    they happened."""
    first: dict[str, Event] = {}
    for e in world().events:
        if e.received_at < BATCH_ENDS[through] and (e.event_id not in first or e.received_at < first[e.event_id].received_at):
            first[e.event_id] = e
    return sorted(first.values(), key=lambda e: (e.subscription_id, e.occurred_at))

def subscription_states(batch: int) -> list[dict]:
    """The billing system's subscriptions export: each subscription's state after its events of the batch."""
    out = {}
    for e in events(batch):
        created = out.get(e.subscription_id, {}).get("created_at", e.occurred_at)
        out[e.subscription_id] = {"subscription_id": e.subscription_id, "customer_id": e.customer_id, "price_id": e.price_id,
                                  "quantity": e.quantity, "status": e.status, "created_at": created, "updated_at": e.occurred_at}  # fmt: skip
    return [out[k] for k in sorted(out)]
