"""The one place this backend sends email.

Used for password-reset links. Nothing else mails today; if a later feature needs a message, add a
function here rather than constructing a client in a router or in `auth.py`.

Two transports, one at a time:

- **Resend** (HTTPS) when `RESEND_API_KEY` is set — the Railway-friendly path. SMTP ports on a
  container host are often blocked or unroutable.
- **SMTP** when `SMTP_HOST` is set and Resend is not.

`MAIL_FROM` is required for either. Without a transport, callers fall back (password reset logs
the link for local development). Tests never hit a live provider: patch `send_email` or this
module's `_client`, same rule as `firecrawl_client`.

Docs: https://resend.com/docs/api-reference/emails/send-email
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

RESEND_API_KEY_ENV = "RESEND_API_KEY"
MAIL_FROM_ENV = "MAIL_FROM"
SMTP_HOST_ENV = "SMTP_HOST"
SMTP_PORT_ENV = "SMTP_PORT"
SMTP_USER_ENV = "SMTP_USER"
SMTP_PASSWORD_ENV = "SMTP_PASSWORD"
SMTP_STARTTLS_ENV = "SMTP_STARTTLS"

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 20.0
_DEFAULT_SMTP_PORT = 587


class MailError(Exception):
    """A mail call did not deliver."""


class MailNotConfigured(MailError):
    """Neither Resend nor SMTP is configured. Callers treat this as "log instead", not a failure."""


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _mail_from() -> str:
    return _env(MAIL_FROM_ENV)


def is_configured() -> bool:
    """True when a real send would be attempted — a key/host *and* a From address."""
    if not _mail_from():
        return False
    return bool(_env(RESEND_API_KEY_ENV) or _env(SMTP_HOST_ENV))


def reset_client() -> None:
    """Drop the cached HTTP client. For tests and for a key rotated without a restart."""
    clear = getattr(_client, "cache_clear", None)
    if clear is not None:
        clear()


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    key = _env(RESEND_API_KEY_ENV)
    if not key:
        raise MailNotConfigured(
            f"{RESEND_API_KEY_ENV} is not set, so Resend is unavailable. Add it to the backend .env."
        )
    return httpx.Client(
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=_TIMEOUT_SECONDS,
    )


def send_email(*, to: str, subject: str, text: str, html: str | None = None) -> None:
    """Deliver one message. Raises `MailNotConfigured` or `MailError`; never returns a provider id
    the rest of the app would have to store."""
    if not is_configured():
        raise MailNotConfigured(
            f"Set {MAIL_FROM_ENV} and either {RESEND_API_KEY_ENV} or {SMTP_HOST_ENV} to send mail."
        )
    if _env(RESEND_API_KEY_ENV):
        _send_resend(to=to, subject=subject, text=text, html=html)
        return
    _send_smtp(to=to, subject=subject, text=text, html=html)


def _send_resend(*, to: str, subject: str, text: str, html: str | None) -> None:
    payload: dict[str, str | list[str]] = {
        "from": _mail_from(),
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html
    try:
        response = _client().post(_RESEND_URL, json=payload)
        response.raise_for_status()
    except MailNotConfigured:
        raise
    except Exception as exc:
        raise _fail("Resend could not send the email", exc) from exc


def _send_smtp(*, to: str, subject: str, text: str, html: str | None) -> None:
    host = _env(SMTP_HOST_ENV)
    port = _smtp_port()
    user = _env(SMTP_USER_ENV)
    password = _env(SMTP_PASSWORD_ENV)
    starttls = _env(SMTP_STARTTLS_ENV).lower() not in {"0", "false", "no"}

    message = EmailMessage()
    message["From"] = _mail_from()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(host, port, timeout=_TIMEOUT_SECONDS) as smtp:
            smtp.ehlo()
            if starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
    except Exception as exc:
        raise _fail("SMTP could not send the email", exc) from exc


def _smtp_port() -> int:
    raw = _env(SMTP_PORT_ENV)
    if not raw:
        return _DEFAULT_SMTP_PORT
    try:
        port = int(raw)
    except ValueError:
        raise MailError(f"{SMTP_PORT_ENV} must be a number, not {raw!r}.") from None
    if not (1 <= port <= 65535):
        raise MailError(f"{SMTP_PORT_ENV} is out of range.")
    return port


def _fail(what: str, exc: Exception) -> MailError:
    if isinstance(exc, MailError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        detail = f"the provider did not answer within {int(_TIMEOUT_SECONDS)}s"
    elif isinstance(exc, httpx.ConnectError):
        detail = "could not reach the provider from this server"
    elif isinstance(exc, httpx.HTTPStatusError):
        detail = f"HTTP {exc.response.status_code}: {_status_body(exc)}"
    elif isinstance(exc, smtplib.SMTPException):
        detail = str(exc) or exc.__class__.__name__
    else:
        detail = str(exc) or exc.__class__.__name__
    return MailError(f"{what}: {detail}")


def _status_body(exc: httpx.HTTPStatusError) -> str:
    try:
        body = exc.response.json()
    except ValueError:
        return exc.response.text[:300]
    if isinstance(body, dict):
        message = body.get("message") or body.get("error")
        if message:
            return str(message)[:300]
    return str(body)[:300]
