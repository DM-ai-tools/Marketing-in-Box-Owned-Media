"""Tests for the sign-in gate — `app/services/auth.py` and `app/routers/auth.py`.

Two halves, because the two risks are different:

1. **Credential mechanics, no database.** A password hash that verifies the wrong thing, or a
   length rule that disagrees with the form, fails silently in the direction of letting people in.
   These tests pin the scrypt round trip, the 6-character floor, and the corrupt-hash case.

2. **The flows, over real HTTP against the real database.** Signup -> login -> me -> logout and
   forgot -> reset are stateful across three tables and a cookie; testing the service functions in
   isolation would miss exactly the things that break — a cookie not set, a session not revoked, a
   reset token reusable twice.

The second half is skipped rather than failed when Postgres is not reachable, so the suite still
runs on a machine with no database (the rest of this project's tests need none). It cleans up the
accounts it creates in a fixture, and every address it uses is under `@auth-test.invalid` — a
reserved TLD, so a stray row can never collide with a real user.
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.services import auth as auth_service
from app.services.auth import (
    PASSWORD_MIN_LENGTH,
    SESSION_COOKIE,
    AuthError,
    hash_password,
    hash_token,
    normalize_email,
    validate_email,
    validate_password,
    verify_password,
)

# --------------------------------------------------------------------------------------
# Credential mechanics (no database)
# --------------------------------------------------------------------------------------


def test_password_round_trip():
    stored = hash_password("correct horse")
    assert verify_password("correct horse", stored)
    assert not verify_password("correct hors", stored)
    assert not verify_password("", stored)


def test_password_hash_is_salted():
    """Two accounts with the same password must not share a hash, or one cracked hash cracks both."""
    assert hash_password("same") != hash_password("same")


def test_password_hash_carries_its_own_parameters():
    """The cost has to travel with the hash, so raising it later does not invalidate every
    existing password. See `hash_password`."""
    scheme, n, r, p, _salt, _digest = hash_password("x").split("$")
    assert scheme == "scrypt"
    assert (int(n), int(r), int(p)) == (2**15, 8, 1)


def test_password_verifies_against_a_higher_cost_hash():
    """A hash written under stronger parameters than the current default still verifies — the
    forward-compatibility the self-describing format exists for."""
    import hashlib
    import secrets

    salt = secrets.token_bytes(16)
    n = 2**16
    digest = hashlib.scrypt(
        b"future", salt=salt, n=n, r=8, p=1, dklen=32, maxmem=256 * n * 8
    )
    stored = f"scrypt${n}$8$1${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"
    assert verify_password("future", stored)
    assert not verify_password("futur", stored)


def test_verify_password_rejects_null_and_garbage():
    """`password_hash` is NOT NULL in the schema, so these are unreachable rows — but a corrupt one
    must fail closed as ordinary wrong credentials rather than 500-ing the login endpoint."""
    assert not verify_password("anything", None)
    assert not verify_password("anything", "")
    assert not verify_password("anything", "not-a-hash")
    assert not verify_password("anything", "scrypt$bad$bad$bad$bad$bad")


def test_password_length_floor_is_six():
    with pytest.raises(AuthError):
        validate_password("12345")
    assert validate_password("123456") == "123456"
    assert PASSWORD_MIN_LENGTH == 6


def test_password_length_ceiling_exists():
    """A ceiling so an enormous body cannot turn a login into CPU exhaustion via the KDF."""
    with pytest.raises(AuthError):
        validate_password("x" * 5000)


def test_email_is_normalized_not_folded():
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"
    # Gmail's dot/plus folding is *not* applied — those are provider rules, and folding them here
    # would silently merge addresses their owner may consider separate.
    assert normalize_email("a.b+tag@gmail.com") == "a.b+tag@gmail.com"


@pytest.mark.parametrize("bad", ["", "   ", "nope", "no@domain", "two@@at.com", "a@b"])
def test_email_validation_rejects_obvious_junk(bad):
    with pytest.raises(AuthError):
        validate_email(bad)


def test_token_hash_is_stable_and_hex():
    digest = hash_token("abc")
    assert digest == hash_token("abc")
    assert len(digest) == 64
    assert digest != hash_token("abd")


def test_deliver_password_reset_sends_mail_when_configured(monkeypatch, caplog):
    """A configured transport gets the link; the logs do not — it is a live credential."""
    sent: list[dict] = []
    monkeypatch.setattr(auth_service.mail, "is_configured", lambda: True)
    monkeypatch.setattr(
        auth_service.mail, "send_email", lambda **kwargs: sent.append(kwargs)
    )
    url = "http://localhost:5173/?reset_token=secret-token"
    with caplog.at_level("WARNING"):
        auth_service.deliver_password_reset(email="a@example.com", reset_url=url)
    assert len(sent) == 1
    assert sent[0]["to"] == "a@example.com"
    assert "secret-token" in sent[0]["text"]
    assert "secret-token" in sent[0]["html"]
    assert "secret-token" not in caplog.text


def test_deliver_password_reset_logs_the_link_when_mail_is_off(monkeypatch, caplog):
    monkeypatch.setattr(auth_service.mail, "is_configured", lambda: False)
    url = "http://localhost:5173/?reset_token=secret-token"
    with caplog.at_level("WARNING"):
        auth_service.deliver_password_reset(email="a@example.com", reset_url=url)
    assert "secret-token" in caplog.text


def test_deliver_password_reset_swallows_send_failures(monkeypatch, caplog):
    """A provider outage must not become a 500 on known addresses — that would enumerate accounts."""
    monkeypatch.setattr(auth_service.mail, "is_configured", lambda: True)

    def boom(**_kwargs):
        raise auth_service.mail.MailError("Resend is down")

    monkeypatch.setattr(auth_service.mail, "send_email", boom)
    url = "http://localhost:5173/?reset_token=secret-token"
    with caplog.at_level("ERROR"):
        auth_service.deliver_password_reset(email="a@example.com", reset_url=url)
    assert "failed" in caplog.text.lower()
    assert "secret-token" not in caplog.text


# --------------------------------------------------------------------------------------
# End-to-end flows (require Postgres)
# --------------------------------------------------------------------------------------


def _database_reachable() -> bool:
    """Whether the integration half can run at all. The rest of this project's tests need no
    database, so an unreachable one is a skip rather than a failure."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False
    try:
        import psycopg

        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=3):
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _database_reachable(), reason="No reachable DATABASE_URL; skipping auth HTTP flow tests."
)

#: A reserved TLD (RFC 2606), so a row left behind by a crashed run can never collide with, or be
#: mistaken for, a real account.
TEST_DOMAIN = "auth-test.invalid"


@pytest.fixture
def client():
    """A `TestClient` that keeps cookies across calls, which is what makes the session testable.

    `app.main` is imported inside the fixture rather than at module scope so the unit half above
    runs on a machine where importing the app (and its Windows event-loop setup) is not wanted.
    """
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fresh_email():
    """A unique address per test, and the rows it created removed afterwards.

    Cleanup goes through raw SQL rather than the ORM because it must run even when the test failed
    halfway through a flow, and every FK in this group is `ON DELETE CASCADE` — so deleting the
    user takes its sessions and reset tokens with it.
    """
    email = f"user-{uuid.uuid4().hex[:12]}@{TEST_DOMAIN}"
    yield email

    import psycopg

    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM users WHERE email = %s", (email,))
        conn.commit()


@requires_db
def test_config_endpoint_reports_the_password_floor(client):
    body = client.get("/auth/config").json()
    assert body["password_min_length"] == PASSWORD_MIN_LENGTH


@requires_db
def test_check_email_is_false_for_an_unknown_address(client, fresh_email):
    body = client.post("/auth/check-email", json={"email": fresh_email}).json()
    assert body == {"exists": False, "full_name": None}


@requires_db
def test_signup_then_check_email_then_login_then_logout(client, fresh_email):
    """The whole first-time-user path in one test, because the interesting failures are between
    the steps rather than inside them."""
    # First visit: no account, so the UI shows the signup step.
    assert client.post("/auth/check-email", json={"email": fresh_email}).json()["exists"] is False

    created = client.post(
        "/auth/signup",
        json={"email": fresh_email, "password": "sixchr", "full_name": "Test Person"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["email"] == fresh_email
    # Signed in immediately — the credentials were just verified, so asking again is pure friction.
    assert SESSION_COOKIE in created.cookies or SESSION_COOKIE in client.cookies

    # Second visit: the same address now routes to the password step instead.
    state = client.post("/auth/check-email", json={"email": fresh_email}).json()
    assert state["exists"] is True
    assert state["full_name"] == "Test Person"

    assert client.get("/auth/me").json()["email"] == fresh_email

    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401

    # And back in with the password.
    assert client.post("/auth/login", json={"email": fresh_email, "password": "sixchr"}).status_code == 200
    assert client.get("/auth/me").json()["email"] == fresh_email


@requires_db
def test_signup_is_case_insensitive_on_email(client, fresh_email):
    """Mixed case at signup and lowercase at login must be one account, not two."""
    client.post("/auth/signup", json={"email": fresh_email.upper(), "password": "sixchr"})
    client.post("/auth/logout")

    assert client.post("/auth/login", json={"email": fresh_email, "password": "sixchr"}).status_code == 200
    assert client.get("/auth/me").json()["email"] == fresh_email  # stored lowercased


@requires_db
def test_signup_rejects_a_short_password(client, fresh_email):
    res = client.post("/auth/signup", json={"email": fresh_email, "password": "12345"})
    assert res.status_code == 400
    assert "at least 6" in res.json()["detail"]


@requires_db
def test_duplicate_signup_is_refused(client, fresh_email):
    client.post("/auth/signup", json={"email": fresh_email, "password": "sixchr"})
    res = client.post("/auth/signup", json={"email": fresh_email, "password": "another"})
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"]


@requires_db
def test_wrong_password_and_unknown_account_answer_identically(client, fresh_email):
    """The same 401 and the same wording either way, so the response body cannot be used to
    enumerate which addresses are registered."""
    client.post("/auth/signup", json={"email": fresh_email, "password": "sixchr"})
    client.post("/auth/logout")

    wrong = client.post("/auth/login", json={"email": fresh_email, "password": "nope123"})
    missing = client.post(
        "/auth/login", json={"email": f"absent-{uuid.uuid4().hex[:8]}@{TEST_DOMAIN}", "password": "nope123"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["detail"] == missing.json()["detail"]


@requires_db
def test_me_is_401_when_signed_out(client):
    assert client.get("/auth/me").status_code == 401


@requires_db
def test_forgot_password_answer_does_not_reveal_whether_the_account_exists(client, fresh_email, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        auth_service, "deliver_password_reset", lambda *, email, reset_url: sent.append(reset_url)
    )

    client.post("/auth/signup", json={"email": fresh_email, "password": "sixchr"})
    client.post("/auth/logout")

    known = client.post("/auth/forgot-password", json={"email": fresh_email})
    unknown = client.post(
        "/auth/forgot-password", json={"email": f"absent-{uuid.uuid4().hex[:8]}@{TEST_DOMAIN}"}
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    # ...but only the real account actually got a link.
    assert len(sent) == 1


@requires_db
def test_forgot_password_still_200_when_mail_fails(client, fresh_email, monkeypatch):
    """Same 200 as a successful send, so a down mailer cannot be used to enumerate accounts."""
    monkeypatch.setattr(auth_service.mail, "is_configured", lambda: True)

    def boom(**_kwargs):
        raise auth_service.mail.MailError("Resend is down")

    monkeypatch.setattr(auth_service.mail, "send_email", boom)
    client.post("/auth/signup", json={"email": fresh_email, "password": "sixchr"})
    client.post("/auth/logout")
    res = client.post("/auth/forgot-password", json={"email": fresh_email})
    assert res.status_code == 200
    assert "on its way" in res.json()["message"]


@requires_db
def test_reset_link_sets_a_new_password_and_is_single_use(client, fresh_email, monkeypatch):
    """The reset flow's three load-bearing properties in one pass: the link works, the old password
    stops working, and the link cannot be replayed."""
    sent: list[str] = []
    monkeypatch.setattr(
        auth_service, "deliver_password_reset", lambda *, email, reset_url: sent.append(reset_url)
    )

    client.post("/auth/signup", json={"email": fresh_email, "password": "oldpass"})
    client.post("/auth/logout")

    client.post("/auth/forgot-password", json={"email": fresh_email})
    assert len(sent) == 1
    token = sent[0].split("reset_token=")[1]

    res = client.post("/auth/reset-password", json={"token": token, "password": "newpass"})
    assert res.status_code == 200, res.text
    # Redeeming the link signs you in — reaching it proved control of the mailbox.
    assert client.get("/auth/me").json()["email"] == fresh_email
    # ...and proves the address, which a password signup could not.
    assert client.get("/auth/me").json()["email_verified"] is True

    client.post("/auth/logout")
    assert client.post("/auth/login", json={"email": fresh_email, "password": "oldpass"}).status_code == 401
    assert client.post("/auth/login", json={"email": fresh_email, "password": "newpass"}).status_code == 200

    # Replaying the same link must fail, even though it has not expired.
    client.post("/auth/logout")
    replay = client.post("/auth/reset-password", json={"token": token, "password": "thirdpass"})
    assert replay.status_code == 400
    assert "invalid or has expired" in replay.json()["detail"]


@requires_db
def test_reset_rejects_a_short_password(client, fresh_email, monkeypatch):
    """The 6-character floor applies to the reset form too, not only to signup."""
    sent: list[str] = []
    monkeypatch.setattr(
        auth_service, "deliver_password_reset", lambda *, email, reset_url: sent.append(reset_url)
    )
    client.post("/auth/signup", json={"email": fresh_email, "password": "oldpass"})
    client.post("/auth/forgot-password", json={"email": fresh_email})
    token = sent[0].split("reset_token=")[1]

    res = client.post("/auth/reset-password", json={"token": token, "password": "12345"})
    assert res.status_code == 400
    assert "at least 6" in res.json()["detail"]


@requires_db
def test_reset_revokes_other_sessions(client, fresh_email, monkeypatch):
    """A reset is often prompted by a compromise, so every other browser is signed out. Two
    `TestClient`s stand in for two browsers — they keep separate cookie jars."""
    from app.main import app

    sent: list[str] = []
    monkeypatch.setattr(
        auth_service, "deliver_password_reset", lambda *, email, reset_url: sent.append(reset_url)
    )

    client.post("/auth/signup", json={"email": fresh_email, "password": "oldpass"})
    assert client.get("/auth/me").status_code == 200  # browser A, signed in

    with TestClient(app) as other:  # browser B
        other.post("/auth/login", json={"email": fresh_email, "password": "oldpass"})
        assert other.get("/auth/me").status_code == 200

        other.post("/auth/forgot-password", json={"email": fresh_email})
        token = sent[-1].split("reset_token=")[1]
        assert other.post("/auth/reset-password", json={"token": token, "password": "newpass"}).status_code == 200

        # B, which performed the reset, stays in.
        assert other.get("/auth/me").status_code == 200

    # A does not.
    assert client.get("/auth/me").status_code == 401


@requires_db
def test_reset_token_is_not_stored_in_the_clear(client, fresh_email, monkeypatch):
    """The token in the link must not be recoverable from the table — only its SHA-256 is kept."""
    import psycopg

    sent: list[str] = []
    monkeypatch.setattr(
        auth_service, "deliver_password_reset", lambda *, email, reset_url: sent.append(reset_url)
    )
    client.post("/auth/signup", json={"email": fresh_email, "password": "oldpass"})
    client.post("/auth/forgot-password", json={"email": fresh_email})
    token = sent[0].split("reset_token=")[1]

    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT token_hash FROM password_reset_tokens t "
            "JOIN users u ON u.id = t.user_id WHERE u.email = %s",
            (fresh_email,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] != token
    assert rows[0][0] == hash_token(token)
