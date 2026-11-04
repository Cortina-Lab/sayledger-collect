from app.paypal_client import money, to_minor, payer_link


def test_money_roundtrip():
    assert money(14250, "USD") == {"currency_code": "USD", "value": "142.50"}
    assert money(5, "USD")["value"] == "0.05"
    assert money(1200, "JPY")["value"] == "1200"
    assert to_minor("142.50", "USD") == 14250 and to_minor("1200", "JPY") == 1200


def test_payer_link_prefers_send_result():
    inv = {"links": [{"rel": "payer-view", "href": "https://x/inv"}]}
    assert payer_link(inv, {"href": "https://y/send", "rel": "payer-view"}) == "https://y/send"
    assert payer_link(inv) == "https://x/inv"
    assert payer_link({"links": []}) is None
