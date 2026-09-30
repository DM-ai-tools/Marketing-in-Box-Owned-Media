"""Account, credential, and session mechanics for the sign-in gate.

Email and password only. There is no federated/OAuth sign-in: an account is an email plus a
scrypt-hashed password in this project's own Postgres. The only outbound call is a password-reset
email, and that goes through `app/services/mail.py` — nothing in this module constructs a mail
client.

Everything security-sensitive about authentication lives in this module so there is exactly one
place to audit; `app/routers/auth.py` above it does request/response shaping and cookie plumbing
only.

Two deliberate choices, each of which removes a dependency rather than adding one:

1. **Password hashing is stdlib `hashlib.scrypt`**, not bcrypt/argon2 via passlib. scrypt is a
   memory-hard KDF standardised in RFC 7914 and shipped in CPython, so the project gains a
   correct password hash without a new wheel to install, pin, or rebuild on deploy. Parameters
   are stored *inside* each hash string, so they can be raised later without invalidating
   existing passwords — `verify_password` reads the cost from the stored value, not from the
   constants below.

2. **Sessions are opaque random tokens in a database row**, not JWTs. See the `UserSession`
   docstring in app/db/models.py: sign-out has to actually revoke, which a stateless token
   cannot do.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PasswordResetToken, User, UserSession
from app.services import mail

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Policy constants
# --------------------------------------------------------------------------------------

#: Minimum password length. Six is the product's chosen floor (it is what the sign-up form
#: promises), and the frontend's counter reads this same number via `GET /auth/config` so the two
#: can never drift into "the form accepted it, the API rejected it".
PASSWORD_MIN_LENGTH = 6
#: A ceiling, purely so an enormous body cannot turn a login into a CPU exhaustion attack via the
#: KDF. Far above anything a person or a password manager produces.
PASSWORD_MAX_LENGTH = 200

#: How long a signed-in browser stays signed in without re-authenticating.
SESSION_TTL = timedelta(days=30)
#: Reset links are short-lived on purpose: the window in which a forwarded or logged link is
#: dangerous is exactly this long.
RESET_TOKEN_TTL = timedelta(hours=1)

SESSION_COOKIE = "miab_session"

# scrypt work factors for *new* hashes. n=2**15 with r=8 costs ~32 MB and a few tens of
# milliseconds per verification, which is the standard interactive-login target.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024  # Must exceed 128*n*r; the default of 0 is too small for n=2**15.

# Deliberately permissive. This is a sanity check that catches typos and empty input, not an
# attempt to implement RFC 5322 — over-strict email regexes reject valid addresses, and the only
# real proof an address works is a message arriving at it.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """A rejected credential or malformed auth input. The router maps this to a 4xx with
    `message` shown verbatim to the user, so every message raised here must be safe to display
    and phrased for a person rather than for a log."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------------------
# Email + password
# --------------------------------------------------------------------------------------


def normalize_email(raw: str) -> str:
    """Lowercase and trim, which is the form `users.email` is stored in.

    Every read and write path funnels through this, so "Alice@Example.com" at signup and
    "alice@example.com" at login are the same account rather than two. Gmail's dot- and
    plus-folding is *not* applied: those are provider-specific rules, and silently treating
    `a.b@gmail.com` as `ab@gmail.com` would merge two addresses their owner may consider
    separate.
    """
    return (raw or "").strip().lower()


def validate_email(raw: str) -> str:
    email = normalize_email(raw)
    if not email:
        raise AuthError("Enter your email address.")
    if len(email) > 320 or not _EMAIL_RE.match(email):
        raise AuthError("That does not look like a valid email address.")
    return email


def validate_password(raw: str) -> str:
    if not raw:
        raise AuthError("Enter a password.")
    if len(raw) < PASSWORD_MIN_LENGTH:
        raise AuthError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if len(raw) > PASSWORD_MAX_LENGTH:
        raise AuthError(f"Password must be {PASSWORD_MAX_LENGTH} characters or fewer.")
    return raw


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt_b64$hash_b64` — self-describing, so the cost can be raised later.

    The parameters travel with the hash rather than being read from the constants above at
    verification time. Without that, raising `_SCRYPT_N` would make every existing password fail
    to verify, since a different `n` produces a different digest from the same input.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of `password` against a hash produced by `hash_password`.

    Returns False rather than raising for an empty or unparseable stored value. `password_hash` is
    NOT NULL in the schema, so that should be unreachable — but a corrupt row must fail closed as
    an ordinary "wrong credentials" rather than 500-ing the login endpoint.
    """
    if not stored:
        return False
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        logger.warning("Unparseable password hash encountered; treating as a failed login.")
        return False

    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
        # Sized from the *stored* parameters, so a hash written under a higher cost still verifies.
        maxmem=max(_SCRYPT_MAXMEM, 256 * n * r),
    )
    return hmac.compare_digest(candidate, expected)


# --------------------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------------------


def _new_token() -> str:
    """A 256-bit URL-safe secret. Used for session cookies and reset links."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex of a bearer token, which is the only form the database ever holds.

    Plain SHA-256 rather than a KDF is correct *here* and wrong for passwords: these tokens are
    256 bits of `secrets` output, so there is no dictionary to attack and no reason to pay scrypt's
    cost on every authenticated request. Passwords are low-entropy and human-chosen, which is why
    they get scrypt above.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------------------


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def account_state(session: AsyncSession, email: str) -> dict:
    """What the sign-in form needs to know before it can ask its second question.

    This backs the email-first flow: the UI asks for an address, then shows *either* a password
    field *or* a "create your account" form, instead of making a first-time visitor guess which
    of two tabs they belong on.

    It is an intentional, bounded disclosure of whether an address has an account. Email
    enumeration is the price of an email-first form — every product with one pays it — and the
    alternative (a single ambiguous form) trades a real usability gain for an attacker who can
    learn the same fact from the signup endpoint's "that email is already registered". What is
    *not* disclosed is anything about the credential itself.
    """
    user = await find_user_by_email(session, email)
    if user is None:
        return {"exists": False}
    return {"exists": True, "full_name": user.full_name}


async def create_password_user(
    session: AsyncSession, *, email: str, password: str, full_name: str | None
) -> User:
    """Register a brand-new account.

    A duplicate address is refused rather than merged. With password as the only credential there
    is no legitimate "same person, second way in" case — an existing address means either the
    person already has an account (sign in) or they have forgotten they do (reset), and both are
    better answers than quietly overwriting a password from an unauthenticated request.
    """
    email = validate_email(email)
    validate_password(password)

    if await find_user_by_email(session, email) is not None:
        raise AuthError("An account with that email already exists. Sign in instead.", status_code=409)

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=(full_name or "").strip()[:200] or None,
        # A password signup proves nothing about the address. See the `User.email_verified` comment.
        email_verified=False,
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate_password(session: AsyncSession, *, email: str, password: str) -> User:
    """Verify an email/password pair, or raise `AuthError`.

    Both "no such account" and "wrong password" answer with the same message, and the no-account
    branch still runs a scrypt hash against a throwaway value. Without that dummy work, a missing
    account would return in microseconds while a real one took ~50ms, and the timing difference
    alone would answer "is this address registered?" for an attacker who never sees the response
    body. (The email-first `account_state` above discloses that deliberately and only for a
    human-paced form; this path should not leak it as a free side channel.)
    """
    email = normalize_email(email)
    generic = AuthError("Incorrect email or password.", status_code=401)

    user = await find_user_by_email(session, email)
    if user is None:
        verify_password(password or "x", hash_password("timing-equalizer"))
        raise generic
    if not verify_password(password or "", user.password_hash):
        raise generic

    user.last_login_at = _now()
    await session.flush()
    return user


# --------------------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------------------


async def create_session(
    session: AsyncSession, *, user: User, user_agent: str | None = None
) -> tuple[str, datetime]:
    """Issue a login session. Returns `(raw_token, expires_at)`; only the hash is persisted.

    The raw token is returned rather than stored anywhere, which is what makes the row useless to
    anyone who reads the table: it can confirm a token they already hold, and cannot produce one.
    """
    token = _new_token()
    expires_at = _now() + SESSION_TTL
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
            last_seen_at=_now(),
            user_agent=(user_agent or "")[:400] or None,
        )
    )
    await session.flush()
    return token, expires_at


async def resolve_session(session: AsyncSession, token: str | None) -> User | None:
    """The signed-in user for a cookie value, or None.

    An expired row is deleted on sight rather than merely ignored, so the table is groomed by
    ordinary traffic and needs no scheduled cleanup job to stop growing.
    """
    if not token:
        return None
    result = await session.execute(
        select(UserSession).where(UserSession.token_hash == hash_token(token))
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if row.expires_at <= _now():
        await session.delete(row)
        await session.commit()
        return None

    row.last_seen_at = _now()
    user = await session.get(User, row.user_id)
    await session.commit()
    return user


async def revoke_session(session: AsyncSession, token: str | None) -> None:
    """Sign out one browser. Silent when the token is unknown — a logout that 404s is a worse
    outcome than a logout that was already done."""
    if not token:
        return
    result = await session.execute(
        select(UserSession).where(UserSession.token_hash == hash_token(token))
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()


async def revoke_all_sessions(session: AsyncSession, user_id) -> None:
    """Sign out every browser for a user. Called after a password reset, because the reason
    someone resets a password is often that somebody else has it."""
    result = await session.execute(select(UserSession).where(UserSession.user_id == user_id))
    for row in result.scalars().all():
        await session.delete(row)
    await session.flush()


# --------------------------------------------------------------------------------------
# Password reset
# --------------------------------------------------------------------------------------


def app_base_url() -> str:
    """Where the browser should be sent — the frontend's origin, not the API's.

    Defaults to the Vite dev server because that is where this app is served in development, and
    the reset link has to be openable in a browser rather than reachable only from the API port.
    """
    return (os.environ.get("APP_BASE_URL") or "http://localhost:5173").rstrip("/")


async def issue_password_reset(session: AsyncSession, *, email: str) -> tuple[str, str] | None:
    """Create a reset grant and return `(raw_token, reset_url)`, or None if there is no account.

    Returning None for an unknown address is the whole point of splitting this from the route: the
    *route* answers "if that address has an account, a link is on its way" either way, so the
    absence of an account never leaks out of the API. Any prior unused token for the user is
    invalidated first, so the most recent link is the only live one.
    """
    email = normalize_email(email)
    user = await find_user_by_email(session, email)
    if user is None:
        return None

    result = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)
        )
    )
    for stale in result.scalars().all():
        await session.delete(stale)

    token = _new_token()
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=_now() + RESET_TOKEN_TTL,
        )
    )
    await session.flush()

    reset_url = f"{app_base_url()}/?reset_token={token}"
    return token, reset_url


def _reset_email_text(reset_url: str) -> str:
    minutes = int(RESET_TOKEN_TTL.total_seconds() // 60)
    return (
        "Use this link to set a new password for Marketing-in-a-Box.\n\n"
        f"{reset_url}\n\n"
        f"The link expires in {minutes} minutes and works once. If you did not ask for this, "
        "you can ignore the message."
    )


def _reset_email_html(reset_url: str) -> str:
    minutes = int(RESET_TOKEN_TTL.total_seconds() // 60)
    # The URL is interpolated into an attribute and a text node. It is produced by this process
    # (`app_base_url` + a `token_urlsafe` grant), never from operator input, so it is not escaped
    # as untrusted HTML — but quotes in a mis-set APP_BASE_URL would still break the href, so they
    # are stripped.
    safe = reset_url.replace('"', "").replace("<", "").replace(">", "")
    return (
        "<p>Use this link to set a new password for Marketing-in-a-Box.</p>"
        f'<p><a href="{safe}">Set a new password</a></p>'
        f"<p>The link expires in {minutes} minutes and works once. If you did not ask for this, "
        "you can ignore the message.</p>"
    )


def deliver_password_reset(*, email: str, reset_url: str) -> None:
    """Send the reset link, or log it when no mail transport is configured.

    Mail goes through `app/services/mail.py` (Resend or SMTP). A send failure is logged and
    swallowed: the forgot-password route always returns the same 200 either way, and turning a
    provider outage into a 500 on *known* addresses would disclose which emails have accounts.

    The raw URL is logged only when mail is unconfigured, at WARNING so it stands out in a local
    request log. Once a transport is set, the URL stays out of the logs — it is a live credential.
    """
    minutes = int(RESET_TOKEN_TTL.total_seconds() // 60)
    if mail.is_configured():
        try:
            mail.send_email(
                to=email,
                subject="Reset your Marketing-in-a-Box password",
                text=_reset_email_text(reset_url),
                html=_reset_email_html(reset_url),
            )
        except mail.MailError:
            logger.exception(
                "Password reset email failed for %s; the link was not logged because a mail "
                "transport is configured.",
                email,
            )
        return

    logger.warning(
        "PASSWORD RESET for %s — no mail transport configured, so the link is logged instead.\n"
        "    %s\n"
        "    (valid for %d minutes, single use)",
        email,
        reset_url,
        minutes,
    )


async def consume_password_reset(session: AsyncSession, *, token: str, new_password: str) -> User:
    """Redeem a reset token and set the new password, or raise `AuthError`.

    Expired and already-used tokens answer with the same message as an unknown one. The
    distinction is real but useless to the person holding a dead link — the action is identical
    ("request a new one") — and stating it would confirm to a stranger that the token was once
    valid.
    """
    validate_password(new_password)

    result = await session.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token or ""))
    )
    row = result.scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= _now():
        raise AuthError("That reset link is invalid or has expired. Request a new one.", status_code=400)

    user = await session.get(User, row.user_id)
    if user is None:  # pragma: no cover - CASCADE makes this unreachable
        raise AuthError("That account no longer exists.", status_code=404)

    user.password_hash = hash_password(new_password)
    # Reaching a link sent to the address is proof the address is theirs — the one thing a
    # password signup could not establish.
    user.email_verified = True
    row.used_at = _now()

    # Every other browser is signed out. If the reset was prompted by a compromise, leaving the
    # attacker's existing session alive would make the new password pointless.
    await revoke_all_sessions(session, user.id)
    await session.flush()
    return user
