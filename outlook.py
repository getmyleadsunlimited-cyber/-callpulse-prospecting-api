"""Microsoft Graph sending boundary.

A successful result is returned only after Graph accepts the message. Delivery state must
then be recorded through recordDelivery with the caller's stable idempotency key.
"""
import os
import httpx

GRAPH_BASE_URL = os.getenv("MICROSOFT_GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")

def configured() -> bool:
    return bool(os.getenv("MICROSOFT_GRAPH_ACCESS_TOKEN") and os.getenv("MICROSOFT_GRAPH_SENDER"))

def send_mail(*, recipient: str, subject: str, body: str) -> dict:
    token = os.getenv("MICROSOFT_GRAPH_ACCESS_TOKEN")
    sender = os.getenv("MICROSOFT_GRAPH_SENDER")
    if not token or not sender:
        raise RuntimeError("Microsoft Graph is not configured")
    payload = {"message":{"subject":subject,"body":{"contentType":"Text","content":body},"toRecipients":[{"emailAddress":{"address":recipient}}]},"saveToSentItems":True}
    response = httpx.post(f"{GRAPH_BASE_URL}/users/{sender}/sendMail", headers={"Authorization":f"Bearer {token}"}, json=payload, timeout=30)
    response.raise_for_status()
    # sendMail normally returns 202 without a message ID; never invent one.
    return {"accepted": response.status_code == 202, "status_code": response.status_code, "provider_message_id": response.headers.get("request-id")}
