"""Collect agent: tool loop + validation, with a scripted model (no network)."""
import json
from datetime import date

import pytest

from app import collect_agent
from app.agent import AgentError
from app.ledger import Ledger


@pytest.fixture
def lg(tmp_path):
    lg = Ledger(tmp_path / "b.db")
    lg.set_shop("Harbor Market", "USD")
    for name, amt in (("Maria", 14250), ("Jake", 3000), ("Priya", 9900)):
        p = lg.add_party(name)
        lg.add_entry("credit_given", amt, p["id"], created_at="2026-09-20T10:00:00+00:00")
    lg.add_party("Luis")  # owes nothing
    return lg


def scripted(*replies):
    calls = []

    def chat(msgs):
        calls.append(msgs)
        return replies[len(calls) - 1]
    chat.calls = calls
    return chat


def final(actions, say="Done."):
    return json.dumps({"final": {"say": say, "actions": actions}})


def test_tool_loop_then_cards(lg):
    chat = scripted(json.dumps({"tool": "list_owing", "args": {}}),
                    final([{"type": "send_payment_link", "customer": "Maria", "language": "Spanish",
                            "amount": None, "why": "45 days unpaid"}]))
    out = collect_agent.plan("who should I chase today? in Spanish", lg, date(2026, 11, 4), chat)
    assert out["steps"] == [{"tool": "list_owing", "args": {}}]
    tool_result = chat.calls[1][-1]["content"]
    assert "Maria" in tool_result and "$142.50" in tool_result
    assert out["cards"] == [{"type": "send_payment_link", "party_id": 1, "party": "Maria", "amount_minor": 14250,
                             "balance_minor": 14250, "partial": False, "currency": "USD", "language": "Spanish",
                             "why": "45 days unpaid"}]


def test_amount_never_above_balance_and_smaller_kept(lg):
    chat = scripted(final([
        {"type": "send_payment_link", "customer": "maria", "amount": 99999, "language": "English"},
        {"type": "send_payment_link", "customer": "Priya", "amount": "50", "language": "English"}]))
    cards = collect_agent.plan("send links", lg, date(2026, 11, 4), chat)["cards"]
    assert cards[0]["amount_minor"] == 14250 and not cards[0]["partial"]
    assert cards[1]["amount_minor"] == 5000 and cards[1]["partial"]


def test_invented_or_paid_up_customers_dropped(lg):
    chat = scripted(final([{"type": "send_payment_link", "customer": "Zorro"},
                           {"type": "send_payment_link", "customer": "Luis"},
                           {"type": "send_payment_link", "customer": "Jake", "language": "Klingon"},
                           {"type": "send_payment_link", "customer": "Jake"},
                           {"type": "wire_money", "customer": "Jake"}]))
    out = collect_agent.plan("x", lg, date(2026, 11, 4), chat)
    assert [c["party"] for c in out["cards"]] == ["Jake"] and out["cards"][0]["language"] == "English"
    assert any("Zorro" in d for d in out["dropped"]) and any("Luis" in d for d in out["dropped"])


def test_bad_json_then_recovers_and_unknown_tool(lg):
    chat = scripted("sure!", json.dumps({"tool": "delete_everything", "args": {}}), final([]))
    out = collect_agent.plan("hi", lg, date(2026, 11, 4), chat)
    assert out["cards"] == [] and out["steps"][0]["tool"] == "delete_everything"
    assert "Unknown tool" in chat.calls[2][-1]["content"]


def test_gives_up_after_max_steps(lg):
    chat = scripted(*[json.dumps({"tool": "list_owing", "args": {}})] * collect_agent.MAX_STEPS)
    with pytest.raises(AgentError):
        collect_agent.plan("loop", lg, date(2026, 11, 4), chat)


def test_inr_book_gets_no_cards(tmp_path):
    lg = Ledger(tmp_path / "inr.db")
    lg.set_shop("Demo Shop", "INR")
    p = lg.add_party("Ramesh")
    lg.add_entry("credit_given", 18000, p["id"])
    out = collect_agent.plan("send Ramesh a link", lg, date(2026, 11, 4),
                             scripted(final([{"type": "send_payment_link", "customer": "Ramesh"}])))
    assert out["cards"] == [] and "INR" in out["say"]


def test_link_appended_by_code(monkeypatch):
    monkeypatch.setattr(collect_agent.agent, "reminder_text", lambda *a: ("Hola Maria, $142.50 pendiente.", True))
    text, used = collect_agent.link_message("Maria", "Harbor Market", 14250, "USD", "Spanish",
                                            "https://www.sandbox.paypal.com/invoice/p/#INV2-X", False)
    assert text.endswith("Puede pagar aquí con PayPal: https://www.sandbox.paypal.com/invoice/p/#INV2-X") and used
