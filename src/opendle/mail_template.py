"""Render one branded email layout as HTML and plain text from one content.

A host describes an email with ``EmailContent``: a heading, paragraphs, one
main action, a preview card, sections of items and a note. ``render_email``
gives the two parts from the same content, so the HTML and the text never
differ. ``email_message`` builds the ``multipart/alternative`` message for
``opendle.mail.send_message``. The message carries each ``EmailImage`` as an
inline part, so the client shows it without a remote request.

The HTML uses tables and inline styles, which the common email clients show
the same way. A client with dark mode support gets dark colors. The module
escapes each value and accepts only HTTP and HTTPS links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.utils import make_msgid
from html import escape
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from opendle.mail import html_message

if TYPE_CHECKING:
    from email.headerregistry import Address
    from email.message import EmailMessage

__all__ = [
    "EmailBrand",
    "EmailContent",
    "EmailImage",
    "EmailItem",
    "EmailLink",
    "EmailPreview",
    "EmailSection",
    "EmailTone",
    "RenderedEmail",
    "email_message",
    "render_email",
]

type EmailTone = Literal["neutral", "success", "danger"]

_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_TONES: dict[str, str] = {"success": "#2f7a4f", "danger": "#b4432f"}
_PAGE = "#f5f2ec"
_CARD = "#ffffff"
_DARK_CARD = "#211e1a"
_BORDER = "#e9e3d9"
_TEXT = "#2a2621"
_BODY = "#4a443c"
_MUTED = "#7a7166"
_IMAGE_TYPES = frozenset({"image/gif", "image/jpeg", "image/png"})
# The largest side of a preview picture and the side of a row picture, in pixels.
_PREVIEW_IMAGE = 80
_ROW_IMAGE = 24
# Spaces after the preheader stop a client from showing the body text there.
_PREHEADER_FILL = "&#8199;&#65279;&#847; " * 40


def _link_url(url: str) -> str:
    """Return a link URL, or raise ValueError when it is not HTTP or HTTPS."""
    if not url.startswith(("https://", "http://")) or any(
        character.isspace() for character in url
    ):
        msg = "An email link must be an HTTP or HTTPS URL without whitespace."
        raise ValueError(msg)
    return url


def _check_color(value: str) -> None:
    """Raise ValueError when a color does not have the form ``#rrggbb``."""
    if not _COLOR.fullmatch(value):
        msg = "An email color must have the form #rrggbb."
        raise ValueError(msg)


def _mix(color: str, base: str, amount: float) -> str:
    """Mix two ``#rrggbb`` colors. ``amount`` is the share of ``color``."""

    def channels(value: str) -> list[int]:
        return [int(value[index : index + 2], 16) for index in (1, 3, 5)]

    return "#" + "".join(
        f"{round(back + (front - back) * amount):02x}"
        for front, back in zip(channels(color), channels(base), strict=True)
    )


@dataclass(frozen=True, slots=True)
class EmailBrand:
    """Identify the sender: name, home URL, optional logo and accent color.

    ``logo_url`` must be a PNG or JPEG image at a public HTTPS URL, because
    most clients do not show SVG. ``display_font`` is the CSS font list of
    the heading; clients without the first font use the next one.
    """

    name: str
    url: str
    logo_url: str | None = None
    accent: str = "#a75931"
    display_font: str = "Georgia, 'Times New Roman', serif"
    body_font: str = (
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    )

    def __post_init__(self) -> None:
        """Reject a link or a color that the layout cannot use safely."""
        _link_url(self.url)
        if self.logo_url is not None:
            _link_url(self.logo_url)
        _check_color(self.accent)
        if any(character in self.display_font + self.body_font for character in ';"<>'):
            msg = 'An email font list must not contain ; " < or >.'
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class EmailLink:
    """Give a link text and its HTTP or HTTPS target."""

    label: str
    url: str

    def __post_init__(self) -> None:
        """Reject a target that is not HTTP or HTTPS."""
        _link_url(self.url)


@dataclass(frozen=True, slots=True)
class EmailImage:
    """Give a PNG, JPEG or GIF picture that the message carries inline.

    ``width`` and ``height`` are the pixel size of ``content``. With them, the
    layout keeps the aspect ratio in clients that ignore CSS sizes.
    """

    content: bytes = field(repr=False)
    content_type: str
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        """Reject a type that the common clients do not show, or a bad size."""
        if self.content_type not in _IMAGE_TYPES:
            msg = "An email image must be a PNG, JPEG or GIF picture."
            raise ValueError(msg)
        if any(side is not None and side <= 0 for side in (self.width, self.height)):
            msg = "An email image size must be positive."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class EmailItem:
    """Show one row: a title, an optional detail, a link and a small picture."""

    title: str
    detail: str = ""
    link: EmailLink | None = None
    image: EmailImage | None = None


@dataclass(frozen=True, slots=True)
class EmailSection:
    """Group rows under a short title, for example the next steps."""

    title: str
    items: tuple[EmailItem, ...]


@dataclass(frozen=True, slots=True)
class EmailPreview:
    """Show a compact card of one thing, for example a post and its channels.

    The card has an optional picture, a title, a short text and one row for
    each item. Each row shows its picture, or the first letter of its title.
    """

    title: str
    text: str = ""
    image: EmailImage | None = None
    items: tuple[EmailItem, ...] = ()


@dataclass(frozen=True, slots=True)
class EmailContent:
    """Describe one email. Each text is plain text; the layout escapes it.

    ``label`` is a short word above the heading, in the color of ``tone``.
    ``preheader`` is the summary that a client shows next to the subject.
    ``preview`` is a card below the main action.
    ``footer`` is the text below the card, for example why the person gets
    the email, and ``footer_link`` is where the person changes that.
    """

    subject: str
    heading: str
    paragraphs: tuple[str, ...] = ()
    action: EmailLink | None = None
    preview: EmailPreview | None = None
    sections: tuple[EmailSection, ...] = ()
    note: str = ""
    label: str = ""
    tone: EmailTone = "neutral"
    preheader: str = ""
    footer: tuple[str, ...] = field(default_factory=tuple)
    footer_link: EmailLink | None = None


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """Give the subject, the two parts and the inline pictures of one email.

    The HTML part refers to each picture as ``cid:`` followed by its content ID.
    """

    subject: str
    text: str
    html: str
    images: tuple[tuple[str, EmailImage], ...] = ()


class _Images:
    """Give each different picture of one email one content ID."""

    def __init__(self, domain: str) -> None:
        self._domain = domain
        self._ids: dict[tuple[bytes, str], str] = {}
        self.parts: list[tuple[str, EmailImage]] = []

    def source(self, image: EmailImage) -> str:
        key = (image.content, image.content_type)
        if key not in self._ids:
            content_id = make_msgid(domain=self._domain)[1:-1]
            self._ids[key] = content_id
            self.parts.append((content_id, image))
        return f"cid:{escape(self._ids[key])}"


def _fit(image: EmailImage, side: int) -> tuple[int, int | None]:
    """Return a display size inside a square. Without a height, the client scales."""
    if image.width is None or image.height is None:
        return side, None
    scale = side / max(image.width, image.height)
    return max(1, round(image.width * scale)), max(1, round(image.height * scale))


def _text(value: str) -> str:
    """Escape a text and keep its line breaks."""
    return escape(value).replace("\n", "<br>")


def _table(cells: str, style: str = "", css_class: str = "") -> str:
    """Return a full-width layout table with one row of the given cells."""
    attributes = f' class="{css_class}"' if css_class else ""
    return (
        f'<table role="presentation"{attributes} width="100%" cellpadding="0" '
        f'cellspacing="0" border="0" style="{style}"><tr>{cells}</tr></table>'
    )


def _paragraph(value: str, style: str, css_class: str = "m-body") -> str:
    return f'<p class="{css_class}" style="margin:0 0 16px;{style}">{_text(value)}</p>'


def _button(link: EmailLink, color: str, font: str) -> str:
    """Return a button that works also in clients without CSS padding on links."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:8px 0 4px;"><tr>'
        f'<td bgcolor="{color}" style="border-radius:10px;background:{color};">'
        f'<a href="{escape(link.url)}" target="_blank" '
        f'style="display:inline-block;padding:13px 24px;border-radius:10px;'
        f"font-family:{font};font-size:15px;font-weight:600;line-height:1;"
        f'color:#ffffff;text-decoration:none;">{escape(link.label)}</a>'
        "</td></tr></table>"
    )


def _picture(image: EmailImage, side: int, images: _Images, style: str) -> str:
    width, height = _fit(image, side)
    size = f'width="{width}"' + (f' height="{height}"' if height else "")
    css = f"width:{width}px;" + (f"height:{height}px;" if height else "height:auto;")
    return (
        f'<img src="{images.source(image)}" {size} alt="" '
        f'style="display:block;{css}border:0;{style}">'
    )


def _row_picture(item: EmailItem, brand: EmailBrand, images: _Images) -> str:
    """Return the row picture, or the first letter of the title in a circle."""
    radius = f"border-radius:{_ROW_IMAGE // 2}px;"
    if item.image is not None:
        return _picture(item.image, _ROW_IMAGE, images, radius)
    letter = escape(item.title.strip()[:1].upper())
    return (
        f'<div class="m-initial" style="width:{_ROW_IMAGE}px;height:{_ROW_IMAGE}px;'
        f"{radius}background:{_mix(brand.accent, _CARD, 0.14)};"
        f"color:{brand.accent};font-size:12px;font-weight:700;"
        f'line-height:{_ROW_IMAGE}px;text-align:center;">{letter}</div>'
    )


def _row(
    item: EmailItem,
    brand: EmailBrand,
    images: _Images,
    *,
    padding: str,
    picture: bool,
) -> str:
    """Return one table row: an optional picture, the title, detail and link."""
    rule = f"border-top:1px solid {_BORDER};"
    cell = f'class="m-rule" valign="top" style="{rule}padding:{padding};'
    detail = (
        f'<div class="m-muted" style="margin-top:3px;font-size:13.5px;'
        f'line-height:1.5;color:{_MUTED};">{_text(item.detail)}</div>'
        if item.detail
        else ""
    )
    link = (
        f'<a class="m-link" href="{escape(item.link.url)}" target="_blank" '
        f'style="font-size:13.5px;font-weight:600;color:{brand.accent};'
        f'text-decoration:none;">{escape(item.link.label)}&nbsp;&rarr;</a>'
        if item.link
        else ""
    )
    image = (
        f'<td width="{_ROW_IMAGE + 12}" {cell}padding-right:12px;">'
        f"{_row_picture(item, brand, images)}</td>"
        if picture or item.image is not None
        else ""
    )
    return (
        f'<tr>{image}<td {cell}">'
        f'<div class="m-text" style="font-size:14.5px;font-weight:600;'
        f'line-height:1.45;color:{_TEXT};">{_text(item.title)}</div>'
        f'{detail}</td><td align="right" {cell}padding-left:16px;'
        f'white-space:nowrap;">{link}</td></tr>'
    )


def _rows(
    items: tuple[EmailItem, ...],
    brand: EmailBrand,
    images: _Images,
    *,
    padding: str,
    picture: bool = False,
) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0">'
        + "".join(
            _row(item, brand, images, padding=padding, picture=picture)
            for item in items
        )
        + "</table>"
    )


def _section(section: EmailSection, brand: EmailBrand, images: _Images) -> str:
    return (
        f'<div class="m-muted" style="margin:28px 0 6px;font-size:11.5px;'
        f"font-weight:700;letter-spacing:0.08em;text-transform:uppercase;"
        f'color:{_MUTED};">{escape(section.title)}</div>'
        + _rows(section.items, brand, images, padding="14px 0")
    )


def _preview(preview: EmailPreview, brand: EmailBrand, images: _Images) -> str:
    """Return a bordered card: picture, title and text, then one row per item."""
    picture = (
        '<td valign="top" style="padding-right:14px;">'
        + _picture(preview.image, _PREVIEW_IMAGE, images, "border-radius:8px;")
        + "</td>"
        if preview.image is not None
        else ""
    )
    text = (
        f'<div class="m-muted" style="margin-top:4px;font-size:13.5px;'
        f'line-height:1.5;color:{_MUTED};">{_text(preview.text)}</div>'
        if preview.text
        else ""
    )
    head = _table(
        f'{picture}<td valign="top"><div class="m-text" style="font-size:15px;'
        f'font-weight:600;line-height:1.4;color:{_TEXT};">{_text(preview.title)}'
        f"</div>{text}</td>"
    )
    rows = (
        f'<tr><td style="padding:0 16px;">'
        f"{_rows(preview.items, brand, images, padding='10px 0', picture=True)}"
        "</td></tr>"
        if preview.items
        else ""
    )
    return (
        '<table role="presentation" class="m-rule" width="100%" cellpadding="0" '
        f'cellspacing="0" border="0" style="margin:24px 0 4px;border:1px solid '
        f'{_BORDER};border-radius:12px;border-collapse:separate;">'
        f'<tr><td style="padding:16px;">{head}</td></tr>{rows}</table>'
    )


def _html(brand: EmailBrand, content: EmailContent, images: _Images) -> str:
    tone = brand.accent if content.tone == "neutral" else _TONES[content.tone]
    logo = (
        f'<img src="{escape(brand.logo_url)}" width="32" height="32" alt="" '
        'style="display:block;width:32px;height:32px;border:0;border-radius:8px;">'
        if brand.logo_url
        else ""
    )
    header = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        + (
            f'<td valign="middle" style="padding-right:10px;">{logo}</td>'
            if logo
            else ""
        )
        + f'<td valign="middle"><a class="m-text" href="{escape(brand.url)}" '
        f'target="_blank" style="font-family:{brand.display_font};font-size:19px;'
        f'color:{_TEXT};text-decoration:none;">{escape(brand.name)}</a></td>'
        "</tr></table>"
    )
    label = (
        f'<div style="margin:0 0 14px;"><span class="m-label" '
        f'style="display:inline-block;'
        f"padding:4px 10px;border-radius:999px;background:{_mix(tone, _CARD, 0.12)};"
        f"color:{tone};font-size:12px;font-weight:700;letter-spacing:0.02em;"
        f'line-height:1.4;">{escape(content.label)}</span></div>'
        if content.label
        else ""
    )
    body = "".join(
        _paragraph(text, f"font-size:15.5px;line-height:1.65;color:{_BODY};")
        for text in content.paragraphs
    )
    action = (
        _button(content.action, brand.accent, brand.body_font) if content.action else ""
    )
    preview = (
        _preview(content.preview, brand, images) if content.preview is not None else ""
    )
    sections = "".join(_section(section, brand, images) for section in content.sections)
    note = (
        f'<p class="m-muted m-rule" style="margin:20px 0 0;padding-top:18px;'
        f"border-top:1px solid {_BORDER};font-size:13px;line-height:1.6;"
        f'color:{_MUTED};">{_text(content.note)}</p>'
        if content.note
        else ""
    )
    footer = "".join(
        _paragraph(text, f"font-size:12.5px;line-height:1.6;color:{_MUTED};", "m-muted")
        for text in content.footer
    )
    links = [content.footer_link, EmailLink(brand.name, brand.url)]
    footer += (
        '<p style="margin:0;font-size:12.5px;line-height:1.6;">'
        + "&nbsp;&middot;&nbsp;".join(
            f'<a href="{escape(link.url)}" target="_blank" style="color:{_MUTED};'
            f'text-decoration:underline;" class="m-muted">{escape(link.label)}</a>'
            for link in links
            if link is not None
        )
        + "</p>"
    )
    preheader = (
        '<div style="display:none;max-height:0;max-width:0;overflow:hidden;'
        f'opacity:0;mso-hide:all;">{escape(content.preheader)}{_PREHEADER_FILL}</div>'
        if content.preheader
        else ""
    )
    font = f"font-family:{brand.body_font};"
    heading = (
        '<h1 class="m-text m-heading" style="margin:0 0 16px;'
        f"font-family:{brand.display_font};font-size:26px;font-weight:400;"
        f'line-height:1.3;letter-spacing:-0.01em;color:{_TEXT};">'
        f"{_text(content.heading)}</h1>"
    )
    card = _table(
        f'<td class="m-pad" style="padding:36px 40px;{font}">'
        f"{label}{heading}{body}{action}{preview}{sections}{note}</td>"
    )
    column = _table(
        f'<td style="padding:0 6px 18px;">{header}</td></tr><tr>'
        f'<td class="m-card" style="background:{_CARD};'
        f'border:1px solid {_BORDER};border-radius:16px;">{card}</td></tr><tr>'
        f'<td align="center" style="padding:24px 24px 0;{font}">{footer}</td>',
        "max-width:560px;",
    )
    layout = _table(
        f'<td class="m-outer" align="center" style="padding:40px 16px 48px;{font}">'
        f"{column}</td>",
        f"background:{_PAGE};",
        "m-page",
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{escape(content.subject)}</title>
<style>
body {{ margin: 0; padding: 0; -webkit-text-size-adjust: 100%; }}
@media (max-width: 600px) {{
  .m-outer {{ padding: 20px 10px 32px !important; }}
  .m-pad {{ padding: 28px 22px !important; }}
  .m-heading {{ font-size: 23px !important; }}
}}
@media (prefers-color-scheme: dark) {{
  .m-page {{ background: #161412 !important; }}
  .m-card {{ background: {_DARK_CARD} !important; border-color: #36312a !important; }}
  .m-text {{ color: #f1ece4 !important; }}
  .m-body {{ color: #d6cfc5 !important; }}
  .m-muted {{ color: #a1978a !important; }}
  .m-rule {{ border-color: #36312a !important; }}
  .m-label {{ background: {_mix(tone, _DARK_CARD, 0.22)} !important;
    color: {_mix(tone, "#ffffff", 0.6)} !important; }}
  .m-link {{ color: {_mix(brand.accent, "#ffffff", 0.6)} !important; }}
  .m-initial {{ background: {_mix(brand.accent, _DARK_CARD, 0.22)} !important;
    color: {_mix(brand.accent, "#ffffff", 0.6)} !important; }}
}}
</style>
</head>
<body class="m-page" style="margin:0;padding:0;background:{_PAGE};">
{preheader}{layout}
</body>
</html>
"""


def _plain_items(lines: list[str], items: tuple[EmailItem, ...]) -> str:
    for item in items:
        lines.append(f"- {item.title}")
        if item.detail:
            lines.extend(f"  {line}" for line in item.detail.splitlines())
        if item.link:
            lines.append(f"  {item.link.label}: {item.link.url}")
    return "\n".join(lines)


def _plain(brand: EmailBrand, content: EmailContent) -> str:
    parts = [content.heading, *content.paragraphs]
    if content.action:
        parts.append(f"{content.action.label}: {content.action.url}")
    if content.preview is not None:
        preview = content.preview
        head = [preview.title, *([preview.text] if preview.text else [])]
        parts.append(_plain_items(head, preview.items))
    parts.extend(
        _plain_items([section.title.upper()], section.items)
        for section in content.sections
    )
    if content.note:
        parts.append(content.note)
    footer = list(content.footer)
    if content.footer_link:
        footer.append(f"{content.footer_link.label}: {content.footer_link.url}")
    footer.append(f"{brand.name}: {brand.url}")
    parts.append("-- \n" + "\n".join(footer))
    return "\n\n".join(parts) + "\n"


def render_email(brand: EmailBrand, content: EmailContent) -> RenderedEmail:
    """Return the subject, the plain text, the HTML and the inline pictures."""
    images = _Images(urlsplit(brand.url).hostname or "localhost")
    html = _html(brand, content, images)
    return RenderedEmail(
        subject=content.subject,
        text=_plain(brand, content),
        html=html,
        images=tuple(images.parts),
    )


def email_message(
    *,
    sender: str | Address,
    to: str | Address,
    brand: EmailBrand,
    content: EmailContent,
    reply_to: str | Address | None = None,
) -> EmailMessage:
    """Build the ``multipart/alternative`` message of one email.

    With pictures, the HTML part is a ``multipart/related`` part that also
    holds each picture inline. Raise ValueError for an invalid address or a
    line break in the subject.
    """
    rendered = render_email(brand, content)
    message = html_message(
        sender=sender,
        to=to,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
        reply_to=reply_to,
    )
    # ``html_message`` gives the text part first and the HTML part second.
    _plain_part, html = message.iter_parts()
    for content_id, image in rendered.images:
        maintype, subtype = image.content_type.split("/")
        html.add_related(
            image.content,
            maintype,
            subtype,
            cid=f"<{content_id}>",
            disposition="inline",
        )
    return message
