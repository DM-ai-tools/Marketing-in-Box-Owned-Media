"""Tests for `app/services/mail.py`.

Nothing here talks to Resend or an SMTP server. The seams are `_client` (HTTP) and `smtplib`
(SMTP); patch those, never the live network.
"""

from __future__ import annotations

from email.message import EmailMessage
from types import SimpleNamespace

import httpx
import pytest

from app.services import mail


@pytest.fixture(autouse=True)
def _clean_mail_env(monkeypatch):
    for name in (
        mail.RESEND_API_KEY_ENV,
        mail.MAIL_FROM_ENV,
        mail.SMTP_HOST_ENV,
        mail.SMTP_PORT_ENV,
        mail.SMTP_USER_ENV,
        mail.SMTP_PASSWORD_ENV,
        mail.SMTP_STARTTLS_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    mail.reset_client()
    yield
    mail.reset_client()


def test_is_configured_needs_from_and_a_transport(monkeypatch):
    assert mail.is_configured() is False
    monkeypatch.setenv(mail.RESEND_API_KEY_ENV, "re_test")
    assert mail.is_configured() is False
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "noreply@example.com")
    assert mail.is_configured() is True


def test_smtp_is_configured_without_resend(monkeypatch):
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "noreply@example.com")
    monkeypatch.setenv(mail.SMTP_HOST_ENV, "smtp.example.com")
    assert mail.is_configured() is True


def test_send_without_config_raises():
    with pytest.raises(mail.MailNotConfigured):
        mail.send_email(to="a@example.com", subject="s", text="t")


def test_resend_posts_the_message(monkeypatch):
    monkeypatch.setenv(mail.RESEND_API_KEY_ENV, "re_test")
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "Box <noreply@example.com>")

    captured: dict = {}

    class _Client:
        def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={"id": "msg_1"})

    monkeypatch.setattr(mail, "_client", lambda: _Client())
    mail.send_email(
        to="user@example.com",
        subject="Reset your password",
        text="plain",
        html="<p>html</p>",
    )
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["json"]["to"] == ["user@example.com"]
    assert captured["json"]["from"] == "Box <noreply@example.com>"
    assert captured["json"]["text"] == "plain"
    assert captured["json"]["html"] == "<p>html</p>"


def test_resend_http_error_becomes_mail_error(monkeypatch):
    monkeypatch.setenv(mail.RESEND_API_KEY_ENV, "re_test")
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "noreply@example.com")

    class _Client:
        def post(self, url, json):
            request = httpx.Request("POST", url)
            response = httpx.Response(
                403, request=request, json={"message": "domain not verified"}
            )
            response.raise_for_status()

    monkeypatch.setattr(mail, "_client", lambda: _Client())
    with pytest.raises(mail.MailError, match="403"):
        mail.send_email(to="user@example.com", subject="s", text="t")


def test_resend_is_preferred_over_smtp(monkeypatch):
    monkeypatch.setenv(mail.RESEND_API_KEY_ENV, "re_test")
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "noreply@example.com")
    monkeypatch.setenv(mail.SMTP_HOST_ENV, "smtp.example.com")

    called = {"resend": False, "smtp": False}
    monkeypatch.setattr(
        mail,
        "_send_resend",
        lambda **_kwargs: called.__setitem__("resend", True),
    )
    monkeypatch.setattr(
        mail,
        "_send_smtp",
        lambda **_kwargs: called.__setitem__("smtp", True),
    )
    mail.send_email(to="user@example.com", subject="s", text="t")
    assert called == {"resend": True, "smtp": False}


def test_smtp_sends_the_message(monkeypatch):
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "noreply@example.com")
    monkeypatch.setenv(mail.SMTP_HOST_ENV, "smtp.example.com")
    monkeypatch.setenv(mail.SMTP_PORT_ENV, "587")
    monkeypatch.setenv(mail.SMTP_USER_ENV, "mailer")
    monkeypatch.setenv(mail.SMTP_PASSWORD_ENV, "secret")

    sent: list[EmailMessage] = []

    class _Smtp:
        def __init__(self, host, port, timeout):
            self.host = host
            self.port = port
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            return None

        def starttls(self, context):
            return None

        def login(self, user, password):
            self.user = user
            self.password = password

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(mail.smtplib, "SMTP", _Smtp)
    mail.send_email(to="user@example.com", subject="Reset", text="click here")
    assert len(sent) == 1
    assert sent[0]["To"] == "user@example.com"
    assert sent[0]["Subject"] == "Reset"
    assert sent[0].get_content().startswith("click here")


def test_smtp_ssl_uses_port_465(monkeypatch):
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "noreply@example.com")
    monkeypatch.setenv(mail.SMTP_HOST_ENV, "smtp.example.com")
    monkeypatch.setenv(mail.SMTP_PORT_ENV, "465")

    used = {"ssl": False}

    class _Ssl:
        def __init__(self, host, port, context):
            used["ssl"] = True
            used["port"] = port

        def __enter__(self):
            return SimpleNamespace(
                login=lambda *_a: None,
                send_message=lambda *_a: None,
            )

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", _Ssl)
    mail.send_email(to="user@example.com", subject="s", text="t")
    assert used == {"ssl": True, "port": 465}
