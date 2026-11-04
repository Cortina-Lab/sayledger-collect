"""SayLedger Collect: requests, reconciliation, inbox and the webhook route (fake PayPal)."""
import json

import pytest
from fastapi.testclient import TestClient

from app import collect
from app.ledger import Ledger


class FakePayPal:
    def __init__(self):
        self.invoices = {}
        self.n = 0
        self.verify_ok = True

    def create_invoice(self, *, amount_minor, currency, customer_name, shop_name, note="", customer_email=None,
                       allow_partial=True, reference=None):
        self.n += 1
        iid = f"INV2-TEST-{self.n}"
        self.invoices[iid] = {"id": iid, "status": "DRAFT", "amount": {"value": f"{amount_minor / 100:.2f}"},
                              "payments": {}, "refunds": {}, "links": []}
        return self.invoices[iid]

    def send_invoice(self, iid, notify_recipient=False):
        self.invoices[iid]["status"] = "UNPAID"
        return {"href": f"https://www.sandbox.paypal.com/invoice/p/#{iid}", "rel": "payer-view"}

    def get_invoice(self, iid):
        return json.loads(json.dumps(self.invoices[iid]))

    def pay(self, iid, value, status="PARTIALLY_PAID", method="PAYPAL"):
        inv = self.invoices[iid]
        prev = float(inv["payments"].get("paid_amount", {}).get("value", "0"))
        inv["payments"] = {"paid_amount": {"currency_code": "USD", "value": f"{prev + value:.2f}"},
                           "transactions": [{"type": "PAYPAL", "method": method, "payment_id": f"P{self.n}",
                                             "amount": {"value": f"{value:.2f}"}}]}
        inv["status"] = status

    def refund(self, iid, value):
        self.invoices[iid]["refunds"] = {"refund_amount": {"currency_code": "USD", "value": f"{value:.2f}"}}
        self.invoices[iid]["status"] = "PARTIALLY_REFUNDED"

    def verify_webhook(self, headers, raw, webhook_id):
        return self.verify_ok


@pytest.fixture
def shop(tmp_path):
    lg = Ledger(tmp_path / "b.db")
    lg.set_shop("Harbor Market", "USD")
    maria = lg.add_party("Maria")
    lg.add_entry("credit_given", 14250, maria["id"], note="groceries")
    return lg, maria["id"], collect.Registry(tmp_path), FakePayPal()


def test_request_is_for_the_ledger_balance(shop):
    lg, pid, reg, pp = shop
    req = collect.Collect(lg).create_request(pp, reg, "book1", pid)
    assert req["amount_minor"] == 14250 and req["link_url"].endswith(req["invoice_id"])
    assert reg.book_for(req["invoice_id"]) == "book1"


def test_request_rules(shop, tmp_path):
    lg, pid, reg, pp = shop
    c = collect.Collect(lg)
    with pytest.raises(collect.CollectError):
        c.create_request(pp, reg, "b", pid, amount_minor=20000)  # more than owed
    other = lg.add_party("Jake")
    with pytest.raises(collect.CollectError):
        c.create_request(pp, reg, "b", other["id"])  # owes nothing
    inr = Ledger(tmp_path / "inr.db")
    inr.set_shop("Demo Shop", "INR")
    r = inr.add_party("Ramesh")
    inr.add_entry("credit_given", 18000, r["id"])
    with pytest.raises(collect.CollectError, match="INR"):
        collect.Collect(inr).create_request(pp, reg, "b", r["id"])


def test_partial_then_rest_then_confirm(shop):
    lg, pid, reg, pp = shop
    c = collect.Collect(lg)
    req = c.create_request(pp, reg, "b", pid)
    pp.pay(req["invoice_id"], 100.00)
    items = c.reconcile(pp.get_invoice(req["invoice_id"]))
    assert [i["amount_minor"] for i in items] == [10000]
    assert c.reconcile(pp.get_invoice(req["invoice_id"])) == []  # same state twice -> nothing new
    open_ = c.inbox()
    assert open_[0]["proposal"]["balance_after_minor"] == 4250
    assert lg.party_balance(pid) == 14250  # nothing saved before confirm
    res = c.confirm(open_[0]["id"])
    assert res["balance_minor"] == 4250 and res["entry"]["source"] == "paypal"
    with pytest.raises(collect.CollectError):
        c.confirm(open_[0]["id"])  # can't confirm twice
    pp.pay(req["invoice_id"], 42.50, status="PAID")
    c.sync(pp)
    c.confirm(c.inbox()[0]["id"])
    assert lg.party_balance(pid) == 0


def test_overpayment_becomes_advance_and_refund_reverses(shop):
    lg, pid, reg, pp = shop
    c = collect.Collect(lg)
    req = c.create_request(pp, reg, "b", pid)
    pp.pay(req["invoice_id"], 162.50, status="PAID")
    item = c.reconcile(pp.get_invoice(req["invoice_id"]))[0]
    assert c.inbox()[0]["proposal"]["advance_minor"] == 2000
    c.confirm(item["id"])
    assert lg.party_balance(pid) == -2000
    pp.refund(req["invoice_id"], 15.00)
    ref = [i for i in c.reconcile(pp.get_invoice(req["invoice_id"])) if i["kind"] == "refund"][0]
    c.confirm(ref["id"])
    assert lg.party_balance(pid) == -500


def test_dismiss_saves_nothing(shop):
    lg, pid, reg, pp = shop
    c = collect.Collect(lg)
    req = c.create_request(pp, reg, "b", pid)
    pp.pay(req["invoice_id"], 10.00)
    item = c.reconcile(pp.get_invoice(req["invoice_id"]))[0]
    c.dismiss(item["id"])
    assert lg.party_balance(pid) == 14250 and c.inbox() == []


def test_event_parse():
    ev = collect.parse_event(json.dumps({"id": "WH-1", "event_type": "INVOICING.INVOICE.PAID",
                                         "resource": {"invoice": {"id": "INV2-X"}}}).encode())
    assert ev == {"event_id": "WH-1", "type": "INVOICING.INVOICE.PAID", "invoice_id": "INV2-X"}
    with pytest.raises(collect.CollectError):
        collect.parse_event(b"not json")


def test_webhook_route_verified_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("SAYLEDGER_DATA", str(tmp_path))
    import importlib
    import app.main as main
    main = importlib.reload(main)
    pp = FakePayPal()
    monkeypatch.setattr(main, "_paypal", pp)
    c = TestClient(main.app)
    tok = c.post("/books", json={"shop_name": "Harbor Market", "currency": "USD"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    pid = c.post("/entries/confirm", headers=h, json={"proposals": [
        {"party_name": "Maria", "party_id": None, "kind": "credit_given", "amount_minor": 14250, "note": "", "doubt": []}],
        "heard": "", "source": "manual"}).json()
    parties = c.get("/parties", headers=h).json()
    pid = parties["parties"][0]["id"]
    req = c.post("/collect/request", headers=h, json={"party_id": pid}).json()
    pp.pay(req["invoice_id"], 100.00)
    body = json.dumps({"id": "WH-9", "event_type": "INVOICING.INVOICE.PAID",
                       "resource": {"invoice": {"id": req["invoice_id"]}}}).encode()
    assert c.post("/paypal/webhook", content=body).json() == {"ok": True}
    assert c.post("/paypal/webhook", content=body).json() == {"ok": True}  # duplicate delivery
    inbox = c.get("/inbox", headers=h).json()
    assert len(inbox) == 1 and inbox[0]["amount_minor"] == 10000
    pp.verify_ok = False  # forged event: stored, never acted on
    pp.pay(req["invoice_id"], 10.00)
    forged = json.dumps({"id": "WH-10", "event_type": "INVOICING.INVOICE.PAID",
                         "resource": {"invoice": {"id": req["invoice_id"]}}}).encode()
    c.post("/paypal/webhook", content=forged)
    assert len(c.get("/inbox", headers=h).json()) == 1
    r = c.post(f"/inbox/{inbox[0]['id']}/confirm", headers=h).json()
    assert r["balance_minor"] == 4250
