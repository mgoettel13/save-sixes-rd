"""Server-side Plunk transport. The website owns its mailing-list consent records."""

from collections.abc import AsyncIterator
from urllib.parse import quote
from html import escape

import httpx

from .config import get_settings


class PlunkError(Exception):
    """A safe error that may be displayed without exposing provider data or secrets."""

    def __init__(self, message: str, status_code: int = 502, uncertain: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.uncertain = uncertain


class PlunkClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def request(self, method: str, path: str, **kwargs):
        settings = get_settings()
        if not settings.plunk_secret_key:
            raise PlunkError("Mailing list unavailable: Plunk has not been configured.", 503)
        base = settings.plunk_api_base_url.rstrip("/")
        if not base.startswith("https://"):
            raise PlunkError("Plunk requires an HTTPS API address.", 503)
        try:
            response = await self.client.request(
                method, f"{base}{path}",
                headers={"Authorization": f"Bearer {settings.plunk_secret_key}", **kwargs.pop("headers", {})},
                timeout=30, **kwargs,
            )
        except httpx.HTTPError as exc:
            raise PlunkError("Plunk could not be reached. Please refresh before retrying.") from exc
        if response.status_code == 429:
            raise PlunkError("Plunk is busy. Please wait a moment and try again.", 503, uncertain=False)
        if not response.is_success:
            raise PlunkError(f"Plunk could not complete the request (HTTP {response.status_code}).", uncertain=response.status_code >= 500 or response.status_code == 409)
        try:
            data = response.json()
        except ValueError as exc:
            raise PlunkError("Plunk returned an unexpected response.") from exc
        if not isinstance(data, dict) or data.get("success") is False:
            raise PlunkError("Plunk could not complete the request.")
        return data

    async def contacts(self, **params):
        page = await self.request("GET", "/contacts", params={k: v for k, v in params.items() if v is not None})
        if not isinstance(page.get("data"), list) or not isinstance(page.get("hasMore"), bool):
            raise PlunkError("Plunk returned an unexpected contact list. No changes were made.")
        for contact in page["data"]:
            if not isinstance(contact, dict) or not contact.get("id") or not contact.get("email") or not isinstance(contact.get("subscribed"), bool):
                raise PlunkError("Plunk returned incomplete subscription data. No changes were made.")
        return page

    async def all_contacts(self, **params) -> AsyncIterator[dict]:
        cursor = None
        seen = set()
        while True:
            page = await self.contacts(limit=100, cursor=cursor, **params)
            for contact in page.get("data", []):
                yield contact
            if not page.get("hasMore"):
                break
            cursor = page.get("cursor")
            if not cursor or cursor in seen:
                raise PlunkError("Plunk returned incomplete pagination. Export was stopped.")
            seen.add(cursor)

    async def find_contact(self, email: str):
        async for contact in self.all_contacts(search=email):
            if contact.get("email", "").casefold() == email.casefold():
                return contact
        return None

    async def get_contact(self, contact_id: str):
        return await self.request("GET", f"/contacts/{quote(contact_id, safe='')}")

    async def create_contact(self, email: str, subscribed: bool, data: dict):
        return await self.request("POST", "/contacts", json={"email": email, "subscribed": subscribed, "data": data})

    async def update_contact(self, contact_id: str, **values):
        return await self.request("PATCH", f"/contacts/{quote(contact_id, safe='')}", json=values)

    async def send_email(self, email: str, subject: str, body: str, key: str, unsubscribe_token: str | None = None):
        settings = get_settings()
        if not settings.plunk_from_address:
            raise PlunkError("Mailing list unavailable: the sender has not been configured.", 503)
        payload = {
            "to": email,
            "from": {"email": settings.plunk_from_address, "name": settings.plunk_from_name},
            "subject": subject, "body": body, "reply": settings.plunk_reply_to,
        }
        if unsubscribe_token:
            payload["headers"] = {
                "List-Unsubscribe": f"<{settings.api_public_url.rstrip('/')}/newsletter/unsubscribe?token={quote(unsubscribe_token)}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        result = await self.request("POST", "/v1/send", json=payload, headers={"Idempotency-Key": key})
        try:
            provider_id = result["data"]["emails"][0]["email"]
            if result.get("success") is not True or not isinstance(provider_id, str) or not provider_id:
                raise ValueError()
            return provider_id
        except (KeyError, TypeError, IndexError, ValueError) as exc:
            raise PlunkError("The email outcome is uncertain. Check Plunk before sending again.") from exc

    async def send_confirmation(self, email: str, token: str, unsubscribe_token: str):
        import hashlib
        settings = get_settings()
        url = escape(f"{settings.site_url.rstrip('/')}/newsletter/confirm#token={quote(token)}", quote=True)
        return await self.send_email(email,
            "Confirm your Save Sixes Rd email updates", (
                "<h1>Stay informed about Sixes Road</h1>"
                "<p>Please confirm that you want to receive campaign news, meeting updates, and ways to get involved.</p>"
                f'<p><a href="{url}">Confirm my subscription</a></p>'
                "<p>If you did not request these updates, ignore this email. You will not be subscribed.</p>"
            ), "confirm-" + hashlib.sha256(token.encode()).hexdigest())


async def get_plunk():
    async with httpx.AsyncClient(follow_redirects=False) as client:
        yield PlunkClient(client)
