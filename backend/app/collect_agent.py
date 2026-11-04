"""The Collect agent — Muse Spark plans collection actions with read-only tools.

Loop (max MAX_STEPS): the model replies with ONE JSON object, either
  {"tool": "<name>", "args": {...}}          -> we run the tool in Python and send back the result
  {"final": {"say": "...", "actions": [...]}} -> we validate and turn actions into proposal cards
Tools only READ the ledger / PayPal requests. The only action the agent can propose is
"send_payment_link" for a customer; Python fills the amount from the ledger (or a smaller
amount the shopkeeper said), and nothing happens until the shopkeeper confirms the cards.
"""
from __future__ import annotations

import json
from datetime import date

from . import agent, meta_client
from .collect import SUPPORTED, Collect

MAX_STEPS = 6
MAX_ACTIONS = 5
LANGS = ("English", "Spanish", "Hindi", "Hinglish", "Portuguese", "French", "Bahasa Indonesia", "Swahili")

SYSTEM = """You are the Collect agent inside SayLedger, a credit book for a small shop. You help the
shopkeeper get paid by sending customers PayPal payment links with a polite reminder.
Shop currency: {currency}. Today: {today}. Payment links available: {links}.

You can call these READ-ONLY tools (one per reply):
- list_owing(): customers who owe money, with balance, oldest unpaid days, days since last payment,
  and when we last sent them a payment link.
- customer(name): one customer's balance, open credit and payment-link history.
- find_customer(name): fuzzy name lookup; returns the closest known customer names.

Reply with exactly ONE JSON object and nothing else:
  {{"tool": "list_owing", "args": {{}}}}
  {{"tool": "customer", "args": {{"name": "Maria"}}}}
  {{"tool": "find_customer", "args": {{"name": "Marya"}}}}
or when you are done:
  {{"final": {{"say": "<one or two short sentences for the shopkeeper, in {reply_lang}>",
             "actions": [{{"type": "send_payment_link", "customer": "<exact known name>",
                          "language": "<one of {langs}>", "amount": <number or null>,
                          "why": "<short reason, e.g. 41 days unpaid, no payment since>"}}]}}}}
Rules: only propose send_payment_link for customers who owe money. Use "amount": null to ask for the
full balance; give a number ONLY if the shopkeeper said a specific smaller amount. Never invent
customers or amounts. At most {max_actions} actions. If the request is not about collecting money,
return {{"final": {{"say": "I can help you collect money owed to you.", "actions": []}}}}.
For "who should I chase / remind today" pick customers with the oldest unpaid credit and no recent
payment or link. Pick the reminder language the shopkeeper asked for; else the customer's language
if known; else {default_lang}."""


def _tool_list_owing(ledger, col: Collect, today: date) -> list[dict]:
    last_link: dict[int, str] = {}
    for r in col.requests():
        last_link.setdefault(r["party_id"], r["created_at"][:10])
    out = []
    for c in ledger.collections(today)["customers"][:25]:
        out.append({"name": c["name"], "balance": agent.fmt_money(c["balance_minor"], ledger.currency()),
                    "oldest_unpaid_days": c["oldest_unpaid_days"], "days_since_payment": c["days_since_payment"],
                    "last_payment_link": last_link.get(c["id"]), "language": c["language"]})
    return out


def _tool_customer(ledger, col: Collect, name: str) -> dict:
    known = {p["name"]: p["id"] for p in ledger.parties_with_balances()}
    real, pid = agent.match_party(name, known)
    if pid is None:
        return {"error": f"No customer called {name!r}. Try find_customer."}
    bal = ledger.party_balance(pid)
    reqs = [{"date": r["created_at"][:10], "amount": agent.fmt_money(r["amount_minor"], r["currency"]),
             "status": r["status"]} for r in col.requests(pid)[:5]]
    return {"name": real, "balance": agent.fmt_money(bal, ledger.currency()), "owes": bal > 0,
            "payment_links": reqs}


def _tool_find(ledger, name: str) -> dict:
    import difflib
    names = [p["name"] for p in ledger.parties_with_balances()]
    return {"matches": difflib.get_close_matches(name or "", names, n=3, cutoff=0.5)}


def run_tool(ledger, col: Collect, call: dict, today: date) -> dict | list:
    name, args = call.get("tool"), call.get("args") or {}
    if name == "list_owing":
        return _tool_list_owing(ledger, col, today)
    if name == "customer":
        return _tool_customer(ledger, col, str(args.get("name", "")))
    if name == "find_customer":
        return _tool_find(ledger, str(args.get("name", "")))
    return {"error": f"Unknown tool {name!r}"}


def validate_actions(ledger, final: dict, default_lang: str) -> tuple[str, list[dict], list[str]]:
    """Model's final -> proposal cards. Every number comes from the ledger. Returns (say, cards, dropped)."""
    say = str(final.get("say") or "")[:300]
    cur = ledger.currency()
    known = {p["name"]: p["id"] for p in ledger.parties_with_balances()}
    cards, dropped, seen = [], [], set()
    for a in (final.get("actions") or [])[:MAX_ACTIONS]:
        if not isinstance(a, dict) or a.get("type") != "send_payment_link":
            continue
        real, pid = agent.match_party(a.get("customer") if isinstance(a.get("customer"), str) else None, known)
        if pid is None:
            dropped.append(f"Unknown customer {a.get('customer')!r}")
            continue
        if pid in seen:
            continue
        bal = ledger.party_balance(pid)
        if bal <= 0:
            dropped.append(f"{real} owes nothing")
            continue
        amount, partial = bal, False
        if a.get("amount") not in (None, "", 0):
            try:
                asked = agent.to_minor(a["amount"], cur)
            except agent.AgentError:
                asked = None
            if asked and asked < bal:
                amount, partial = asked, True
        lang = a.get("language") if a.get("language") in LANGS else default_lang
        seen.add(pid)
        cards.append({"type": "send_payment_link", "party_id": pid, "party": real, "amount_minor": amount,
                      "balance_minor": bal, "partial": partial, "currency": cur, "language": lang,
                      "why": str(a.get("why") or "")[:160]})
    return say, cards, dropped


def plan(text: str, ledger, today: date | None = None, chat=None) -> dict:
    """Shopkeeper's words -> {say, cards, steps}. `chat` is injectable for tests."""
    today = today or date.today()
    chat = chat or (lambda msgs: meta_client.chat(msgs, effort="low", max_tokens=2500))
    cur = ledger.currency()
    col = Collect(ledger)
    default_lang = "English"
    system = SYSTEM.format(currency=cur, today=today.isoformat(), links="yes" if cur in SUPPORTED else "no",
                           reply_lang="English", langs=", ".join(LANGS), max_actions=MAX_ACTIONS,
                           default_lang=default_lang)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": text[:1000]}]
    steps: list[dict] = []
    for _ in range(MAX_STEPS):
        reply = chat(msgs)
        try:
            obj = agent._json_object(reply)
        except agent.AgentError:
            msgs += [{"role": "assistant", "content": reply[:2000]},
                     {"role": "user", "content": "Reply with ONE JSON object only."}]
            continue
        if "final" in obj and isinstance(obj["final"], dict):
            say, cards, dropped = validate_actions(ledger, obj["final"], default_lang)
            if cur not in SUPPORTED:
                say = (say + " " if say else "") + f"(PayPal links aren't available for {cur} books.)"
                cards = []
            return {"say": say, "cards": cards, "dropped": dropped, "steps": steps}
        if "tool" in obj:
            result = run_tool(ledger, col, obj, today)
            steps.append({"tool": obj.get("tool"), "args": obj.get("args") or {}})
            msgs += [{"role": "assistant", "content": json.dumps(obj)},
                     {"role": "user", "content": "TOOL RESULT: " + json.dumps(result, ensure_ascii=False)[:6000]}]
            continue
        msgs += [{"role": "assistant", "content": reply[:2000]},
                 {"role": "user", "content": "Use a tool or give final."}]
    raise agent.AgentError("Agent did not finish")


def link_message(party: str, shop: str, amount_minor: int, currency: str, language: str, link: str,
                 partial: bool) -> tuple[str, bool]:
    """Reminder text (model words, our amount) + the PayPal link appended by code, never by the model."""
    text, used = agent.reminder_text(party, shop, amount_minor, currency, language)
    pay = {"Spanish": "Puede pagar aquí con PayPal", "Hindi": "PayPal से यहाँ भुगतान करें",
           "Portuguese": "Pague aqui com PayPal", "French": "Payez ici avec PayPal"}.get(language,
                                                                                         "You can pay here with PayPal")
    return f"{text}\n\n{pay}: {link}", used
