import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_db
from app.models import AdminUser, NewsAggregatorImport, Post
from app.news_aggregator import get_aggregator_client, news_aggregator_router
import app.news_aggregator as aggregator
from app.campaigns import render_email

ARTICLE_ID = UUID("b45c0340-59fc-41a1-83f8-5e5ab435d285")


def article(**changes):
    value = {
        "id": str(ARTICLE_ID), "title": "A useful update", "summary": "A short summary",
        "body": "A paragraph with **emphasis**.\n\n<script>alert(1)</script>\n\n[Unsafe](javascript:alert(1))\n\n[Data](data:text/html,unsafe)\n\n[Mail](mailto:reader@example.org)\n\n[Read more](https://example.org/story)",
        "language": "en", "status": "published", "slug": "useful-update",
        "image_url": "javascript:alert(1)", "metadata_json": {"source": "test"},
    }
    return {**value, **changes}


@asynccontextmanager
async def harness(tmp_path, handler):
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'aggregator.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(AdminUser(id=1, email="admin@example.com", password_hash="unused"))
        await db.commit()

    async def database():
        async with factory() as db:
            yield db

    async def client_override():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            yield client

    app = FastAPI()
    app.include_router(news_aggregator_router(lambda: SimpleNamespace(id=1)))
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_aggregator_client] = client_override
    origin = "https://news.example.org"
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(aggregator, "_configured_origin", lambda: (origin, "public-news"))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client, factory
        finally:
            await engine.dispose()


def test_browse_uses_server_config_and_filters_unpublished_non_english(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[article(), article(id="183f05d9-3f3d-40db-85b9-115c4f1d1d28", status="draft"), article(id="91764b83-4ee4-4a4e-8022-cc1dc9c3d999", language="de")])

    async def run():
        async with harness(tmp_path, handler) as (client, _):
            response = await client.get("/admin/news-aggregator/articles?limit=10&offset=20")
            assert response.status_code == 200
            assert [item["id"] for item in response.json()["articles"]] == [str(ARTICLE_ID)]
            assert response.json()["has_more"] is False
            assert requests[0].url.path == "/api/v1/public/public-news/articles"
            assert dict(requests[0].url.params) == {"limit": "10", "offset": "20", "language": "en"}
    asyncio.run(run())


def test_browse_full_mixed_page_keeps_next_page_available(tmp_path):
    def handler(request):
        return httpx.Response(200, json=[article(), article(id="183f05d9-3f3d-40db-85b9-115c4f1d1d28", language="de")])

    async def run():
        async with harness(tmp_path, handler) as (client, _):
            response = await client.get("/admin/news-aggregator/articles?limit=2")
            assert response.status_code == 200
            assert [item["id"] for item in response.json()["articles"]] == [str(ARTICLE_ID)]
            assert response.json()["has_more"] is True
    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {"title": "Hi"},
    {"title": "  A  "},
    {"body": " \n\t ", "summary": ""},
    {"body": " \n\t ", "summary": "A nonempty summary"},
])
def test_import_rejects_fields_that_would_break_existing_post_responses(tmp_path, changes):
    def handler(request):
        return httpx.Response(200, json=article(**changes))

    async def run():
        async with harness(tmp_path, handler) as (client, factory):
            response = await client.post("/admin/news-aggregator/import", json={"article_id": str(ARTICLE_ID)})
            assert response.status_code == 502
            assert response.json() == {"detail": "News aggregator returned an invalid article."}
            async with factory() as db:
                assert await db.scalar(select(Post.id)) is None
                assert await db.scalar(select(NewsAggregatorImport.id)) is None
    asyncio.run(run())


def test_import_is_idempotent_and_preserves_editor_and_published_content(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        data = article(title=f"Fetched version {len(calls)}")
        return httpx.Response(200, json=data)

    async def run():
        async with harness(tmp_path, handler) as (client, factory):
            first = await client.post("/admin/news-aggregator/import", json={"article_id": str(ARTICLE_ID)})
            assert first.status_code == 201 and first.json()["already_imported"] is False
            post_id = first.json()["post_id"]
            async with factory() as db:
                post = await db.get(Post, post_id)
                source = await db.scalar(select(NewsAggregatorImport))
                assert post.status == "draft" and post.title == "Fetched version 1"
                assert post.image_url is None
                assert source.article_id == str(ARTICLE_ID)
                post.title, post.content = "Editor's title", "Locally revised story"
                await db.commit()
            second = await client.post("/admin/news-aggregator/import", json={"article_id": str(ARTICLE_ID)})
            assert second.status_code == 201 and second.json() == {"post_id": post_id, "status": "draft", "already_imported": True}
            async with factory() as db:
                post = await db.get(Post, post_id)
                post.status = "published"
                await db.commit()
            third = await client.post("/admin/news-aggregator/import", json={"article_id": str(ARTICLE_ID)})
            assert third.json()["status"] == "published" and third.json()["already_imported"] is True
            async with factory() as db:
                post = await db.get(Post, post_id)
                assert post.title == "Editor's title" and post.content == "Locally revised story" and post.status == "published"
            assert len(calls) == 3
    asyncio.run(run())


def test_import_accepts_long_upstream_fields_and_resolves_relative_image(tmp_path):
    long_title = "T" * 300
    long_slug = "story-" + "x" * 314

    def handler(request):
        return httpx.Response(200, json=article(title=long_title, slug=long_slug, image_url="media/header.png"))

    async def run():
        async with harness(tmp_path, handler) as (client, factory):
            response = await client.post("/admin/news-aggregator/import", json={"article_id": str(ARTICLE_ID)})
            assert response.status_code == 201
            async with factory() as db:
                post = await db.get(Post, response.json()["post_id"])
                assert len(post.title) == 200
                assert len(post.slug) <= 220 and post.slug.endswith(ARTICLE_ID.hex)
                assert post.image_url == "https://news.example.org/media/header.png"
    asyncio.run(run())


@pytest.mark.parametrize("changes", [{"status": "draft"}, {"language": "de"}])
def test_import_rejects_nonpublished_or_nonenglish_article(tmp_path, changes):
    def handler(request):
        return httpx.Response(200, json=article(**changes))

    async def run():
        async with harness(tmp_path, handler) as (client, factory):
            response = await client.post("/admin/news-aggregator/import", json={"article_id": str(ARTICLE_ID)})
            assert response.status_code == 422
            async with factory() as db:
                assert await db.scalar(select(Post.id)) is None
    asyncio.run(run())


def test_upstream_redirect_and_oversized_body_are_sanitized(tmp_path):
    async def run():
        async with harness(tmp_path, lambda request: httpx.Response(302, headers={"Location": "https://private.example/secret"})) as (client, _):
            response = await client.get("/admin/news-aggregator/articles")
            assert response.status_code == 502
            assert "private.example" not in response.text
        async with harness(tmp_path / "oversized", lambda request: httpx.Response(200, content=b"x" * 1_000_001)) as (client, _):
            response = await client.get("/admin/news-aggregator/articles")
            assert response.status_code == 502 and "oversized" in response.text
    asyncio.run(run())


def test_configured_local_destination_is_rejected(monkeypatch):
    monkeypatch.setattr(aggregator, "get_settings", lambda: SimpleNamespace(
        news_aggregator_base_url="https://127.0.0.1", news_aggregator_tenant_slug="public-news"))
    with pytest.raises(aggregator.HTTPException) as exc:
        aggregator._configured_origin()
    assert exc.value.status_code == 503


@pytest.mark.parametrize("raw_url", ["https://[malformed", "https://news.example.org:bad"])
def test_malformed_configured_url_is_a_sanitized_unavailable_error(monkeypatch, raw_url):
    monkeypatch.setattr(aggregator, "get_settings", lambda: SimpleNamespace(
        news_aggregator_base_url=raw_url, news_aggregator_tenant_slug="public-news"))
    with pytest.raises(aggregator.HTTPException) as exc:
        aggregator._configured_origin()
    assert exc.value.status_code == 503
    assert "malformed" not in str(exc.value.detail) and "bad" not in str(exc.value.detail)


def test_email_markdown_keeps_text_and_blocks_html_and_unsafe_links():
    campaign = SimpleNamespace(introduction="Use **care**.\nLine two.")
    output = render_email(campaign, {"slug": "story", "title": "A <headline>", "content": article()["body"]})
    assert "<strong>care</strong>" in output
    assert "Use <strong>care</strong>.<br />" in output
    assert "&lt;script&gt;" in output and "<script>" not in output
    assert 'href="javascript:' not in output
    assert 'href="data:' not in output and 'href="mailto:' not in output
    assert 'href="https://example.org/story"' in output and '>Read more</a>' in output
