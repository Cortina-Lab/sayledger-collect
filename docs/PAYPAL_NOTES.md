# PayPal facts verified from official docs (Nov 4 2026)

Sources: developer.paypal.com/docs/api/invoicing/v2/ · /api/invoicing/v2/schema.json (saved here) ·
/api/rest/webhooks/event-names/ · /api/rest/webhooks/rest/

## Invoicing v2 (chosen: approach A)
- POST /v2/invoicing/invoices -> 201 invoice (draft). invoice fields: id, status, detail, invoicer,
  primary_recipients, items, configuration, amount, due_amount, payments, refunds, links.
- detail: currency_code, reference, note, invoice_number, invoice_date, payment_term, memo ...
- items[]: required name, quantity (string), unit_amount {currency_code, value}.
- primary_recipients[].billing_info: name, email_address, language ...
- configuration.partial_payment: { allow_partial_payment: bool, minimum_amount_due: amount }  -> partial payments supported.
- POST /v2/invoicing/invoices/{id}/send -> 200 {href: https://www.sandbox.paypal.com/invoice/p/#INV2-..., rel: payer-view} — CONFIRMED in sandbox Nov 4 (send_to_recipient false = no email).
- GET /v2/invoicing/invoices/{id}; payments: {paid_amount, transactions[payment_detail: type, payment_id, payment_date, method, amount]}.
- Also: /remind, /cancel, /refunds, /generate-qr-code (QR for counter!), /search-invoices.

## Webhooks
- INVOICING.INVOICE.PAID (fully paid, partially paid, pending) · INVOICING.INVOICE.REFUNDED (full+partial)
  · INVOICING.INVOICE.CANCELLED · INVOICING.INVOICE.UPDATED
- Captures: PAYMENT.CAPTURE.COMPLETED / PAYMENT.CAPTURE.REFUNDED. CHECKOUT.ORDER.COMPLETED = marketplaces only.
- Verify: POST https://api.sandbox.paypal.com/v1/notifications/verify-webhook-signature with
  transmission_id/time, cert_url, auth_algo, transmission_sig (from paypal-transmission-* headers),
  webhook_id, webhook_event (EXACT raw payload — do not re-serialize). Response field not shown on page
  (expect verification_status SUCCESS/FAILURE — confirm in sandbox).
- Webhooks simulator mock events can NOT be verified by postback -> test with real sandbox payments.

## Still to confirm in sandbox (Phase 1)
- OAuth token endpoint (expected POST /v1/oauth2/token, client_credentials) and sandbox base api-m.sandbox.paypal.com.
- send response rel name; whether invoicer email/merchant info required on create.
