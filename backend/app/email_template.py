"""Shared, email-client-friendly presentation for blog campaign messages."""
from html import escape
from urllib.parse import quote, urlsplit

from markdown_it import MarkdownIt


def _safe_markdown_link(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in ("https", "http") and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


_markdown = MarkdownIt("commonmark", {"html": False, "breaks": True})
_markdown.validateLink = _safe_markdown_link

# Put essential styles on each element: inboxes may discard a wrapping element.
_text = "font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:26px;color:#263f35;"
_styles = {
    "p": _text + "margin:0 0 18px;",
    "h1": "font-family:Georgia,serif;font-size:28px;line-height:36px;color:#17392e;margin:28px 0 14px;",
    "h2": "font-family:Georgia,serif;font-size:23px;line-height:30px;color:#17392e;margin:28px 0 12px;",
    "h3": _text + "font-size:19px;font-weight:bold;margin:24px 0 10px;",
    "ul": _text + "margin:0 0 20px;padding-left:24px;",
    "ol": _text + "margin:0 0 20px;padding-left:24px;",
    "li": _text + "margin:0 0 8px;",
    "a": "color:#285c43;text-decoration:underline;overflow-wrap:anywhere;",
    "blockquote": _text + "margin:20px 0;padding:0 0 0 16px;border-left:3px solid #b9c9b6;",
    "img": "display:block;border:0;max-width:100%;height:auto;margin:16px 0;",
    "pre": "white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;",
}


def _render_markdown(value):
    tokens = _markdown.parse(value or "")

    def style_tokens(items):
        for token in items:
            if token.nesting != -1 and token.tag in _styles:
                token.attrSet("style", _styles[token.tag])
            if token.children:
                style_tokens(token.children)

    style_tokens(tokens)
    return _markdown.renderer.render(tokens, _markdown.options, {})


def render_blog_email(campaign, snapshot, settings, unsubscribe_token=None, test=False):
    site = settings.site_url.rstrip("/")
    home = escape(site + "/", quote=True)
    url = escape(f"{site}/blog/{quote(snapshot['slug'], safe='')}", quote=True)
    logo = escape(f"{site}/source-assets/logo-primary.png", quote=True)
    photo = escape(f"{site}/source-assets/62-lakeAllatoona.jpg", quote=True)
    title = escape(snapshot["title"])
    preheader = escape("Test preview — " if test else "") + escape(snapshot.get("excerpt") or snapshot["title"])
    small = "font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:20px;color:#64716a;"
    footer = '<p style="margin:12px 0 0;">This is a preview. No mailing-list audience was emailed.</p>' if test else ""
    if unsubscribe_token:
        unsub = escape(f"{site}/newsletter/unsubscribe#token={quote(unsubscribe_token, safe='')}", quote=True)
        footer += f'<p style="margin:12px 0 0;"><a href="{unsub}" style="color:#285c43;text-decoration:underline;">Unsubscribe from Save Sixes Rd emails</a></p>'
    elif not test:
        footer += '<p style="margin:12px 0 0;">Each recipient receives their own unsubscribe link.</p>'
    test_label = '<p style="font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:18px;color:#855c23;margin:0 0 14px;">TEST EMAIL · Preview only</p>' if test else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background-color:#f5f3eb;">
<div style="display:none;font-size:1px;line-height:1px;color:#f5f3eb;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f5f3eb" style="width:100%;border-collapse:collapse;background-color:#f5f3eb;">
<tr><td align="center" style="padding:24px 12px;">
<!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="width:100%;max-width:600px;border-collapse:collapse;background-color:#ffffff;">
<tr><td style="padding:24px 24px 20px;border-top:4px solid #285c43;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;"><tr>
<td width="80" valign="middle"><a href="{home}"><img src="{logo}" width="80" height="80" alt="Save Sixes Rd — Green Not Gridlock" style="display:block;width:80px;height:80px;border:0;"></a></td>
<td valign="middle" style="padding-left:18px;"><p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:20px;letter-spacing:2px;font-weight:bold;color:#285c43;margin:0 0 4px;">SAVE SIXES RD</p><p style="{small}margin:0;">News from our community</p></td>
</tr></table></td></tr>
<tr><td style="padding:0 24px;"><img src="{photo}" width="552" alt="Lake Allatoona and the surrounding woodland" style="display:block;width:100%;max-width:552px;height:auto;border:0;"></td></tr>
<tr><td style="padding:28px 24px 12px;{_text}">
{test_label}<h1 style="font-family:Georgia,serif;font-size:30px;line-height:38px;font-weight:bold;color:#17392e;margin:0 0 20px;">{title}</h1>
{_render_markdown(campaign.introduction)}{_render_markdown(snapshot['content'])}
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:8px 0 24px;"><tr><td bgcolor="#285c43" style="background-color:#285c43;border-radius:3px;mso-padding-alt:13px 20px;"><a href="{url}" style="display:inline-block;padding:13px 20px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:22px;font-weight:bold;color:#ffffff;text-decoration:none;">Read on the website &#8594;</a></td></tr></table>
</td></tr>
<tr><td style="padding:20px 24px 24px;border-top:1px solid #e4e8df;{small}"><p style="margin:0;"><strong>Save Sixes Rd</strong><br>{escape(settings.newsletter_postal_address)}</p>{footer}</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''
