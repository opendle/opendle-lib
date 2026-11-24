"""Tests for the shared email layout."""

from __future__ import annotations

import pytest

from opendle.mail_template import (
    EmailBrand,
    EmailContent,
    EmailImage,
    EmailItem,
    EmailLink,
    EmailPreview,
    EmailSection,
    email_message,
    render_email,
)

BRAND = EmailBrand(
    name="Example",
    url="https://app.example",
    logo_url="https://app.example/mark.png",
    accent="#A75931",
)
CONTENT = EmailContent(
    subject="Song is live",
    heading="Song <b> is live",
    paragraphs=("Published on YouTube.\nAnd on X.",),
    action=EmailLink("Open the post", "https://app.example/posts/1?a=1&b=2"),
    sections=(
        EmailSection(
            "Next steps",
            (
                EmailItem(
                    "Set the related video",
                    "Short: Song",
                    EmailLink("Open YouTube Studio", "https://studio.example/1"),
                ),
                EmailItem("Comment", "Tomorrow at 10:00"),
            ),
        ),
    ),
    note="Sent once.",
    label="Published",
    tone="success",
    preheader="All channels are live.",
    footer=("You get this email because email alerts are on.",),
    footer_link=EmailLink("Notification settings", "https://app.example/settings"),
)


def test_both_parts_come_from_the_same_content() -> None:
    """The text part has each value that the HTML part shows."""
    rendered = render_email(BRAND, CONTENT)
    assert rendered.subject == "Song is live"
    assert rendered.text == (
        "Song <b> is live\n\n"
        "Published on YouTube.\nAnd on X.\n\n"
        "Open the post: https://app.example/posts/1?a=1&b=2\n\n"
        "NEXT STEPS\n"
        "- Set the related video\n"
        "  Short: Song\n"
        "  Open YouTube Studio: https://studio.example/1\n"
        "- Comment\n"
        "  Tomorrow at 10:00\n\n"
        "Sent once.\n\n"
        "-- \n"
        "You get this email because email alerts are on.\n"
        "Notification settings: https://app.example/settings\n"
        "Example: https://app.example\n"
    )
    html = rendered.html
    assert "Song &lt;b&gt; is live" in html
    assert "Published on YouTube.<br>And on X." in html
    assert 'href="https://app.example/posts/1?a=1&amp;b=2"' in html
    assert 'src="https://app.example/mark.png"' in html
    assert "All channels are live." in html
    assert "#A75931" in html
    assert "#2f7a4f" in html  # The success tone colors the label.
    assert "Open YouTube Studio&nbsp;&rarr;" in html
    assert html.count("<script") == 0


def test_the_message_has_a_text_and_an_html_part() -> None:
    """A client without HTML support shows the text part."""
    message = email_message(
        sender="noreply@app.example",
        to="owner@example.com",
        brand=BRAND,
        content=CONTENT,
    )
    assert message.get_content_type() == "multipart/alternative"
    assert message["Subject"] == "Song is live"
    text = message.get_body(("plain",))
    html = message.get_body(("html",))
    assert text is not None
    assert html is not None
    assert "NEXT STEPS" in text.get_content()
    assert "<!doctype html>" in html.get_content()


def test_a_minimal_email_has_no_empty_parts() -> None:
    """Optional parts do not show when the content does not have them."""
    rendered = render_email(
        EmailBrand(name="Example", url="https://app.example"),
        EmailContent(subject="Hello", heading="Hello"),
    )
    assert rendered.text == "Hello\n\n-- \nExample: https://app.example\n"
    assert "<img" not in rendered.html
    assert "border-radius:999px" not in rendered.html


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,x", "https://a.example/ x", "/relative"],
)
def test_a_link_must_be_http(url: str) -> None:
    """The layout does not accept a link that can run code or is not absolute."""
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        EmailLink("Open", url)
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        EmailBrand(name="Example", url=url)


@pytest.mark.parametrize("accent", ["red", "#abc", "#a75931;x"])
def test_the_accent_must_be_a_hex_color(accent: str) -> None:
    """A color goes into style attributes, so only #rrggbb is accepted."""
    with pytest.raises(ValueError, match="rrggbb"):
        EmailBrand(name="Example", url="https://app.example", accent=accent)


def test_a_font_list_cannot_close_the_style() -> None:
    """A font list goes into style attributes without escaping."""
    with pytest.raises(ValueError, match="font list"):
        EmailBrand(name="Example", url="https://app.example", body_font='a" onload="x')


def test_a_row_can_have_only_a_title_and_a_link() -> None:
    """A row without a detail shows its link under the title in the text."""
    rendered = render_email(
        BRAND,
        EmailContent(
            subject="Hello",
            heading="Hello",
            sections=(
                EmailSection(
                    "Posts",
                    (EmailItem("X", link=EmailLink("Open", "https://x.example")),),
                ),
            ),
        ),
    )
    assert "POSTS\n- X\n  Open: https://x.example\n" in rendered.text


_JPEG = EmailImage(b"\xff\xd8jpeg", "image/jpeg", width=320, height=180)
_PNG = EmailImage(b"\x89PNGpicture", "image/png")
PREVIEW = EmailContent(
    subject="Song is live",
    heading="Song is live",
    action=EmailLink("Open", "https://app.example/posts/1"),
    preview=EmailPreview(
        "Song",
        "New single <now>",
        image=_JPEG,
        items=(
            EmailItem(
                "Studio", "YouTube", EmailLink("View", "https://y.example/1"), _PNG
            ),
            EmailItem("band page", "Facebook"),
            EmailItem("Studio 2", "Instagram", image=_PNG),
        ),
    ),
)


def test_a_preview_shows_a_card_with_its_rows() -> None:
    """The card has the title, the text and one row for each item."""
    rendered = render_email(BRAND, PREVIEW)
    assert rendered.text.startswith(
        "Song is live\n\n"
        "Open: https://app.example/posts/1\n\n"
        "Song\nNew single <now>\n"
        "- Studio\n  YouTube\n  View: https://y.example/1\n"
        "- band page\n  Facebook\n"
        "- Studio 2\n  Instagram\n\n"
    )
    html = rendered.html
    assert html.index(">Open</a>") < html.index("New single &lt;now&gt;")
    # The picture keeps its aspect ratio inside the 80 pixel square.
    assert 'width="80" height="45"' in html
    # A row without a picture shows the first letter of its title.
    assert ">B</div>" in html


def test_the_message_carries_each_different_picture_once() -> None:
    """Two rows with the same picture use one inline part."""
    rendered = render_email(BRAND, PREVIEW)
    assert [image for _cid, image in rendered.images] == [_JPEG, _PNG]
    assert all(cid.endswith("@app.example") for cid, _image in rendered.images)
    message = email_message(
        sender="noreply@app.example",
        to="owner@example.com",
        brand=BRAND,
        content=PREVIEW,
    )
    text, related = message.iter_parts()
    assert text.get_content_type() == "text/plain"
    assert related.get_content_type() == "multipart/related"
    html, *pictures = related.iter_parts()
    assert html.get_content_type() == "text/html"
    assert [part.get_content() for part in pictures] == [_JPEG.content, _PNG.content]
    for part in pictures:
        assert f'src="cid:{part["Content-ID"][1:-1]}"' in html.get_content()
    assert all(part.get_content_disposition() == "inline" for part in pictures)


def test_an_email_without_pictures_has_no_related_part() -> None:
    """The HTML part stays a single part when the content has no picture."""
    message = email_message(
        sender="noreply@app.example",
        to="owner@example.com",
        brand=BRAND,
        content=CONTENT,
    )
    assert [part.get_content_type() for part in message.iter_parts()] == [
        "text/plain",
        "text/html",
    ]


@pytest.mark.parametrize(
    ("content_type", "width"),
    [("image/svg+xml", None), ("image/webp", None), ("image/png", 0)],
)
def test_an_image_must_be_a_common_picture(
    content_type: str, width: int | None
) -> None:
    """Clients do not show SVG or WebP pictures reliably."""
    with pytest.raises(ValueError, match="email image"):
        EmailImage(b"x", content_type, width=width)
