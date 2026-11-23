"""Send email through an authenticated SMTP submission server.

The module uses only the standard library. Connections always use TLS: either
STARTTLS on a submission port or TLS from the first byte. The caller owns the
recipients, the text, the sender policy and the retry policy. No exception text
or representation contains the password.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass, field
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Literal

__all__ = [
    "MailError",
    "SmtpSettings",
    "html_message",
    "plain_text_message",
    "send_message",
]

type Security = Literal["starttls", "tls"]
_SECURITY_MODES: frozenset[str] = frozenset({"starttls", "tls"})
_MAXIMUM_PORT = 65535


class MailError(Exception):
    """The SMTP server was not available or did not accept the message."""


def _address(value: str | Address, role: str) -> Address:
    """Parse one bare email address, or return a caller-built Address."""
    if isinstance(value, Address):
        return value
    try:
        address = Address(addr_spec=value)
    except ValueError, IndexError, HeaderParseError:
        msg = f"The {role} address is not a valid email address."
        raise ValueError(msg) from None
    return address


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    """Connection settings for one SMTP submission account.

    An empty username sends without authentication. ``security`` is
    ``"starttls"`` (default, usually port 587 or 25) or ``"tls"`` (usually
    port 465). The password is excluded from the representation.
    """

    host: str
    port: int
    sender: str
    username: str = ""
    password: str = field(default="", repr=False)
    security: Security = "starttls"
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        """Reject settings that cannot open a secure connection."""
        if not self.host or any(character.isspace() for character in self.host):
            msg = "The SMTP host must be a host name without whitespace."
            raise ValueError(msg)
        if not 1 <= self.port <= _MAXIMUM_PORT:
            msg = "The SMTP port must be between 1 and 65535."
            raise ValueError(msg)
        if self.security not in _SECURITY_MODES:
            msg = "The SMTP security must be 'starttls' or 'tls'."
            raise ValueError(msg)
        if self.timeout_seconds <= 0:
            msg = "The SMTP timeout must be positive."
            raise ValueError(msg)
        if self.password and not self.username:
            msg = "The SMTP password needs a username."
            raise ValueError(msg)
        _address(self.sender, "sender")


def _headers(
    *,
    sender: str | Address,
    to: str | Address,
    subject: str,
    reply_to: str | Address | None,
) -> EmailMessage:
    """Return a message with validated addresses, a date and a message ID."""
    if "\r" in subject or "\n" in subject:
        msg = "The subject must be one line."
        raise ValueError(msg)
    sender_address = _address(sender, "sender")
    message = EmailMessage()
    message["From"] = sender_address
    message["To"] = _address(to, "recipient")
    if reply_to is not None:
        message["Reply-To"] = _address(reply_to, "reply-to")
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message["Message-ID"] = make_msgid(domain=sender_address.domain)
    return message


def plain_text_message(
    *,
    sender: str | Address,
    to: str | Address,
    subject: str,
    body: str,
    reply_to: str | Address | None = None,
) -> EmailMessage:
    """Build a UTF-8 plain text message with a date and a message ID.

    Raise ValueError for an invalid address or for a line break in the
    subject. Give an ``Address`` to add a display name; the email package
    rejects a display name with a line break.
    """
    message = _headers(sender=sender, to=to, subject=subject, reply_to=reply_to)
    message.set_content(body)
    return message


def html_message(  # noqa: PLR0913 - A message needs both parts and all headers.
    *,
    sender: str | Address,
    to: str | Address,
    subject: str,
    text: str,
    html: str,
    reply_to: str | Address | None = None,
) -> EmailMessage:
    """Build a UTF-8 ``multipart/alternative`` message with text and HTML.

    A client that cannot show HTML shows ``text``. The address and subject
    rules are the same as for ``plain_text_message``. The caller owns the
    HTML and must escape all values in it.
    """
    message = _headers(sender=sender, to=to, subject=subject, reply_to=reply_to)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


def send_message(settings: SmtpSettings, message: EmailMessage) -> None:
    """Send one message, or raise MailError.

    The connection verifies the server certificate against the system trust
    store. The error text names the server and the error type only.
    """
    context = ssl.create_default_context()
    try:
        client: smtplib.SMTP
        if settings.security == "tls":
            client = smtplib.SMTP_SSL(
                settings.host,
                settings.port,
                timeout=settings.timeout_seconds,
                context=context,
            )
        else:
            client = smtplib.SMTP(
                settings.host, settings.port, timeout=settings.timeout_seconds
            )
        with client:
            if settings.security == "starttls":
                client.starttls(context=context)
            if settings.username:
                client.login(settings.username, settings.password)
            client.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        msg = (
            f"The SMTP server {settings.host}:{settings.port} did not accept "
            f"the message ({type(error).__name__})."
        )
        raise MailError(msg) from None
