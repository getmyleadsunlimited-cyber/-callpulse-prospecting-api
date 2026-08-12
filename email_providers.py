"""Fail-closed email provider boundary for single-delivery canary execution."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmailSendResult:
    message_id: str | None


class EmailDeliveryProvider(Protocol):
    name: str

    def send(self, *, sender: str, recipient: str, message: str,
             idempotency_key: str) -> EmailSendResult: ...


class DisabledEmailProvider:
    name = "disabled"

    def send(self, **_: str) -> EmailSendResult:
        raise RuntimeError("No email delivery provider is configured")


class WebhookEmailProvider:
    """Existing operator HTTPS adapter, now behind the explicit provider interface."""

    name = "webhook"

    def __init__(self, url: str):
        if not url.startswith("https://"):
            raise ValueError("CALLPULSE_DELIVERY_WEBHOOK must be an HTTPS URL")
        self.url = url

    def send(self, *, sender: str, recipient: str, message: str,
             idempotency_key: str) -> EmailSendResult:
        payload = json.dumps({
            "from": sender, "to": recipient, "message": message,
            "idempotency_key": idempotency_key,
        }).encode()
        request = urllib.request.Request(self.url, data=payload, method="POST", headers={
            "Content-Type": "application/json", "Idempotency-Key": idempotency_key,
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Delivery adapter returned HTTP {response.status}")
            # Only accept the non-secret provider identifier, if the adapter supplies it.
            message_id = response.headers.get("X-Provider-Message-Id")
        return EmailSendResult(message_id=message_id)


class DeterministicMockEmailProvider:
    """Test-only deterministic adapter; it never performs network I/O."""

    name = "mock"
    calls: list[dict[str, str]] = []

    def send(self, *, sender: str, recipient: str, message: str,
             idempotency_key: str) -> EmailSendResult:
        self.calls.append({"sender": sender, "recipient": recipient,
                           "message": message, "idempotency_key": idempotency_key})
        return EmailSendResult(message_id=f"mock-{idempotency_key[:16]}")


def configured_provider(name: str, webhook_url: str) -> EmailDeliveryProvider:
    normalized = name.strip().lower()
    if normalized == "mock":
        return DeterministicMockEmailProvider()
    if normalized == "webhook":
        return WebhookEmailProvider(webhook_url)
    return DisabledEmailProvider()
