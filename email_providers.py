"""Fail-closed email providers for single-delivery canary execution."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmailSendResult:
    message_id: str | None = None
    correlation_id: str | None = None


class EmailProviderError(RuntimeError):
    """A provider failure containing only response-safe classification data."""

    def __init__(self, reason: str, *, retry_after: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class EmailDeliveryProvider(Protocol):
    name: str

    def send(self, *, sender: str, recipient: str, subject: str, message: str,
             idempotency_key: str) -> EmailSendResult: ...


class DisabledEmailProvider:
    name = "disabled"

    def send(self, **_: str) -> EmailSendResult:
        raise EmailProviderError("email delivery provider is disabled")


class DeterministicMockEmailProvider:
    """Test-only deterministic adapter; it never performs network I/O."""

    name = "mock"
    calls: list[dict[str, str]] = []

    def send(self, **values: str) -> EmailSendResult:
        self.calls.append(values)
        return EmailSendResult(message_id=f"mock-{values['idempotency_key'][:16]}")


class MicrosoftGraphEmailProvider:
    """Microsoft Graph application-permission adapter using client credentials."""

    name = "microsoft_graph"
    _token_cache: dict[tuple[str, str, str], tuple[str, float]] = {}
    _token_lock = threading.Lock()

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self._client_secret = client_secret

    def _redact_client_secret(self, value: str) -> str:
        """Remove plaintext and common direct encodings of the configured secret."""
        secret = self._client_secret
        if not secret:
            return value
        variants = {secret, html.escape(secret), json.dumps(secret)[1:-1], secret.encode().hex()}
        variants.update({base64.b64encode(secret.encode()).decode(),
                         base64.urlsafe_b64encode(secret.encode()).decode()})
        # Identity providers may echo either form encoding (``+`` for spaces) or
        # percent encoding, including an encoding that has itself been encoded.
        encoded = {secret}
        for _ in range(3):
            encoded = ({urllib.parse.quote(item, safe="") for item in encoded} |
                       {urllib.parse.quote_plus(item, safe="") for item in encoded})
            variants.update(encoded)
            variants.update(re.sub(r"%[0-9A-F]{2}", lambda match: match.group(0).lower(), item)
                            for item in encoded)
        redacted = value
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                redacted = redacted.replace(variant, "[REDACTED]")
        return redacted

    def _authentication_error(self, exc: urllib.error.HTTPError) -> EmailProviderError:
        """Build useful Entra diagnostics without retaining credential material."""
        detail = f"microsoft graph authentication failed (HTTP {exc.code}"
        try:
            payload = json.loads(exc.read())
        except (AttributeError, ValueError, TypeError, OSError):
            payload = {}
        error_name = payload.get("error")
        if isinstance(error_name, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", error_name):
            detail += f", {error_name}"
        description = payload.get("error_description")
        if isinstance(description, str):
            description = self._redact_client_secret(description).replace("\r", " ").replace("\n", " ")
            aadsts = re.search(r"AADSTS\d+:\s*.*?(?=\s+(?:Trace ID|Correlation ID|Timestamp):|$)",
                               description, re.IGNORECASE)
            if aadsts:
                detail += f", {aadsts.group(0)[:300]}"
        return EmailProviderError(detail + ")")

    def _access_token(self) -> str:
        # A digest distinguishes credential rotation without retaining another plaintext copy.
        key = (self.tenant_id, self.client_id,
               hashlib.sha256(self._client_secret.encode()).hexdigest())
        with self._token_lock:
            cached = self._token_cache.get(key)
            if cached and cached[1] > time.time() + 60:
                return cached[0]
            data = urllib.parse.urlencode({
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }).encode()
            request = urllib.request.Request(
                f"https://login.microsoftonline.com/{urllib.parse.quote(self.tenant_id, safe='')}/oauth2/v2.0/token",
                data=data, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read())
            except urllib.error.HTTPError as exc:
                raise self._authentication_error(exc) from exc
            except (ValueError, KeyError) as exc:
                raise EmailProviderError("microsoft graph authentication failed") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise EmailProviderError("microsoft graph authentication network failure") from exc
            token = payload.get("access_token")
            if not token:
                raise EmailProviderError("microsoft graph authentication failed")
            expires_in = max(0, int(payload.get("expires_in", 0)))
            self._token_cache[key] = (token, time.time() + expires_in)
            return token

    def send(self, *, sender: str, recipient: str, subject: str, message: str,
             idempotency_key: str) -> EmailSendResult:
        correlation_id = str(uuid.uuid4())
        payload = json.dumps({"message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": message},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
        }, "saveToSentItems": True}).encode()
        request = urllib.request.Request(
            "https://graph.microsoft.com/v1.0/users/"
            f"{urllib.parse.quote(sender, safe='')}/sendMail",
            data=payload, method="POST", headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
                "client-request-id": correlation_id,
                "return-client-request-id": "true",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 202:
                    raise EmailProviderError(f"microsoft graph send failed (HTTP {response.status})")
                safe_correlation = response.headers.get("request-id") or correlation_id
        except urllib.error.HTTPError as exc:
            reasons = {400: "microsoft graph rejected the message or sender",
                       401: "microsoft graph authentication failed",
                       403: "microsoft graph permission or sender authorization failed",
                       404: "microsoft graph sender was not found",
                       429: "microsoft graph rate limit exceeded"}
            reason = reasons.get(exc.code, "microsoft graph service failure" if exc.code >= 500
                                 else "microsoft graph send failed")
            raise EmailProviderError(reason, retry_after=exc.headers.get("Retry-After")) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise EmailProviderError("microsoft graph network failure") from exc
        # sendMail returns 202 with no message resource, so no message ID is claimed.
        return EmailSendResult(correlation_id=safe_correlation)


def configured_provider(name: str, _legacy_webhook_url: str = "", *, tenant_id: str = "",
                        client_id: str = "", client_secret: str = "") -> EmailDeliveryProvider:
    normalized = name.strip().lower()
    if normalized == "mock":
        return DeterministicMockEmailProvider()
    if normalized == "microsoft_graph":
        return MicrosoftGraphEmailProvider(tenant_id, client_id, client_secret)
    return DisabledEmailProvider()
