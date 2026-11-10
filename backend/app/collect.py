"""SayLedger Collect — PayPal payment requests, webhook intake and the payment inbox.

Flow: shopkeeper asks for a payment link -> PayPal invoice for the amount owed (Invoicing v2) ->
customer pays on PayPal -> webhook (verified) or a manual "sync" -> we RE-FETCH the invoice from
PayPal (never trust the payload amount) -> the new money since last time becomes an inbox
proposal -> the shopkeeper confirms -> ledger entry (source 'paypal'). Nothing touches the ledger
without that confirm tap.

Tables live in each book's SQLite file. A small registry (data/collect.db) maps PayPal invoice ids
to books and stores every webhook event once (idempotency).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .ledger import Ledger, LedgerError
from .paypal_client import PayPalError, payer_link, to_minor

SUPPORTED = ("USD", "EUR", "GBP", "CAD", "AUD")  # currencies we offer links for (PayPal Invoicing)
OPEN_STATUSES = ("UNPAID", "SENT", "PARTIALLY_PAID", "MARKED_AS_PARTIALLY_PAID", "SCHEDULED")
HANDLED_EVENTS = ("INVOICING.INVOICE.PAID", "INVOICING.INVOICE.REFUNDED", "INVOICING.INVOICE.CANCELLED",
                  "INVOICING.INVOICE.UPDATED")

BOOK_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_request (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    party_id      INTEGER NOT NULL REFERENCES party (id),
    amount_minor  INTEGER NOT NULL CHECK (amount_minor > 0),
    currency      TEXT    NOT NULL,
    invoice_id    TEXT    NOT NULL UNIQUE,
    link_url      TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    paid_seen     INTEGER NOT NULL DEFAULT 0,
    refunded_seen INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS inbox_item (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL CHECK (kind IN ('payment','refund','cancelled')),
    request_id    INTEGER REFERENCES payment_request (id),
    party_id      INTEGER,
    amount_minor  INTEGER NOT NULL,
    currency      TEXT    NOT NULL,
    detail        TEXT    NOT NULL DEFAULT '{}',
    status        TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open','confirmed','dismissed')),
    entry_id      INTEGER,
    created_at    TEXT    NOT NULL,
    decided_at    TEXT
);
"""

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS invoice_book (invoice_id TEXT PRIMARY KEY, book_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paypal_event (
    event_id    TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    resource_id TEXT,
    verified    INTEGER NOT NULL,
    payload     TEXT NOT NULL,
    received_at TEXT NOT NULL
);
"""


class CollectError(ValueError):
    """Shown to the user as a 400."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registry:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "collect.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as con:
            con.executescript(REGISTRY_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def link(self, invoice_id: str, book_id: str) -> None:
        with closing(self._connect()) as con, con:
            con.execute("INSERT OR REPLACE INTO invoice_book VALUES (?, ?)", (invoice_id, book_id))

    def book_for(self, invoice_id: str) -> str | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT book_id FROM invoice_book WHERE invoice_id = ?", (invoice_id,)).fetchone()
        return row["book_id"] if row else None

    def record_event(self, event_id: str, etype: str, resource_id: str | None, verified: bool,
                     payload: str) -> bool:
        """True if this event should be acted on: first time we see this event_id, or the first
        verified copy of an event we only had unverified (e.g. it arrived before the webhook id
        was configured, and PayPal retried or resent it)."""
        with self._lock, closing(self._connect()) as con, con:
            try:
                con.execute("INSERT INTO paypal_event VALUES (?, ?, ?, ?, ?, ?)",
                            (event_id, etype, resource_id, int(verified), payload[:200_000], _now()))
                return True
            except sqlite3.IntegrityError:
                if not verified:
                    return False
                cur = con.execute("UPDATE paypal_event SET verified = 1, payload = ?, received_at = ? "
                                  "WHERE event_id = ? AND verified = 0",
                                  (payload[:200_000], _now(), event_id))
                return cur.rowcount == 1


class Collect:
    """Collect features for one book."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        with closing(self._connect()) as con:
            con.executescript(BOOK_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return self.ledger._connect()

    # ── payment requests ───────────────────────────────────────────────
    def create_request(self, paypal, registry: Registry, book_id: str, party_id: int,
                       amount_minor: int | None = None, note: str = "") -> dict:
        currency = self.ledger.currency()
        if currency not in SUPPORTED:
            raise CollectError(f"PayPal payment links are available for {', '.join(SUPPORTED)} books. "
                               f"This book uses {currency}.")
        party = self.ledger.get_party(party_id)
        if party is None:
            raise CollectError("Customer not found")
        balance = self.ledger.party_balance(party_id)
        if balance <= 0:
            raise CollectError(f"{party['name']} owes nothing right now.")
        amount = balance if amount_minor is None else amount_minor
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise CollectError("Amount must be a positive whole number of minor units")
        if amount > balance:
            raise CollectError("The link can't be for more than the customer owes.")
        # One live link per customer, so nobody pays the same debt twice.
        stale = []
        for old in self._open_requests(paypal, party_id):
            if old["paid_seen"] > 0:
                if old["amount_minor"] - old["paid_seen"] == amount:
                    return {**old, "reused": True, "due_minor": amount}  # part-paid link already asks for exactly this
                continue  # PayPal can't cancel a part-paid invoice (422); it stays open
            stale.append(old)
        shop = self.ledger.get_shop() or {"name": "Shop"}
        try:
            inv = paypal.create_invoice(amount_minor=amount, currency=currency, customer_name=party["name"],
                                        shop_name=shop["name"], note=note, allow_partial=True,
                                        reference=f"sayledger:{book_id[:8]}:{party_id}")
            sent = paypal.send_invoice(inv["id"], notify_recipient=False)
        except PayPalError as e:
            raise CollectError(f"PayPal didn't create the link: {e}") from e
        link = payer_link(inv, sent)
        if not link:
            raise CollectError("PayPal didn't return a payment link.")
        with closing(self._connect()) as con, con:
            cur = con.execute(
                "INSERT INTO payment_request (party_id, amount_minor, currency, invoice_id, link_url, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)", (party_id, amount, currency, inv["id"], link, "UNPAID", _now()))
            rid = cur.lastrowid
        registry.link(inv["id"], book_id)
        for old in stale:
            self._cancel(paypal, old)
        return self.get_request(rid)

    def _open_requests(self, paypal, party_id: int) -> list[dict]:
        """This customer's links that can still take money, refreshed from PayPal first so no payment is missed."""
        out = []
        for req in self.requests(party_id):
            if req["status"] not in OPEN_STATUSES:
                continue
            try:
                self.reconcile(paypal.get_invoice(req["invoice_id"]))
            except PayPalError:
                pass  # keep what we know
            req = self.get_request(req["id"])
            if req["status"] in OPEN_STATUSES:
                out.append(req)
        return out

    def _cancel(self, paypal, req: dict) -> None:
        """Replace an unpaid link. Marked CANCELLED locally first, so the later webhook/sync adds no inbox noise."""
        try:
            paypal.cancel_invoice(req["invoice_id"])
        except PayPalError:
            return  # still open at PayPal; a payment on it would still reach the inbox
        with closing(self._connect()) as con, con:
            con.execute("UPDATE payment_request SET status = 'CANCELLED', updated_at = ? WHERE id = ?", (_now(), req["id"]))

    def get_request(self, request_id: int) -> dict | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM payment_request WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def request_by_invoice(self, invoice_id: str) -> dict | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM payment_request WHERE invoice_id = ?", (invoice_id,)).fetchone()
        return dict(row) if row else None

    def requests(self, party_id: int | None = None) -> list[dict]:
        q, a = "SELECT * FROM payment_request", ()
        if party_id is not None:
            q, a = q + " WHERE party_id = ?", (party_id,)
        with closing(self._connect()) as con:
            return [dict(r) for r in con.execute(q + " ORDER BY id DESC", a)]

    # ── reconciliation (shared by webhook and sync) ─────────────────────
    def reconcile(self, invoice: dict) -> list[dict]:
        """Compare PayPal's invoice with what we've already seen; new money -> inbox proposals."""
        req = self.request_by_invoice(invoice.get("id", ""))
        if req is None:
            return []
        cur = req["currency"]
        payments = invoice.get("payments") or {}
        paid_total = to_minor(payments.get("paid_amount", {}).get("value", "0"), cur)
        refunds = invoice.get("refunds") or {}
        refund_total = to_minor(refunds.get("refund_amount", {}).get("value", "0"), cur)
        status = invoice.get("status", req["status"])
        txns = payments.get("transactions") or []
        last = txns[-1] if txns else {}
        new_items: list[dict] = []
        with closing(self._connect()) as con, con:
            if paid_total > req["paid_seen"]:
                delta = paid_total - req["paid_seen"]
                detail = {"invoice_id": req["invoice_id"], "paypal_status": status,
                          "method": last.get("method"), "type": last.get("type"),
                          "payment_id": last.get("payment_id"), "paid_total_minor": paid_total,
                          "requested_minor": req["amount_minor"]}
                new_items.append(self._add_item(con, "payment", req, delta, detail))
                con.execute("UPDATE payment_request SET paid_seen = ? WHERE id = ?", (paid_total, req["id"]))
            if refund_total > req["refunded_seen"]:
                delta = refund_total - req["refunded_seen"]
                new_items.append(self._add_item(con, "refund", req, delta,
                                                {"invoice_id": req["invoice_id"], "paypal_status": status}))
                con.execute("UPDATE payment_request SET refunded_seen = ? WHERE id = ?", (refund_total, req["id"]))
            if status == "CANCELLED" and req["status"] != "CANCELLED":
                new_items.append(self._add_item(con, "cancelled", req, 0, {"invoice_id": req["invoice_id"]}))
            con.execute("UPDATE payment_request SET status = ?, updated_at = ? WHERE id = ?",
                        (status, _now(), req["id"]))
        return new_items

    def _add_item(self, con, kind: str, req: dict, amount: int, detail: dict) -> dict:
        cur = con.execute(
            "INSERT INTO inbox_item (kind, request_id, party_id, amount_minor, currency, detail, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, req["id"], req["party_id"], amount, req["currency"], json.dumps(detail), _now()))
        return dict(con.execute("SELECT * FROM inbox_item WHERE id = ?", (cur.lastrowid,)).fetchone())

    def sync(self, paypal) -> list[dict]:
        """Fallback when webhooks can't reach us: re-read every open request from PayPal."""
        out: list[dict] = []
        for req in self.requests():
            if req["status"] in ("PAID", "CANCELLED", "REFUNDED") and req["paid_seen"] >= req["amount_minor"]:
                continue
            try:
                out += self.reconcile(paypal.get_invoice(req["invoice_id"]))
            except PayPalError:
                continue
        return out

    # ── inbox ──────────────────────────────────────────────────────────
    def inbox(self, status: str = "open") -> list[dict]:
        with closing(self._connect()) as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM inbox_item WHERE status = ? ORDER BY id DESC", (status,))]
        for r in rows:
            r["detail"] = json.loads(r["detail"] or "{}")
            r["proposal"] = self.proposal(r)
        return rows

    def proposal(self, item: dict) -> dict:
        """What confirming would do — all numbers from the ledger and PayPal's data."""
        party = self.ledger.get_party(item["party_id"]) if item.get("party_id") else None
        balance = self.ledger.party_balance(item["party_id"]) if party else 0
        if item["kind"] == "payment":
            after = balance - item["amount_minor"]
            return {"action": "payment_received", "party": party["name"] if party else None,
                    "balance_before_minor": balance, "balance_after_minor": after,
                    "advance_minor": max(0, -after) - max(0, -balance)}
        if item["kind"] == "refund":
            return {"action": "credit_given", "party": party["name"] if party else None,
                    "balance_before_minor": balance, "balance_after_minor": balance + item["amount_minor"],
                    "note": "PayPal refund — the customer owes this again"}
        return {"action": "none", "party": party["name"] if party else None}

    def _item(self, item_id: int) -> dict:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM inbox_item WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise CollectError("Inbox item not found")
        return dict(row)

    def confirm(self, item_id: int, party_id: int | None = None) -> dict:
        item = self._item(item_id)
        if item["status"] != "open":
            raise CollectError("This item was already handled.")
        pid = party_id or item["party_id"]
        if pid is None or self.ledger.get_party(pid) is None:
            raise CollectError("Pick a customer first.")
        detail = json.loads(item["detail"] or "{}")
        entry = None
        if item["kind"] == "payment":
            entry = self.ledger.add_entry("payment_received", item["amount_minor"], pid,
                                          note=f"PayPal {detail.get('invoice_id', '')}".strip(), source="paypal",
                                          raw_text=json.dumps(detail))
        elif item["kind"] == "refund":
            entry = self.ledger.add_entry("credit_given", item["amount_minor"], pid,
                                          note=f"PayPal refund {detail.get('invoice_id', '')}".strip(),
                                          source="paypal", raw_text=json.dumps(detail))
        with closing(self._connect()) as con, con:
            con.execute("UPDATE inbox_item SET status = 'confirmed', party_id = ?, entry_id = ?, decided_at = ?"
                        " WHERE id = ?", (pid, entry["id"] if entry else None, _now(), item_id))
        return {"item_id": item_id, "entry": entry, "balance_minor": self.ledger.party_balance(pid)}

    def dismiss(self, item_id: int) -> dict:
        item = self._item(item_id)
        if item["status"] != "open":
            raise CollectError("This item was already handled.")
        with closing(self._connect()) as con, con:
            con.execute("UPDATE inbox_item SET status = 'dismissed', decided_at = ? WHERE id = ?", (_now(), item_id))
        return {"item_id": item_id, "status": "dismissed"}


def parse_event(raw: bytes) -> dict:
    try:
        ev = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise CollectError("Webhook body is not JSON") from e
    if not isinstance(ev, dict) or not ev.get("id") or not ev.get("event_type"):
        raise CollectError("Not a PayPal webhook event")
    res = ev.get("resource") or {}
    inv = res.get("invoice") if isinstance(res.get("invoice"), dict) else res
    return {"event_id": ev["id"], "type": ev["event_type"], "invoice_id": (inv or {}).get("id")}
