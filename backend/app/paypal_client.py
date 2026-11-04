"""PayPal REST client for SayLedger Collect — the ONLY module that calls PayPal.

Docs (read Nov 4 2026):
  - Invoicing v2: https://developer.paypal.com/docs/api/invoicing/v2/
    (schema: /api/invoicing/v2/schema.json — fields used below are taken from it)
  - Webhooks verify: https://developer.paypal.com/api/rest/webhooks/rest/
Sandbox only for the hackathon. Credentials come from the environment, never from the phone.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal

import httpx

BASES = {"sandbox": "https://api-m.sandbox.paypal.com", "live": "https://api-m.paypal.com"}
ZERO_DECIMAL = {"JPY", "HUF", "TWD"}  # PayPal currencies without minor units


class PayPalError(RuntimeError):
    pass


def _env() -> str:
    env = os.getenv("PAYPAL_ENV", "sandbox").strip().lower()
    if env != "sandbox":
        raise PayPalError("SayLedger Collect is sandbox-only in this build.")
    return env


def base_url() -> str:
    return BASES[_env()]


def money(amount_minor: int, currency: str) -> dict:
    """Integer minor units -> PayPal money object {currency_code, value} (string)."""
    if currency in ZERO_DECIMAL:
        return {"currency_code": currency, "value": str(amount_minor)}
    return {"currency_code": currency, "value": str((Decimal(amount_minor) / 100).quantize(Decimal("0.01")))}


def to_minor(value: str, currency: str) -> int:
    d = Decimal(value)
    return int(d) if currency in ZERO_DECIMAL else int((d * 100).to_integral_value())


class PayPal:
    def __init__(self, client_id: str | None = None, secret: str | None = None, timeout: float = 30):
        self.client_id = client_id or os.getenv("PAYPAL_CLIENT_ID", "")
        self.secret = secret or os.getenv("PAYPAL_CLIENT_SECRET", "")
        if not self.client_id or not self.secret:
            raise PayPalError("PayPal is not configured (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET).")
        self._token: str | None = None
        self._token_exp = 0.0
        self._http = httpx.Client(base_url=base_url(), timeout=timeout)

    # --- auth -----------------------------------------------------------------------------
    def token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = self._http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                            auth=(self.client_id, self.secret))
        if r.status_code != 200:
            raise PayPalError(f"PayPal auth failed ({r.status_code}).")
        d = r.json()
        self._token, self._token_exp = d["access_token"], time.time() + int(d.get("expires_in", 300))
        return self._token

    def _call(self, method: str, path: str, json: dict | None = None, ok=(200, 201, 202, 204)) -> dict:
        r = self._http.request(method, path, json=json, headers={
            "Authorization": f"Bearer {self.token()}", "Content-Type": "application/json",
            "Prefer": "return=representation"})
        if r.status_code not in ok:
            try:
                msg = r.json().get("message") or r.json().get("name")
            except Exception:
                msg = r.text[:200]
            raise PayPalError(f"PayPal {method} {path} -> {r.status_code}: {msg}")
        return r.json() if r.content else {}

    # --- invoicing ------------------------------------------------------------------------
    def create_invoice(self, *, amount_minor: int, currency: str, customer_name: str, shop_name: str,
                       note: str = "", customer_email: str | None = None, allow_partial: bool = True,
                       reference: str | None = None) -> dict:
        """Draft invoice for exactly the amount owed (one line item)."""
        body: dict = {
            "detail": {"currency_code": currency, "note": note[:4000] if note else "",
                       "reference": reference or "", "memo": "Created by SayLedger Collect"},
            "invoicer": {"business_name": shop_name[:300]},
            "items": [{"name": f"Balance due — {shop_name}"[:200], "quantity": "1",
                       "unit_amount": money(amount_minor, currency)}],
            "configuration": {"partial_payment": {"allow_partial_payment": allow_partial},
                              "allow_tip": False},
        }
        recipient: dict = {"name": {"full_name": customer_name[:140]}}
        if customer_email:
            recipient["email_address"] = customer_email
        body["primary_recipients"] = [{"billing_info": recipient}]
        return self._call("POST", "/v2/invoicing/invoices", body)

    def send_invoice(self, invoice_id: str, notify_recipient: bool = False) -> dict:
        """Moves the invoice out of draft and returns the payer link {href, rel, method}.
        notify_recipient=False: PayPal does not email anyone; the shopkeeper shares the link."""
        return self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/send",
                          {"send_to_recipient": notify_recipient, "send_to_invoicer": False})

    def get_invoice(self, invoice_id: str) -> dict:
        return self._call("GET", f"/v2/invoicing/invoices/{invoice_id}")

    def cancel_invoice(self, invoice_id: str) -> None:
        self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/cancel",
                   {"send_to_recipient": False, "send_to_invoicer": False})

    def qr_code(self, invoice_id: str, width: int = 400) -> bytes:
        r = self._http.post(f"/v2/invoicing/invoices/{invoice_id}/generate-qr-code",
                            json={"width": width, "height": width},
                            headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"})
        if r.status_code != 200:
            raise PayPalError(f"PayPal QR -> {r.status_code}")
        return r.content

    # --- webhooks -------------------------------------------------------------------------
    def verify_webhook(self, headers: dict, raw_body: bytes, webhook_id: str) -> bool:
        """Postback verification. raw_body is inserted verbatim (docs: never re-serialize)."""
        h = {k.lower(): v for k, v in headers.items()}
        need = ["paypal-transmission-id", "paypal-transmission-time", "paypal-cert-url",
                "paypal-auth-algo", "paypal-transmission-sig"]
        if not webhook_id or any(not h.get(n) for n in need):
            return False
        import json as _json
        meta = {"transmission_id": h["paypal-transmission-id"], "transmission_time": h["paypal-transmission-time"],
                "cert_url": h["paypal-cert-url"], "auth_algo": h["paypal-auth-algo"],
                "transmission_sig": h["paypal-transmission-sig"], "webhook_id": webhook_id}
        payload = _json.dumps(meta)[:-1] + ', "webhook_event": ' + raw_body.decode("utf-8") + "}"
        r = self._http.post("/v1/notifications/verify-webhook-signature", content=payload.encode(),
                            headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"})
        return r.status_code == 200 and r.json().get("verification_status") == "SUCCESS"


def payer_link(invoice: dict, send_result: dict | None = None) -> str | None:
    """The link a customer opens to pay. Prefer the /send response, else invoice links."""
    if send_result and send_result.get("href"):
        return send_result["href"]
    for l in invoice.get("links", []):
        if l.get("rel") in ("payer-view", "payer_view"):
            return l["href"]
    return None
