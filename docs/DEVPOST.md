# Devpost submission text — SayLedger Collect

**Project name:** SayLedger Collect by Cortina
**Tagline (≤ 60 chars):** Say it. Send it. Get paid — tabs that collect themselves.
**Built with:** paypal-invoicing-api, paypal-webhooks, meta-muse-spark, python, fastapi, sqlite, flutter, dart, android
**Links:** GitHub repo (public, MIT) · YouTube demo (public) · hosted backend https://cortinainfinity.com/sayledger-collect · APK in GitHub Releases

---

## Inspiration
Around 150,000 corner stores, bodegas, delis and food trucks in the US — and millions of small shops
worldwide — still run customer tabs in a paper notebook. We had already built SayLedger, a voice
credit book: say "Maria took $40 of groceries on credit" and it's in the book. But writing the
credit down is only half the job. The hard part is getting paid: noticing who's overdue, asking
politely, and remembering to cross it off when the money arrives somewhere else.

## What it does
- **"Who should I chase today?"** (voice or text, English or Spanish). An AI agent reads the credit
  book and proposes reminder cards: customer, amount owed, language, and why.
- **One tap → PayPal payment links.** Each ticked card becomes a PayPal invoice for the exact balance
  (or a smaller amount the shopkeeper chooses), with a payer link inside a friendly AI-written message
  the shopkeeper shares on WhatsApp or SMS.
- **The customer pays on PayPal** — in full or in part. No app needed.
- **Payment inbox.** PayPal's webhook arrives, we verify it and re-fetch the invoice, and a card appears:
  *"Maria paid $200 — still owes $304."* One tap on **Confirm** and the book is up to date (oldest credit
  cleared first). Refunds, cancelled links, over-payments (kept as advance) and "wrong customer" are handled.
- **One live link per customer.** A new link cancels the old unpaid one; a part-paid link is reused for
  the remainder.

## How we built it
- **PayPal Invoicing v2 (sandbox):** create → send (`send_to_recipient: false`) → payer-view link;
  get; cancel; record payment (cash at the counter). Partial payments enabled.
- **PayPal Webhooks:** `INVOICING.INVOICE.PAID / REFUNDED / CANCELLED`, signature checked with
  `verify-webhook-signature` on the raw body; duplicate and unverified events are ignored. A
  **Check PayPal now** sync is the fallback.
- **AI:** a Meta `muse-spark-1.3` tool-calling agent (max 6 steps) with read-only ledger tools;
  `muse-voice-transcribe-1.0` for voice commands; Muse Spark for reminder text with an `{AMOUNT}`
  placeholder that our code fills.
- **Backend:** Python FastAPI + SQLite (one private book per install, integer minor units, all maths in
  `ledger.py`). **App:** Flutter (Android). The phone never holds a PayPal or AI key.
- 62 automated tests (fake PayPal + fake model, no network).

## Tools used (AI and PayPal)
- **Meta `muse-spark-1.3`** — the Collect agent. It calls read-only ledger tools (who owes, how much,
  how old, last reminder) in a loop of at most 6 steps and returns reminder cards. Our code checks
  every card against the ledger before it is shown.
- **Meta `muse-voice-transcribe-1.0`** — turns a spoken command ("Who should I chase today?", in English
  or Spanish) into text for the agent.
- **Meta `muse-spark-1.3` (writing)** — writes the friendly reminder message with an `{AMOUNT}`
  placeholder; our code puts in the amount and the PayPal link.
- **PayPal Invoicing v2 (sandbox)** — creates the payment link, takes partial payments, and records
  cash payments. **PayPal Webhooks + verify-webhook-signature** — tell us when a customer pays.
- Code was written with AI coding assistance (Claude Code).

## Trust by design
The AI never moves money and never writes to the book. Amounts always come from the ledger or from
PayPal's re-fetched invoice — never from AI text or the webhook body. The shopkeeper ticks cards before
links are created, shares each message themselves, and confirms each payment.

## Challenges we ran into
- PayPal refuses to cancel a part-paid invoice (HTTP 422), which broke our "one live link per customer"
  rule. We now reuse the part-paid link when its remainder matches what's owed.
- Webhooks need a public HTTPS URL, so local development uses a sync fallback that runs the exact same
  reconciliation code.
- Keeping an LLM agent honest about money: every proposed card is validated (customer exists, owes
  money, amount from the book, max 5 cards, language from a fixed list); anything else is shown as "Skipped".

## Accomplishments that we're proud of
The full loop works end to end in the PayPal sandbox: agent plan → three real invoice links → a $200
partial payment → inbox card → Confirm → balance $304 with ageing updated → the next reminder reuses
the link for the remainder.

**By the numbers (PayPal sandbox, Nov 4–10 2026):**
- 62 backend tests pass (ledger maths, webhook verification, duplicate events, partial/over-payment, refunds).
- 4 real sandbox invoices created and checked against PayPal: full flow $504 → $200 part payment → $304 left.
- 5 live agent commands tested, including one in Spanish and one off-topic request the agent refused.
- The agent never writes an amount: every amount on a card is checked against the ledger before it is shown.

## What we learned
Payments UX is mostly about reconciliation and trust, not the "pay" button. Small details — part
payments, refunds, the wrong customer paying — decide whether a shopkeeper can rely on it.

## What's next
Live PayPal (after review), QR code at the counter, multi-currency books, and a weekly "who to chase"
nudge.

## What is new for this hackathon (honesty note)
SayLedger (the voice credit book) was built Oct 26 – Nov 4 2026 for a separate Meta hackathon.
Everything PayPal and agentic — Invoicing, webhooks, Payment inbox, Collect agent, Get paid screen,
link history — was built during this hackathon (Nov 4 – Nov 12 2026). PayPal sandbox only; all demo
names and amounts are invented. Code was written with AI coding assistance.
The backend is hosted (https://cortinainfinity.com/sayledger-collect) and the PayPal webhook is
registered there. The demo video (https://youtu.be/LG6xQcMYd-s) is a real screen recording of the app
on an Android emulator against that hosted backend, sped up: a person paid $150 of Maria's $504 as a
sandbox buyer (PayPal shows PARTIALLY_PAID, $354 due), PayPal's verified `INVOICING.INVOICE.PAID`
webhook put the card in the Payment inbox, and the shopkeeper confirmed it. The sandbox webhook took
about 3 minutes to arrive; that wait is cut from the video. Narration is an AI voice (Kokoro).
