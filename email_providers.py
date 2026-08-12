"""Fail-closed email providers for single-delivery canary execution."""
from __future__ import annotations

import json
import hashlib
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

    def _authentication_error(self, *, status: int | None = None,
                              payload: object = None, raw_body: str = "") -> EmailProviderError:
        """Build an actionable OAuth error from an allowlist of Microsoft fields."""
        details: list[str] = []
        if status is not None:
            details.append(f"HTTP {status}")

        error = description = ""
        if isinstance(payload, dict):
            error = payload.get("error") if isinstance(payload.get("error"), str) else ""
            description = (payload.get("error_description")
                           if isinstance(payload.get("error_description"), str) else "")

        def redact(value: str) -> str:
            # OAuth descriptions are useful, but Entra can echo submitted values. Redact
            # both the configured secret and common credential-shaped values before the
            # reason can reach logs, API audit rows, or delivery state.
            if self._client_secret:
                value = value.replace(self._client_secret, "[REDACTED]")
            value = re.sub(
                r"(?i)(access_token|refresh_token|client_secret|client_assertion|password|"
                r"api[_-]?key|credential|token|secret)"
                r"(\s*[:=]\s*)([^\s,;\"']+|\"[^\"]*\"|'[^']*')",
                lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value,
            )
            value = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;\"']+",
                           lambda match: f"{match.group(1)} [REDACTED]", value)
            value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
                           "[REDACTED]", value)
            return value.replace("\r", " ").replace("\n", " ")

        if error:
            details.append(f"error={redact(error)}")
        if description:
            details.append(f"error_description={redact(description)}")
        elif raw_body:
            # Never retain an unstructured response body; only its non-secret AADSTS code.
            aadsts = re.search(r"\bAADSTS\d+\b", raw_body, flags=re.IGNORECASE)
            if aadsts:
                details.append(f"error_code={aadsts.group(0).upper()}")
        suffix = f" ({'; '.join(details)})" if details else ""
        return EmailProviderError(f"microsoft graph authentication failed{suffix}")

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
                    raw_body = response.read().decode("utf-8", errors="replace")
                    try:
                        payload = json.loads(raw_body)
                    except (ValueError, TypeError) as exc:
                        raise self._authentication_error(
                            status=response.status, raw_body=raw_body) from exc
            except urllib.error.HTTPError as exc:
                raw_body = exc.read().decode("utf-8", errors="replace")
                try:
                    error_payload = json.loads(raw_body)
                except (ValueError, TypeError):
                    error_payload = None
                raise self._authentication_error(
                    status=exc.code, payload=error_payload, raw_body=raw_body) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise EmailProviderError("microsoft graph authentication network failure") from exc
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not token:
                raise self._authentication_error(
                    status=response.status, payload=payload, raw_body=raw_body)
            try:
                expires_in = max(0, int(payload.get("expires_in", 0)))
            except (TypeError, ValueError) as exc:
                raise self._authentication_error(status=response.status) from exc
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
