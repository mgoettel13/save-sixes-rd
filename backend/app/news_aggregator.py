"""Admin-only reader/importer for a configured public news aggregator feed."""
import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import quote, urljoin, urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .models import NewsAggregatorImport, Post
from .schemas import PostCreate

MAX_RESPONSE_BYTES = 1_000_000
TIMEOUT_SECONDS = 8.0


class AggregatorArticle(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=20_000)
    body: str = Field(min_length=1, max_length=200_000)
    language: str = Field(min_length=2, max_length=20)
    status: str = Field(min_length=1, max_length=20)
    slug: str = Field(min_length=1, max_length=320)
    image_url: str | None = Field(default=None, max_length=500)
    metadata_json: dict | list | str | int | float | bool | None = None


class ImportRequest(BaseModel):
    article_id: UUID


def _configured_origin() -> tuple[str, str]:
    settings = get_settings()
    raw = (settings.news_aggregator_base_url or "").strip()
    tenant = (settings.news_aggregator_tenant_slug or "").strip()
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise HTTPException(503, "News aggregator is not configured.") from None
    if not raw or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise HTTPException(503, "News aggregator is not configured.")
    if not tenant or len(tenant) > 120 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in tenant):
        raise HTTPException(503, "News aggregator is not configured.")

    try:
        addresses = {ipaddress.ip_address(hostname)} if _is_ip(hostname) else {
            ipaddress.ip_address(record[4][0]) for record in socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError):
        raise HTTPException(503, "News aggregator is unavailable.") from None
    if not addresses or any(not address.is_global for address in addresses):
        raise HTTPException(503, "News aggregator is unavailable.")
    origin = f"https://{parsed.netloc}".rstrip("/")
    return origin, tenant


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


async def _fetch_json(client: httpx.AsyncClient, url: str, params: dict | None = None):
    try:
        async with client.stream("GET", url, params=params) as response:
            if response.is_redirect:
                raise HTTPException(502, "News aggregator returned an invalid response.")
            if response.status_code != 200:
                raise HTTPException(502, "News aggregator is temporarily unavailable.")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise HTTPException(502, "News aggregator returned an oversized response.")
    except HTTPException:
        raise
    except (httpx.HTTPError, TimeoutError):
        raise HTTPException(502, "News aggregator is temporarily unavailable.") from None
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(502, "News aggregator returned an invalid response.") from None


async def get_aggregator_client():
    settings = get_settings()
    headers = {"Accept": "application/json"}
    if settings.news_aggregator_api_key:
        headers["Authorization"] = f"Bearer {settings.news_aggregator_api_key}"
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False, headers=headers) as client:
        yield client


def _parse_article(data) -> AggregatorArticle:
    try:
        return AggregatorArticle.model_validate(data)
    except ValidationError:
        raise HTTPException(502, "News aggregator returned an invalid article.") from None


def _image_url(value: str | None, origin: str) -> str | None:
    if not value:
        return None
    try:
        if value.startswith("//"):
            return None
        resolved = urljoin(origin.rstrip("/") + "/", value)
        parsed = urlsplit(resolved)
        if parsed.scheme.lower() in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
            return resolved
    except ValueError:
        pass
    return None


def news_aggregator_router(require_admin):
    router = APIRouter(prefix="/admin/news-aggregator", tags=["News aggregator"], dependencies=[Depends(require_admin)])

    @router.get("/articles")
    async def browse_articles(
        limit: int = Query(default=20, ge=1, le=50),
        offset: int = Query(default=0, ge=0, le=10_000),
        client: httpx.AsyncClient = Depends(get_aggregator_client),
    ):
        origin, tenant = await asyncio.to_thread(_configured_origin)
        url = f"{origin}/api/v1/public/{quote(tenant, safe='')}/articles"
        data = await _fetch_json(client, url, {"limit": limit, "offset": offset, "language": "en"})
        if not isinstance(data, list) or len(data) > limit:
            raise HTTPException(502, "News aggregator returned an invalid article list.")
        articles = [_parse_article(item) for item in data]
        return {"articles": [
            item.model_dump(mode="json")
            for item in articles
            if item.status == "published" and item.language.lower().startswith("en")
        ], "has_more": len(data) == limit}

    @router.get("/articles/{article_id}")
    async def article_detail(article_id: UUID, client: httpx.AsyncClient = Depends(get_aggregator_client)):
        origin, tenant = await asyncio.to_thread(_configured_origin)
        url = f"{origin}/api/v1/public/{quote(tenant, safe='')}/articles/{article_id}"
        article = _parse_article(await _fetch_json(client, url))
        if article.id != article_id or article.status != "published" or not article.language.lower().startswith("en"):
            raise HTTPException(404, "Article not found.")
        return article.model_dump(mode="json")

    @router.post("/import", status_code=201)
    async def import_article(
        payload: ImportRequest,
        admin=Depends(require_admin),
        db: AsyncSession = Depends(get_db),
        client: httpx.AsyncClient = Depends(get_aggregator_client),
    ):
        origin, tenant = await asyncio.to_thread(_configured_origin)
        url = f"{origin}/api/v1/public/{quote(tenant, safe='')}/articles/{payload.article_id}"
        article = _parse_article(await _fetch_json(client, url))
        if article.id != payload.article_id or article.status != "published" or not article.language.lower().startswith("en"):
            raise HTTPException(422, "Only published English articles can be imported.")

        imported = await db.scalar(select(NewsAggregatorImport).where(
            NewsAggregatorImport.source_origin == origin,
            NewsAggregatorImport.tenant_slug == tenant,
            NewsAggregatorImport.article_id == str(article.id),
        ))
        if imported:
            post = await db.get(Post, imported.post_id)
            if post is None:
                raise HTTPException(409, "The imported post record is unavailable.")
            return {"post_id": post.id, "status": post.status, "already_imported": True}

        base_slug = re.sub(r"[^a-z0-9]+", "-", article.slug.lower()).strip("-")[:170].strip("-") or "news"
        title = article.title.strip()[:200].rstrip()
        slug = f"{base_slug}-news-{article.id.hex}"
        summary = article.summary.strip() or article.body.strip()[:497].rstrip() + ("…" if len(article.body.strip()) > 497 else "")
        try:
            normalized = PostCreate(
                title=title, slug=slug, excerpt=summary[:500], content=article.body.strip(),
                image_url=_image_url(article.image_url, origin), status="draft",
            )
        except ValidationError:
            raise HTTPException(502, "News aggregator returned an invalid article.") from None
        post = Post(**normalized.model_dump(), author_id=admin.id)
        try:
            db.add(post)
            await db.flush()
            db.add(NewsAggregatorImport(source_origin=origin, tenant_slug=tenant, article_id=str(article.id), post_id=post.id))
            await db.commit()
        except IntegrityError:
            await db.rollback()
            imported = await db.scalar(select(NewsAggregatorImport).where(
                NewsAggregatorImport.source_origin == origin,
                NewsAggregatorImport.tenant_slug == tenant,
                NewsAggregatorImport.article_id == str(article.id),
            ))
            if imported:
                post = await db.get(Post, imported.post_id)
                return {"post_id": post.id, "status": post.status, "already_imported": True}
            raise HTTPException(409, "This article could not be imported. Refresh and try again.") from None
        await db.refresh(post)
        return {"post_id": post.id, "status": post.status, "already_imported": False}

    return router
