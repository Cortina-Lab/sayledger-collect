# SayLedger Collect — Demo video script (< 3:00, English, public on YouTube)

**Format:** phone screen recording (scrcpy, Android emulator or a real phone) +
English voiceover + burned-in captions.
**Pace:** ~140 words/min. Voiceover ≈ 330 words ≈ 2:25; screen waits fill to ~2:50.
**Rules:** PayPal sandbox only · invented data ("Harbor Market", USD) · no third-party music or
logos except PayPal's name where we describe the integration · say what was built during the event.

Legend: 🎙 voiceover · 📱 screen · 🏷 caption

---

## 0:00 – 0:15 · The problem
📱 Title card: **SayLedger Collect** — *Say it. Send it. Get paid.* → photo of a paper tab notebook (invented names).

🎙 "Corner stores and food trucks still run customer tabs in a paper notebook. Writing the credit
down is easy. Getting paid is the hard part: knowing who's overdue, asking politely, and remembering
to cross it off when the money shows up."

---

## 0:15 – 0:45 · "Who should I chase today?"
📱 Home of **Harbor Market** ($) → **Get paid** → hold mic: *"Who should I chase today?"* →
agent works → 3 plan cards (customer, amount, language, reason).

🎙 "SayLedger Collect starts with a question. A Muse Spark agent reads the credit book with read-only
tools and proposes who to chase, and why. Every amount on these cards comes from the ledger, not from
the AI — our code checks each card before it's shown."

🏷 *muse-spark-1.3 tool loop · amounts validated against the book*

---

## 0:45 – 1:10 · PayPal links, one tap
📱 Keep 3 cards ticked → **Create 3 PayPal links** → sent cards with message + link → **Share** → share sheet.

🎙 "One tap creates a real PayPal invoice for each customer, through the Invoicing API, and gives
back a payment link. The agent writes a friendly reminder — in Spanish for Maria — and the shopkeeper
shares it on WhatsApp or SMS. The AI never sends anything by itself."

🏷 *PayPal Invoicing v2 (sandbox) · create → send → payer link*

---

## 1:10 – 1:40 · The customer pays
📱 Open the link in a browser → PayPal sandbox invoice page → log in with the sandbox **Personal**
buyer (a person logs in — never automated) → pay **$200 of $504** (partial) → "Payment successful".

🎙 "Maria opens the link. She doesn't need our app — just PayPal. She can pay part of it today:
two hundred dollars of five hundred and four."

🏷 *Partial payments allowed · we never see card data*

---

## 1:40 – 2:15 · Payment inbox
📱 Back in the app: banner **"1 new payment"** (or **Check PayPal now**) → Payment inbox card
*"Maria paid $200 — part payment, still owes $304"* + "Reported by PayPal · INV… · PARTIALLY_PAID" → **Confirm**.

🎙 "PayPal tells our backend with a webhook. We verify the signature with PayPal, then fetch the
invoice again and use PayPal's own paid amount. A card lands in the Payment inbox — but nothing
touches the book until the shopkeeper taps Confirm."

🏷 *Webhook signature verified · amount re-fetched from PayPal · you confirm*

---

## 2:15 – 2:35 · The book is up to date
📱 Customer page for Maria: balance **$304**, entry with PayPal icon, link history; tap **Add PayPal
payment link** in a reminder → it reuses the open link for the remaining $304.

🎙 "The payment clears her oldest credit first, so the ageing updates too. And next time, the same
link asks only for what's left."

---

## 2:35 – 2:55 · Close
📱 Architecture slide (the loop diagram from the README) → title card + repo URL.

🎙 "SayLedger Collect: the AI proposes, PayPal moves the money, and the shopkeeper stays in control.
SayLedger's voice credit book existed before; everything PayPal and agentic you saw was built for
this hackathon, in the PayPal sandbox, with invented demo data."

🏷 *Built for the PayPal AI Hackathon 2026 · Made by Lexcore Enterprises · Cortina*

---

### Shot checklist (before recording)
- [ ] Backend reachable from the phone (hosted URL, or `adb reverse tcp:8078 tcp:8078`)
- [ ] Fresh "Harbor Market" USD book seeded: `.venv/bin/python -m scripts.seed_demo data/books/<id>.db --currency USD`
- [ ] Sandbox **Personal (US)** buyer account created (developer.paypal.com → Sandbox accounts)
- [ ] Webhook registered (needs public HTTPS) — otherwise show **Check PayPal now** and change the 1:40 line to "…or the shopkeeper taps Check PayPal now"
- [ ] Notifications muted, emulator clock/battery clean, no real names on screen
