"""SayLedger backend — FastAPI routes.

Every route except /health and POST /books needs `Authorization: Bearer <book token>`.
Nothing the model says is saved until the shopkeeper confirms it
(/entries/confirm). All totals come from ledger.py.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from . import agent, collect, meta_client, paypal_client, stock
from .books import Books, default_data_dir
from .ledger import Ledger, LedgerError
from .schemas import (
    AskIn, BookIn, ConfirmIn, EntryEdit, PartyEdit, ProposalsOut, ReminderIn, ShopIn, TextIn, VoidIn,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sayledger")

app = FastAPI(title="SayLedger by Cortina", version="1.0.0")
books = Books(default_data_dir())

MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_BACKUP_BYTES = 50 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


# ── errors: short, human, never a stack trace ─────────────────────────
@app.exception_handler(LedgerError)
def _ledger_error(_: Request, e: LedgerError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


@app.exception_handler(meta_client.MetaAPIError)
def _meta_error(_: Request, e: meta_client.MetaAPIError):
    log.warning("meta api: %s", e)
    return JSONResponse(status_code=502, content={"detail": "The AI service is busy. Please try again."})


@app.exception_handler(agent.AgentError)
def _agent_error(_: Request, e: agent.AgentError):
    log.info("agent: %s", e)
    return JSONResponse(status_code=422, content={"detail": "Couldn't understand, please try again or type it"})


@app.exception_handler(stock.StockError)
def _stock_error(_: Request, e: stock.StockError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


@app.exception_handler(collect.CollectError)
def _collect_error(_: Request, e: collect.CollectError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


@app.exception_handler(Exception)
def _unexpected(_: Request, e: Exception):
    log.exception("unhandled")
    return JSONResponse(status_code=500, content={"detail": "Something went wrong. Please try again."})


@app.middleware("http")
async def _access_log(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    # Path + status + time only: no bodies, no tokens, no customer data in logs.
    log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code, (time.monotonic() - t0) * 1000)
    return response


# ── auth: one book per token ──────────────────────────────────────────
class Book:
    def __init__(self, book_id: str, ledger: Ledger):
        self.id, self.ledger = book_id, ledger


def current_book(authorization: str = Header(default="")) -> Book:
    token = authorization.removeprefix("Bearer ").strip()
    opened = books.open(token)
    if opened is None:
        raise HTTPException(401, "This device isn't linked to a book. Reopen the app to set it up.")
    return Book(*opened)


# Simple per-book limit on AI calls (each one costs real model credits).
AI_LIMIT, AI_WINDOW = 40, 600
_ai_calls: dict[str, deque] = defaultdict(deque)


def ai_quota(book: Book = Depends(current_book)) -> Book:
    q, now = _ai_calls[book.id], time.monotonic()
    while q and now - q[0] > AI_WINDOW:
        q.popleft()
    if len(q) >= AI_LIMIT:
        raise HTTPException(429, "Too many AI requests. Please wait a few minutes.")
    q.append(now)
    return book


async def _read_limited(upload: UploadFile, limit: int, what: str) -> bytes:
    data = await upload.read(limit + 1)
    if not data:
        raise HTTPException(400, f"{what} is empty")
    if len(data) > limit:
        raise HTTPException(413, f"{what} is too large")
    return data


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# ── books ─────────────────────────────────────────────────────────────
@app.post("/books")
def create_book(body: BookIn) -> dict:
    book_id, token, ledger = books.create(body.shop_name, body.currency)
    log.info("book created %s", book_id[:8])
    return {"token": token, "shop": ledger.get_shop()}


@app.delete("/books/me")
def delete_book(book: Book = Depends(current_book)) -> dict:
    books.delete(book.id)
    log.info("book deleted %s", book.id[:8])
    return {"deleted": True}


@app.get("/shop")
def get_shop(book: Book = Depends(current_book)) -> dict:
    return {**book.ledger.get_shop(), "entries": book.ledger.entry_count()}


@app.put("/shop")
def put_shop(body: ShopIn, book: Book = Depends(current_book)) -> dict:
    return book.ledger.set_shop(body.name, body.currency)


# ── speak / type -> proposals (nothing saved) ─────────────────────────
def _known(ledger: Ledger) -> dict[str, int]:
    return {p["name"]: p["id"] for p in ledger.parties_with_balances()}


def _propose(text: str, ledger: Ledger) -> ProposalsOut:
    currency = ledger.currency()
    return ProposalsOut(heard=text, proposals=agent.extract(text, currency, _known(ledger)), currency=currency)


# Biasing helps with short shop words the model otherwise mishears (tel / til).
SHOP_WORDS = {
    "INR": ["udhaar", "wapas", "diye", "liya", "rupaye", "tel", "chawal", "cheeni", "aata", "dal", "doodh", "sabun"],
    "USD": ["tab", "credit", "paid back", "bucks", "dollars", "gallon"],
}
LANGUAGES = {"INR": ["Hindi", "English"]}


async def _transcribe(audio: UploadFile, ledger: Ledger | None = None) -> str:
    wav = await _read_limited(audio, MAX_AUDIO_BYTES, "Audio")
    cur = ledger.currency() if ledger else "INR"
    names = list(_known(ledger)) if ledger else []
    heard = meta_client.transcribe(wav, language_bias=LANGUAGES.get(cur), keywords=names + SHOP_WORDS.get(cur, [])).text.strip()
    if not heard:
        raise HTTPException(422, "Didn't catch that — please speak again closer to the phone, or type it")
    return heard


@app.post("/text", response_model=ProposalsOut)
def text_entry(body: TextIn, book: Book = Depends(ai_quota)) -> ProposalsOut:
    return _propose(body.text, book.ledger)


@app.post("/voice", response_model=ProposalsOut)
async def voice_entry(audio: UploadFile = File(...), book: Book = Depends(ai_quota)) -> ProposalsOut:
    return _propose(await _transcribe(audio, book.ledger), book.ledger)


# ── confirm -> saved ──────────────────────────────────────────────────
@app.post("/entries/confirm")
def confirm(body: ConfirmIn, book: Book = Depends(current_book)) -> dict:
    lg = book.ledger
    # Validate everything first so a bad card doesn't leave half the batch saved.
    for p in body.proposals:
        if p.kind in ("credit_given", "payment_received") and not (p.party_id or (p.party_name or "").strip()):
            raise LedgerError("Credit and payments need a customer name")
        if p.party_id is not None and lg.get_party(p.party_id) is None:
            raise LedgerError(f"Customer {p.party_id} not found")
    saved = []
    for p in body.proposals:
        pid = p.party_id
        if pid is None and (p.party_name or "").strip():
            party = lg.find_party(p.party_name) or lg.add_party(p.party_name)
            pid = party["id"]
        saved.append(lg.add_entry(p.kind, p.amount_minor, party_id=pid, note=p.note, source=body.source,
                                  raw_text=body.heard, created_at=_page_date(p.date) if body.source == "ledger_photo" else None))
    touched = {e["party_id"] for e in saved if e["party_id"]}
    return {
        "saved": saved,
        "balances": {str(pid): lg.party_balance(pid) for pid in touched},
        "currency": lg.currency(),
    }


def _page_date(value: str | None) -> str | None:
    """A date written on an old ledger page, kept only if it is real and not in the future."""
    try:
        d = date.fromisoformat((value or "")[:10])
    except ValueError:
        return None
    if d > date.today() or d.year < 2000:
        return None
    return datetime.combine(d, datetime.min.time().replace(hour=12)).astimezone().isoformat(timespec="seconds")


# ── day book / edit / void / audit ────────────────────────────────────
@app.get("/entries")
def list_entries(
    start: date | None = None,
    end: date | None = None,
    kind: str | None = None,
    party_id: int | None = None,
    include_voided: bool = False,
    book: Book = Depends(current_book),
) -> dict:
    lg = book.ledger
    rows = lg.entries(start=start, end=end, kind=kind, party_id=party_id, include_voided=include_voided)
    return {"entries": list(reversed(rows)), "currency": lg.currency()}


@app.patch("/entries/{entry_id}")
def edit_entry(entry_id: int, body: EntryEdit, book: Book = Depends(current_book)) -> dict:
    return book.ledger.edit_entry(entry_id, **body.model_dump(exclude_none=True))


@app.post("/entries/{entry_id}/void")
def void_entry(entry_id: int, body: VoidIn, book: Book = Depends(current_book)) -> dict:
    if not book.ledger.void_entry(entry_id, body.reason):
        raise HTTPException(404, "Entry not found or already voided")
    return {"voided": entry_id}


@app.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, book: Book = Depends(current_book)) -> dict:
    if not book.ledger.void_entry(entry_id, "Deleted in app"):
        raise HTTPException(404, "Entry not found or already voided")
    return {"deleted": entry_id, "voided": True}


@app.get("/audit")
def audit(entry_id: int | None = None, limit: int = 200, book: Book = Depends(current_book)) -> dict:
    return {"events": book.ledger.audit_log(entry_id, limit)}


# ── customers ─────────────────────────────────────────────────────────
@app.get("/parties")
def parties(book: Book = Depends(current_book)) -> dict:
    return {"parties": book.ledger.parties_with_balances(), "currency": book.ledger.currency()}


@app.get("/parties/{party_id}")
def party_detail(party_id: int, book: Book = Depends(current_book)) -> dict:
    lg = book.ledger
    party = lg.get_party(party_id)
    if party is None:
        raise HTTPException(404, "Customer not found")
    return {
        **party,
        "balance_minor": lg.party_balance(party_id),
        "entries": list(reversed(lg.entries_for_party(party_id, include_voided=True))),
        "currency": lg.currency(),
    }


@app.patch("/parties/{party_id}")
def edit_party(party_id: int, body: PartyEdit, book: Book = Depends(current_book)) -> dict:
    return book.ledger.update_party(party_id, **body.model_dump(exclude_none=True))


@app.get("/parties/{party_id}/statement")
def statement(party_id: int, start: date | None = None, end: date | None = None,
              book: Book = Depends(current_book)) -> dict:
    return book.ledger.statement(party_id, start, end)


# ── reports / export / backup ─────────────────────────────────────────
@app.get("/summary")
def summary(day: date | None = None, book: Book = Depends(current_book)) -> dict:
    return book.ledger.day_summary(day or date.today())


@app.get("/collections")
def collections(book: Book = Depends(current_book)) -> dict:
    return book.ledger.collections()


@app.get("/report")
def report(start: date, end: date, book: Book = Depends(current_book)) -> dict:
    return book.ledger.report(start, end)


@app.get("/export.csv")
def export_csv(start: date | None = None, end: date | None = None, book: Book = Depends(current_book)):
    data = "﻿" + book.ledger.export_csv(start, end)  # BOM so Excel reads UTF-8 names
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="sayledger-book.csv"'})


@app.get("/backup")
def backup(book: Book = Depends(current_book)):
    tmp = Path(tempfile.mkstemp(suffix=".sayledger")[1])
    book.ledger.backup_to(tmp)
    stamp = date.today().isoformat()
    return FileResponse(tmp, media_type="application/octet-stream",
                        filename=f"sayledger-backup-{stamp}.sayledger",
                        background=BackgroundTask(tmp.unlink, missing_ok=True))


@app.post("/restore")
async def restore(file: UploadFile = File(...), book: Book = Depends(current_book)) -> dict:
    data = await _read_limited(file, MAX_BACKUP_BYTES, "Backup file")
    with tempfile.NamedTemporaryFile(suffix=".sayledger", delete=False) as tmp:
        tmp.write(data)
    try:
        lg = books.restore(book.id, Path(tmp.name))
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    return {"restored": True, "shop": lg.get_shop(), "entries": lg.entry_count()}


# ── ask / ledger photo / reminder ─────────────────────────────────────
@app.post("/ask")
async def ask(request: Request, book: Book = Depends(ai_quota)) -> dict:
    if request.headers.get("content-type", "").startswith("multipart/"):
        form = await request.form()
        audio = form.get("audio")
        if not isinstance(audio, UploadFile) and not hasattr(audio, "read"):
            raise HTTPException(400, "Send audio or text")
        question = await _transcribe(audio, book.ledger)
    else:
        try:
            question = AskIn(**(await request.json())).text
        except Exception:
            raise HTTPException(400, "Send {\"text\": \"your question\"}") from None
    return {"heard": question, **agent.answer_question(question, book.ledger, date.today())}


@app.post("/ledger-photo")
async def ledger_photo(image: UploadFile = File(...), book: Book = Depends(ai_quota)) -> dict:
    mime = image.content_type or "image/jpeg"
    if mime not in IMAGE_TYPES:
        raise HTTPException(400, "Please send a JPEG, PNG or WebP photo")
    data = await _read_limited(image, MAX_IMAGE_BYTES, "Photo")
    return agent.read_ledger_photo(data, mime, book.ledger.currency(), _known(book.ledger))


@app.post("/reminder-draft")
def reminder_draft(body: ReminderIn, book: Book = Depends(ai_quota)) -> dict:
    lg = book.ledger
    party = lg.get_party(body.party_id)
    if party is None:
        raise HTTPException(404, "Customer not found")
    bal = lg.party_balance(body.party_id)
    if bal <= 0:
        raise HTTPException(400, f"{party['name']} doesn't owe anything right now")
    text, used_model = agent.reminder_text(party["name"], lg.get_shop()["name"], bal, lg.currency(), body.language)
    return {"text": text, "balance_minor": bal, "currency": lg.currency(), "language": body.language,
            "phone": party["phone"], "ai_written": used_model}


@app.post("/stock-count")
async def stock_count(image: UploadFile = File(...), item: str = Form(...), book: Book = Depends(ai_quota)) -> dict:
    mime = image.content_type or "image/jpeg"
    if mime not in IMAGE_TYPES:
        raise HTTPException(400, "Please send a JPEG, PNG or WebP photo")
    data = await _read_limited(image, MAX_IMAGE_BYTES, "Photo")
    return stock.count(data, item)


# ── SayLedger Collect (PayPal sandbox) ────────────────────────────────
from pydantic import BaseModel, Field  # noqa: E402

registry = collect.Registry(default_data_dir())
_paypal: paypal_client.PayPal | None = None


def paypal() -> paypal_client.PayPal:
    global _paypal
    if _paypal is None:
        try:
            _paypal = paypal_client.PayPal()
        except paypal_client.PayPalError as e:
            raise HTTPException(503, str(e)) from e
    return _paypal


class CollectRequestIn(BaseModel):
    party_id: int
    amount_minor: int | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=500)


class InboxConfirmIn(BaseModel):
    party_id: int | None = None


@app.get("/collect/status")
def collect_status(book: Book = Depends(current_book)) -> dict:
    cur = book.ledger.currency()
    import os
    return {"currency": cur, "links_available": cur in collect.SUPPORTED,
            "paypal_configured": bool(os.getenv("PAYPAL_CLIENT_ID") and os.getenv("PAYPAL_CLIENT_SECRET")),
            "environment": os.getenv("PAYPAL_ENV", "sandbox"), "supported": list(collect.SUPPORTED)}


@app.post("/collect/request")
def collect_request(body: CollectRequestIn, book: Book = Depends(current_book)) -> dict:
    return collect.Collect(book.ledger).create_request(paypal(), registry, book.id, body.party_id,
                                                       body.amount_minor, body.note)


@app.get("/collect/requests")
def collect_requests(party_id: int | None = None, book: Book = Depends(current_book)) -> list[dict]:
    return collect.Collect(book.ledger).requests(party_id)


@app.post("/collect/sync")
def collect_sync(book: Book = Depends(current_book)) -> dict:
    new = collect.Collect(book.ledger).sync(paypal())
    return {"new_items": len(new)}


@app.get("/inbox")
def inbox(status: str = "open", book: Book = Depends(current_book)) -> list[dict]:
    if status not in ("open", "confirmed", "dismissed"):
        raise HTTPException(400, "Unknown status")
    return collect.Collect(book.ledger).inbox(status)


@app.post("/inbox/{item_id}/confirm")
def inbox_confirm(item_id: int, body: InboxConfirmIn | None = None, book: Book = Depends(current_book)) -> dict:
    return collect.Collect(book.ledger).confirm(item_id, body.party_id if body else None)


@app.post("/inbox/{item_id}/dismiss")
def inbox_dismiss(item_id: int, book: Book = Depends(current_book)) -> dict:
    return collect.Collect(book.ledger).dismiss(item_id)


@app.post("/paypal/webhook")
async def paypal_webhook(request: Request) -> dict:
    """PayPal calls this. Always 200 quickly (PayPal retries on errors); act only on verified events."""
    import os
    raw = await request.body()
    try:
        ev = collect.parse_event(raw)
    except collect.CollectError:
        return {"ok": False}
    verified = False
    try:
        verified = paypal().verify_webhook(dict(request.headers), raw, os.getenv("PAYPAL_WEBHOOK_ID", ""))
    except (paypal_client.PayPalError, HTTPException):
        verified = False
    first = registry.record_event(ev["event_id"], ev["type"], ev["invoice_id"], verified, raw.decode("utf-8", "replace"))
    if not (first and verified and ev["type"] in collect.HANDLED_EVENTS and ev["invoice_id"]):
        log.info("paypal webhook %s ignored (first=%s verified=%s)", ev["type"], first, verified)
        return {"ok": True}
    book_id = registry.book_for(ev["invoice_id"])
    ledger = books.open_id(book_id) if book_id else None
    if ledger is None:
        return {"ok": True}
    try:
        invoice = paypal().get_invoice(ev["invoice_id"])  # re-fetch: never trust payload amounts
        new = collect.Collect(ledger).reconcile(invoice)
        log.info("paypal webhook %s -> %d inbox item(s)", ev["type"], len(new))
    except paypal_client.PayPalError as e:
        log.warning("paypal re-fetch failed: %s", e)
    return {"ok": True}
