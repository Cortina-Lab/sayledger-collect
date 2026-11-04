"""SQLite ledger: schema, queries and ALL balance maths.

Money is always an integer in minor units (paise / cents) plus an ISO 4217
currency code. The AI never computes totals; every number shown to the user
comes from the functions in this file.

Entries are never hard-deleted: a mistake is *voided* (kept, excluded from
every total) and every create / edit / void is written to the audit log.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

KINDS = ("credit_given", "payment_received", "cash_sale", "expense")
SOURCES = ("voice", "ledger_photo", "manual", "paypal")
SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS shop (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    name       TEXT    NOT NULL,
    currency   TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS party (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    phone      TEXT,
    language   TEXT,
    created_at TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS party_name_ci ON party (name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS entry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    party_id     INTEGER REFERENCES party (id) ON DELETE RESTRICT,
    kind         TEXT    NOT NULL CHECK (kind IN ('credit_given','payment_received','cash_sale','expense')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency     TEXT    NOT NULL,
    note         TEXT    NOT NULL DEFAULT '',
    source       TEXT    NOT NULL CHECK (source IN ('voice','ledger_photo','manual','paypal')),
    raw_text     TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT,
    voided_at    TEXT,
    void_reason  TEXT,
    CHECK (kind NOT IN ('credit_given','payment_received') OR party_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS entry_party ON entry (party_id);
CREATE INDEX IF NOT EXISTS entry_created ON entry (created_at);
CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER,
    party_id INTEGER,
    action   TEXT NOT NULL,
    before   TEXT,
    after    TEXT,
    at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_entry ON audit (entry_id);
"""

LIVE = "voided_at IS NULL"


class LedgerError(ValueError):
    """Bad input to the ledger (shown to the user as a 400)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_amount(amount_minor) -> None:
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise LedgerError("Amount must be a positive whole number of minor units")


class Ledger:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with closing(self._connect()) as con:
            con.executescript(SCHEMA)
            self._migrate(con)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        return con

    @staticmethod
    def _migrate(con: sqlite3.Connection) -> None:
        """Bring v1 books (no edit/void columns) up to date in place."""
        cols = {r["name"] for r in con.execute("PRAGMA table_info(entry)")}
        with con:
            for col in ("updated_at", "voided_at", "void_reason"):
                if col not in cols:
                    con.execute(f"ALTER TABLE entry ADD COLUMN {col} TEXT")
        # v3: allow source 'paypal' (SQLite can't alter a CHECK, so rebuild the table once).
        sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='entry'").fetchone()["sql"]
        if "'paypal'" not in sql:
            con.execute("PRAGMA foreign_keys = OFF")
            with con:
                con.execute("ALTER TABLE entry RENAME TO entry_v2")
                con.executescript(SCHEMA.split("CREATE TABLE IF NOT EXISTS audit")[0].split(
                    "CREATE UNIQUE INDEX IF NOT EXISTS party_name_ci ON party (name COLLATE NOCASE);")[1])
                cols_v2 = ", ".join(r["name"] for r in con.execute("PRAGMA table_info(entry_v2)"))
                con.execute(f"INSERT INTO entry ({cols_v2}) SELECT {cols_v2} FROM entry_v2")
                con.execute("DROP TABLE entry_v2")
            con.execute("PRAGMA foreign_keys = ON")
        with con:
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _audit(self, con, action: str, entry_id=None, party_id=None, before=None, after=None) -> None:
        con.execute(
            "INSERT INTO audit (entry_id, party_id, action, before, after, at) VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, party_id, action,
             json.dumps(before, ensure_ascii=False) if before is not None else None,
             json.dumps(after, ensure_ascii=False) if after is not None else None,
             _now()),
        )

    # ── shop ──────────────────────────────────────────────────────────
    def get_shop(self) -> dict | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM shop WHERE id = 1").fetchone()
        return dict(row) if row else None

    def set_shop(self, name: str, currency: str = "INR") -> dict:
        name, currency = name.strip(), currency.strip().upper()
        if not name:
            raise LedgerError("Shop name is required")
        if len(name) > 80:
            raise LedgerError("Shop name is too long")
        if len(currency) != 3 or not currency.isalpha():
            raise LedgerError("Currency must be a 3-letter ISO 4217 code")
        shop = self.get_shop()
        if shop and shop["currency"] != currency and self.entry_count() > 0:
            raise LedgerError("Currency can't change once the book has entries")
        with closing(self._connect()) as con, con:
            con.execute(
                "INSERT INTO shop (id, name, currency, created_at) VALUES (1, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET name = excluded.name, currency = excluded.currency",
                (name, currency, _now()),
            )
        return self.get_shop()

    def currency(self) -> str:
        shop = self.get_shop()
        return shop["currency"] if shop else "INR"

    # ── parties ───────────────────────────────────────────────────────
    def add_party(self, name: str, phone: str | None = None, language: str | None = None) -> dict:
        name = " ".join(name.split())
        if not name:
            raise LedgerError("Customer name is required")
        if len(name) > 80:
            raise LedgerError("Customer name is too long")
        with closing(self._connect()) as con, con:
            try:
                cur = con.execute(
                    "INSERT INTO party (name, phone, language, created_at) VALUES (?, ?, ?, ?)",
                    (name, phone, language, _now()),
                )
            except sqlite3.IntegrityError:
                raise LedgerError(f"Customer '{name}' already exists") from None
            self._audit(con, "party_create", party_id=cur.lastrowid, after={"name": name})
        return self.get_party(cur.lastrowid)

    def update_party(self, party_id: int, name: str | None = None, phone: str | None = None,
                     language: str | None = None) -> dict:
        old = self.get_party(party_id)
        if old is None:
            raise LedgerError(f"Customer {party_id} not found")
        new = dict(old)
        if name is not None:
            name = " ".join(name.split())
            if not name or len(name) > 80:
                raise LedgerError("Customer name must be 1–80 characters")
            new["name"] = name
        if phone is not None:
            phone = phone.strip()
            if phone and not all(ch.isdigit() or ch in "+ -()" for ch in phone):
                raise LedgerError("Phone can only contain digits, spaces, +, -, ( )")
            new["phone"] = phone or None
        if language is not None:
            new["language"] = language.strip()[:40] or None
        with closing(self._connect()) as con, con:
            try:
                con.execute("UPDATE party SET name = ?, phone = ?, language = ? WHERE id = ?",
                            (new["name"], new["phone"], new["language"], party_id))
            except sqlite3.IntegrityError:
                raise LedgerError(f"Customer '{new['name']}' already exists") from None
            self._audit(con, "party_edit", party_id=party_id,
                        before={k: old[k] for k in ("name", "phone", "language")},
                        after={k: new[k] for k in ("name", "phone", "language")})
        return self.get_party(party_id)

    def find_party(self, name: str) -> dict | None:
        """Exact, case-insensitive match. Fuzzy matching lives in the agent layer."""
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT * FROM party WHERE name = ? COLLATE NOCASE", (" ".join(name.split()),)
            ).fetchone()
        return dict(row) if row else None

    def get_party(self, party_id: int) -> dict | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM party WHERE id = ?", (party_id,)).fetchone()
        return dict(row) if row else None

    def party_names(self) -> list[str]:
        with closing(self._connect()) as con:
            return [r["name"] for r in con.execute("SELECT name FROM party ORDER BY name")]

    # ── entries ───────────────────────────────────────────────────────
    def _validate(self, kind: str, amount_minor, party_id, source: str) -> None:
        if kind not in KINDS:
            raise LedgerError(f"Unknown entry kind: {kind}")
        if source not in SOURCES:
            raise LedgerError(f"Unknown source: {source}")
        _check_amount(amount_minor)
        if kind in ("credit_given", "payment_received") and party_id is None:
            raise LedgerError("Credit and payments need a customer")
        if party_id is not None and self.get_party(party_id) is None:
            raise LedgerError(f"Customer {party_id} not found")

    def add_entry(
        self,
        kind: str,
        amount_minor: int,
        party_id: int | None = None,
        note: str = "",
        source: str = "manual",
        raw_text: str = "",
        created_at: str | None = None,
    ) -> dict:
        self._validate(kind, amount_minor, party_id, source)
        with closing(self._connect()) as con, con:
            cur = con.execute(
                "INSERT INTO entry (party_id, kind, amount_minor, currency, note, source, raw_text, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (party_id, kind, amount_minor, self.currency(), note.strip()[:200], source, raw_text[:2000],
                 created_at or _now()),
            )
            row = dict(con.execute("SELECT * FROM entry WHERE id = ?", (cur.lastrowid,)).fetchone())
            self._audit(con, "create", entry_id=row["id"], party_id=party_id,
                        after={k: row[k] for k in ("kind", "amount_minor", "note", "source")})
        return row

    def get_entry(self, entry_id: int) -> dict | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM entry WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None

    def edit_entry(self, entry_id: int, *, kind: str | None = None, amount_minor: int | None = None,
                   party_id: int | None = None, note: str | None = None) -> dict:
        old = self.get_entry(entry_id)
        if old is None:
            raise LedgerError("Entry not found")
        if old["voided_at"]:
            raise LedgerError("A voided entry can't be edited")
        new = dict(old)
        if kind is not None:
            new["kind"] = kind
        if amount_minor is not None:
            new["amount_minor"] = amount_minor
        if party_id is not None:
            new["party_id"] = party_id
        if note is not None:
            new["note"] = note.strip()[:200]
        self._validate(new["kind"], new["amount_minor"], new["party_id"], new["source"])
        fields = ("kind", "amount_minor", "party_id", "note")
        if all(old[f] == new[f] for f in fields):
            return old
        with closing(self._connect()) as con, con:
            con.execute(
                "UPDATE entry SET kind = ?, amount_minor = ?, party_id = ?, note = ?, updated_at = ? WHERE id = ?",
                (new["kind"], new["amount_minor"], new["party_id"], new["note"], _now(), entry_id),
            )
            self._audit(con, "edit", entry_id=entry_id, party_id=new["party_id"],
                        before={f: old[f] for f in fields}, after={f: new[f] for f in fields})
        return self.get_entry(entry_id)

    def void_entry(self, entry_id: int, reason: str = "") -> bool:
        """Cancel a wrong entry. It stays in the book (marked) but leaves every total."""
        old = self.get_entry(entry_id)
        if old is None or old["voided_at"]:
            return False
        with closing(self._connect()) as con, con:
            con.execute("UPDATE entry SET voided_at = ?, void_reason = ? WHERE id = ?",
                        (_now(), reason.strip()[:200], entry_id))
            self._audit(con, "void", entry_id=entry_id, party_id=old["party_id"],
                        before={"amount_minor": old["amount_minor"], "kind": old["kind"]},
                        after={"reason": reason.strip()[:200]})
        return True

    # Kept for older callers/tests: "delete" means void.
    delete_entry = void_entry

    def entry_count(self) -> int:
        with closing(self._connect()) as con:
            return con.execute("SELECT COUNT(*) FROM entry").fetchone()[0]

    def entries(
        self,
        start: date | None = None,
        end: date | None = None,
        kind: str | None = None,
        party_id: int | None = None,
        include_voided: bool = False,
        limit: int | None = None,
    ) -> list[dict]:
        """Day book: entries oldest-first, with party names, filtered."""
        sql = ("SELECT e.*, p.name AS party_name FROM entry e LEFT JOIN party p ON p.id = e.party_id WHERE 1=1")
        args: list = []
        if not include_voided:
            sql += " AND e.voided_at IS NULL"
        if start:
            sql += " AND date(e.created_at, 'localtime') >= ?"
            args.append(start.isoformat())
        if end:
            sql += " AND date(e.created_at, 'localtime') <= ?"
            args.append(end.isoformat())
        if kind:
            if kind not in KINDS:
                raise LedgerError(f"Unknown entry kind: {kind}")
            sql += " AND e.kind = ?"
            args.append(kind)
        if party_id is not None:
            sql += " AND e.party_id = ?"
            args.append(party_id)
        sql += " ORDER BY e.created_at, e.id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with closing(self._connect()) as con:
            return [dict(r) for r in con.execute(sql, args)]

    def entries_for_party(self, party_id: int, start: date | None = None, end: date | None = None,
                          include_voided: bool = False) -> list[dict]:
        return self.entries(start=start, end=end, party_id=party_id, include_voided=include_voided)

    def audit_log(self, entry_id: int | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM audit", []
        if entry_id is not None:
            sql += " WHERE entry_id = ?"
            args.append(entry_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))
        with closing(self._connect()) as con:
            rows = [dict(r) for r in con.execute(sql, args)]
        for r in rows:
            r["before"] = json.loads(r["before"]) if r["before"] else None
            r["after"] = json.loads(r["after"]) if r["after"] else None
        return rows

    # ── balances (all maths here) ─────────────────────────────────────
    _BALANCE_SQL = """
        SELECT p.id, p.name, p.phone, p.language,
               COALESCE(SUM(CASE WHEN e.kind = 'credit_given'     THEN e.amount_minor END), 0)
             - COALESCE(SUM(CASE WHEN e.kind = 'payment_received' THEN e.amount_minor END), 0)
               AS balance_minor,
               MAX(e.created_at) AS last_entry_at
        FROM party p LEFT JOIN entry e ON e.party_id = p.id AND e.voided_at IS NULL
    """

    def party_balance(self, party_id: int) -> int:
        """Customer owes the shop this much (negative = shop owes them / advance)."""
        with closing(self._connect()) as con:
            row = con.execute(self._BALANCE_SQL + " WHERE p.id = ? GROUP BY p.id", (party_id,)).fetchone()
        if row is None:
            raise LedgerError(f"Customer {party_id} not found")
        return int(row["balance_minor"])

    def parties_with_balances(self) -> list[dict]:
        with closing(self._connect()) as con:
            rows = con.execute(self._BALANCE_SQL + " GROUP BY p.id ORDER BY p.name COLLATE NOCASE")
            return [dict(r) for r in rows]

    def outstanding_total(self) -> int:
        """Sum of positive balances only: money customers still owe the shop."""
        return sum(p["balance_minor"] for p in self.parties_with_balances() if p["balance_minor"] > 0)

    def advance_total(self) -> int:
        """Money the shop holds as customer advances (shown as a positive number)."""
        return -sum(p["balance_minor"] for p in self.parties_with_balances() if p["balance_minor"] < 0)

    def top_debtors(self, n: int = 5) -> list[dict]:
        owing = [p for p in self.parties_with_balances() if p["balance_minor"] > 0]
        return sorted(owing, key=lambda p: (-p["balance_minor"], p["name"].lower()))[: max(n, 0)]

    def _totals(self, rows: list[dict]) -> dict:
        totals = {k: 0 for k in KINDS}
        for r in rows:
            totals[r["kind"]] += r["amount_minor"]
        return totals

    def day_summary(self, day: date) -> dict:
        rows = self.entries(start=day, end=day)
        totals = self._totals(rows)
        return {
            "day": day.isoformat(),
            "currency": self.currency(),
            "credit_given_minor": totals["credit_given"],
            "payment_received_minor": totals["payment_received"],
            "cash_sale_minor": totals["cash_sale"],
            "expense_minor": totals["expense"],
            "entries": len(rows),
            "outstanding_total_minor": self.outstanding_total(),
        }

    def report(self, start: date, end: date) -> dict:
        """Period report: totals by kind, net cash, and per-customer movement + balances."""
        if end < start:
            raise LedgerError("End date is before start date")
        rows = self.entries(start=start, end=end)
        totals = self._totals(rows)
        movement: dict[int, dict] = {}
        for r in rows:
            if r["party_id"] is None:
                continue
            m = movement.setdefault(r["party_id"], {"credit_given_minor": 0, "payment_received_minor": 0})
            if r["kind"] in ("credit_given", "payment_received"):
                m[f"{r['kind']}_minor"] += r["amount_minor"]
        parties = []
        for p in self.parties_with_balances():
            m = movement.get(p["id"])
            if m is None and p["balance_minor"] == 0:
                continue
            parties.append({"id": p["id"], "name": p["name"], "balance_minor": p["balance_minor"],
                            **(m or {"credit_given_minor": 0, "payment_received_minor": 0})})
        parties.sort(key=lambda p: (-p["balance_minor"], p["name"].lower()))
        return {
            "shop": self.get_shop(),
            "currency": self.currency(),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "entries": len(rows),
            "credit_given_minor": totals["credit_given"],
            "payment_received_minor": totals["payment_received"],
            "cash_sale_minor": totals["cash_sale"],
            "expense_minor": totals["expense"],
            # Money that actually came in / went out in the period.
            "cash_in_minor": totals["cash_sale"] + totals["payment_received"],
            "cash_out_minor": totals["expense"],
            "net_cash_minor": totals["cash_sale"] + totals["payment_received"] - totals["expense"],
            "outstanding_total_minor": self.outstanding_total(),
            "advance_total_minor": self.advance_total(),
            "parties": parties,
        }

    def statement(self, party_id: int, start: date | None = None, end: date | None = None) -> dict:
        """Customer statement: opening balance, each entry with running balance, closing."""
        party = self.get_party(party_id)
        if party is None:
            raise LedgerError(f"Customer {party_id} not found")
        if start and end and end < start:
            raise LedgerError("End date is before start date")
        opening = 0
        if start:
            for e in self.entries(end=date.fromordinal(start.toordinal() - 1), party_id=party_id):
                opening += self._signed(e)
        running = opening
        lines = []
        for e in self.entries(start=start, end=end, party_id=party_id):
            running += self._signed(e)
            lines.append({**e, "balance_after_minor": running})
        return {
            "shop": self.get_shop(),
            "party": party,
            "currency": self.currency(),
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
            "opening_balance_minor": opening,
            "lines": lines,
            "credit_given_minor": sum(e["amount_minor"] for e in lines if e["kind"] == "credit_given"),
            "payment_received_minor": sum(e["amount_minor"] for e in lines if e["kind"] == "payment_received"),
            "closing_balance_minor": running,
            "generated_at": _now(),
        }

    def collections(self, today: date | None = None) -> dict:
        """Who owes what, and for how long (FIFO: payments clear the oldest credit first).

        Buckets are by age of the still-unpaid credit: 0-30, 31-60, 61-90, 90+ days.
        A customer is suggested for a reminder when money has been unpaid for over
        14 days and they haven't paid anything in the last 14 days.
        """
        today = today or date.today()
        buckets = {"0_30": 0, "31_60": 0, "61_90": 0, "90_plus": 0}
        people = []
        for p in self.parties_with_balances():
            if p["balance_minor"] <= 0:
                continue
            open_credits: list[list] = []  # [day, remaining]
            last_payment = None
            for e in self.entries(party_id=p["id"]):
                day = datetime.fromisoformat(e["created_at"]).astimezone().date()
                if e["kind"] == "credit_given":
                    open_credits.append([day, e["amount_minor"]])
                elif e["kind"] == "payment_received":
                    last_payment = day
                    left = e["amount_minor"]
                    while left and open_credits:
                        take = min(left, open_credits[0][1])
                        open_credits[0][1] -= take
                        left -= take
                        if open_credits[0][1] == 0:
                            open_credits.pop(0)
            mine = {"0_30": 0, "31_60": 0, "61_90": 0, "90_plus": 0}
            for day, amt in open_credits:
                age = (today - day).days
                key = "0_30" if age <= 30 else "31_60" if age <= 60 else "61_90" if age <= 90 else "90_plus"
                mine[key] += amt
            for k in buckets:
                buckets[k] += mine[k]
            oldest_days = (today - open_credits[0][0]).days if open_credits else 0
            since_paid = (today - last_payment).days if last_payment else None
            people.append({
                "id": p["id"], "name": p["name"], "phone": p["phone"], "language": p["language"],
                "balance_minor": p["balance_minor"], "oldest_unpaid_days": oldest_days,
                "days_since_payment": since_paid, "buckets": mine,
                "remind": oldest_days > 14 and (since_paid is None or since_paid > 14),
            })
        people.sort(key=lambda x: (-x["oldest_unpaid_days"], -x["balance_minor"]))
        return {
            "currency": self.currency(),
            "as_of": today.isoformat(),
            "outstanding_total_minor": sum(x["balance_minor"] for x in people),
            "buckets": buckets,
            "customers": people,
            "remind_count": sum(1 for x in people if x["remind"]),
        }

    @staticmethod
    def _signed(e: dict) -> int:
        return {"credit_given": e["amount_minor"], "payment_received": -e["amount_minor"]}.get(e["kind"], 0)

    # ── export / backup ───────────────────────────────────────────────
    def export_csv(self, start: date | None = None, end: date | None = None) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "date_utc", "type", "customer", "amount", "currency", "note", "source", "status"])
        cur = self.currency()
        exp = 0 if cur in {"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "XAF", "XOF", "PYG", "RWF"} else 2
        for e in self.entries(start=start, end=end, include_voided=True):
            amount = f"{e['amount_minor'] / 10**exp:.{exp}f}"
            w.writerow([e["id"], e["created_at"], e["kind"], e["party_name"] or "", amount, e["currency"],
                        e["note"], e["source"], "voided" if e["voided_at"] else "active"])
        return buf.getvalue()

    def backup_to(self, dest: str | Path) -> None:
        """Consistent copy of the whole book (safe while the app is running)."""
        with closing(self._connect()) as src, closing(sqlite3.connect(str(dest))) as dst:
            src.backup(dst)

    @staticmethod
    def validate_backup(path: str | Path) -> None:
        """Raise LedgerError unless the file is a SayLedger book."""
        try:
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as con:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise LedgerError("Backup file is damaged")
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError:
            raise LedgerError("That file is not a SayLedger backup") from None
        if not {"shop", "party", "entry"} <= tables:
            raise LedgerError("That file is not a SayLedger backup")
