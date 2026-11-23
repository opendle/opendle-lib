"""Tests for the shared email layout."""

from __future__ import annotations

import pytest

from opendle.mail_template import (
    EmailBrand,
    EmailContent,
    EmailItem,
    EmailLink,
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
