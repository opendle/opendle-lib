"""Tests for plain text email over SMTP."""

from __future__ import annotations

import smtplib
from email.headerregistry import Address
from typing import TYPE_CHECKING, Any, Self

import pytest

from opendle.mail import MailError, SmtpSettings, plain_text_message, send_message

if TYPE_CHECKING:
    from email.message import EmailMessage


class FakeClient:
    """Record the SMTP calls instead of opening a connection."""

    instances: list[FakeClient] = []  # noqa: RUF012 - shared test record
    fail_on: str | None = None

    def __init__(self, host: str, port: int, **options: object) -> None:
        """Record the connection arguments."""
        self.host = host
        self.port = port
        self.options = options
        self.calls: list[str] = []
        self.sent: list[EmailMessage] = []
        FakeClient.instances.append(self)
        self._step("connect")

    def _step(self, name: str) -> None:
        self.calls.append(name)
        if FakeClient.fail_on == name:
            raise smtplib.SMTPAuthenticationError(535, b"secret-password rejected")

    def __enter__(self) -> Self:
        """Open the fake session."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the fake session."""
        self.calls.append("quit")

    def starttls(self, **_: object) -> None:
        """Record STARTTLS."""
        self._step("starttls")

    def login(self, username: str, password: str) -> None:
        """Record the login."""
        assert (username, password) == ("user", "secret-password")
        self._step("login")

    def send_message(self, message: EmailMessage) -> None:
        """Record the message."""
        self._step("send")
        self.sent.append(message)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace both SMTP client classes."""
    FakeClient.instances = []
    FakeClient.fail_on = None
    monkeypatch.setattr(smtplib, "SMTP", FakeClient)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeClient)


def settings(**changes: object) -> SmtpSettings:
    """Return valid settings with optional changes."""
    values: dict[str, Any] = {
        "host": "smtp.example.com",
        "port": 587,
        "sender": "noreply@example.com",
        "username": "user",
        "password": "secret-password",
    }
    values.update(changes)
    return SmtpSettings(**values)


def message() -> EmailMessage:
    """Return a valid message."""
    return plain_text_message(
        sender="noreply@example.com",
        to="owner@example.com",
        subject="Contact",
        body="Hello\nSecond line",
        reply_to=Address("Ada Lovelace", addr_spec="ada@example.org"),
    )


def test_starttls_login_and_send() -> None:
    """Use STARTTLS before the login, then send the message."""
    send_message(settings(), message())
    client = FakeClient.instances[0]
    assert (client.host, client.port) == ("smtp.example.com", 587)
    assert client.calls == ["connect", "starttls", "login", "send", "quit"]
    sent = client.sent[0]
    assert sent["To"] == "owner@example.com"
    assert sent["Reply-To"] == "Ada Lovelace <ada@example.org>"
    assert sent["Message-ID"].endswith("@example.com>")
    assert sent.get_content() == "Hello\nSecond line\n"


def test_implicit_tls_without_login() -> None:
    """TLS mode does not use STARTTLS; no username means no login."""
    send_message(
        settings(port=465, security="tls", username="", password=""), message()
    )
    client = FakeClient.instances[0]
    assert "context" in client.options
    assert client.calls == ["connect", "send", "quit"]


@pytest.mark.parametrize("step", ["connect", "starttls", "login", "send"])
def test_failure_hides_server_text(step: str) -> None:
    """A failure raises MailError without the server reply or the password."""
    FakeClient.fail_on = step
    with pytest.raises(MailError) as caught:
        send_message(settings(), message())
    assert "secret-password" not in str(caught.value)
    assert "smtp.example.com:587" in str(caught.value)


def test_representation_hides_password() -> None:
    """The settings representation does not contain the password."""
    assert "secret-password" not in repr(settings())


@pytest.mark.parametrize(
    "changes",
    [
        {"host": ""},
        {"host": "smtp example.com"},
        {"port": 0},
        {"port": 65536},
        {"security": "none"},
        {"timeout_seconds": 0},
        {"username": ""},
        {"sender": "not-an-address"},
    ],
)
def test_invalid_settings(changes: dict[str, Any]) -> None:
    """Reject settings that cannot open a secure, authenticated connection."""
    with pytest.raises(ValueError, match=r"SMTP|sender"):
        settings(**changes)


@pytest.mark.parametrize("subject", ["a\nBcc: x@example.com", "a\rb"])
def test_subject_must_be_one_line(subject: str) -> None:
    """A line break in the subject cannot add a header."""
    with pytest.raises(ValueError, match="one line"):
        plain_text_message(
            sender="a@example.com", to="b@example.com", subject=subject, body=""
        )


@pytest.mark.parametrize(
    "address", ["", "plain", "a@", "@example.com", "a@example.com\nBcc: x@y.z"]
)
def test_invalid_address(address: str) -> None:
    """Reject an address that is not one bare email address."""
    with pytest.raises(ValueError, match="address"):
        plain_text_message(sender="a@example.com", to=address, subject="s", body="")


def test_message_without_reply_to() -> None:
    """Without a reply address, replies go to the sender."""
    sent = plain_text_message(
        sender="a@example.com", to="b@example.com", subject="s", body="x"
    )
    assert "Reply-To" not in sent
    assert sent["From"] == "a@example.com"
