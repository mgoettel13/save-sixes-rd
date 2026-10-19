from html.parser import HTMLParser
from types import SimpleNamespace

from app.email_template import render_blog_email


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


SETTINGS = SimpleNamespace(site_url="https://example.org/", newsletter_postal_address="Community & neighbors")
# Existing stored campaign snapshots have no image field.
SNAPSHOT = {"title": "A <community> update", "slug": "a story", "content": "## Meeting details\n\nRead **the agenda**.\n\n- Bring questions", "excerpt": "The latest local news"}
CAMPAIGN = SimpleNamespace(introduction="Hello, neighbors.")


def test_legacy_snapshot_has_shared_branding_and_styled_reading_column():
    html = render_blog_email(CAMPAIGN, SNAPSHOT, SETTINGS)
    tags = Elements(html).tags
    assert any(t == "table" and a.get("width") == "600" and "max-width:600px" in a.get("style", "") for t, a in tags)
    images = [a for t, a in tags if t == "img"]
    assert [a["src"] for a in images] == ["https://example.org/source-assets/logo-primary.png", "https://example.org/source-assets/62-lakeAllatoona.jpg"]
    assert all(a.get("alt") and a.get("width") for a in images)
    # Typography survives removal of outer wrappers by email clients.
    for tag in ("h1", "h2", "p", "li"):
        assert any(t == tag and "font-size:" in a.get("style", "") for t, a in tags)
    assert "A &lt;community&gt; update" in html and "Community &amp; neighbors" in html
    assert '<strong>the agenda</strong>' in html
    assert 'href="https://example.org/blog/a%20story"' in html
    assert "<main" not in html and "<footer" not in html


def test_test_email_is_marked_and_has_no_recipient_unsubscribe_link():
    html = render_blog_email(CAMPAIGN, SNAPSHOT, SETTINGS, test=True)
    assert "TEST EMAIL" in html and "No mailing-list audience was emailed." in html
    assert "/newsletter/unsubscribe" not in html
    assert "Each recipient receives their own unsubscribe link." not in html


def test_live_email_preserves_individual_unsubscribe_and_escapes_content():
    html = render_blog_email(CAMPAIGN, SNAPSHOT, SETTINGS, unsubscribe_token='a/b?"&')
    assert 'href="https://example.org/newsletter/unsubscribe#token=a%2Fb%3F%22%26"' in html
    assert "TEST EMAIL" not in html and "No mailing-list audience" not in html
    assert "Each recipient receives their own unsubscribe link." not in html


def test_markdown_images_fit_column_and_unsafe_content_cannot_escape_template():
    snapshot = {**SNAPSHOT, "content": '![Local view](https://example.org/photo.jpg)\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))'}
    html = render_blog_email(CAMPAIGN, snapshot, SETTINGS)
    photo = next(a for t, a in Elements(html).tags if t == "img" and a.get("src") == "https://example.org/photo.jpg")
    assert "max-width:100%" in photo["style"]
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert 'href="javascript:' not in html
