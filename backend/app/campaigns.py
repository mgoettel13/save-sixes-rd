"""Durable blog-email drafts and deliveries. Uncertain sends are never retried automatically."""
import asyncio
import hashlib
import json
import logging
import secrets
from datetime import timedelta
from html import escape
from urllib.parse import quote
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from .config import get_settings
from .db import get_db, session_factory
from .models import Post, Subscriber, MailingList, ListMembership, EmailCampaign, EmailDelivery
from .mailing import now
from .plunk import PlunkClient, PlunkError, get_plunk

class CampaignEdit(BaseModel):
    post_id: int
    subject: str = Field(min_length=1, max_length=200, pattern=r'^[^\r\n]+$')
    introduction: str = Field(default='', max_length=10000)
    list_ids: list[int] = Field(default_factory=list, max_length=50)

class TestSend(BaseModel):
    email: EmailStr

class SendCampaign(BaseModel):
    preview_token: str
    expected_recipients: int = Field(gt=0)

async def audience_ids(db, lists):
    query = select(Subscriber.id).where(Subscriber.status == 'subscribed')
    if lists:
        query = query.where(Subscriber.id.in_(select(ListMembership.subscriber_id).where(ListMembership.list_id.in_(lists))))
    return list(await db.scalars(query.order_by(Subscriber.id)))

def snapshot_for(post):
    return {'title': post.title, 'slug': post.slug, 'content': post.content, 'excerpt': post.excerpt}

def render_email(campaign, snapshot, unsubscribe_token=None, test=False):
    settings = get_settings()
    url = escape(f"{settings.site_url.rstrip('/')}/blog/{quote(snapshot['slug'], safe='')}", quote=True)
    def paragraphs(value):
        return ''.join('<p style="white-space:pre-wrap">' + escape(p) + '</p>' for p in value.split('\n\n') if p.strip())
    footer = '<p>This is a preview. No mailing-list audience was emailed.</p>' if test else ''
    if unsubscribe_token:
        unsub = escape(f"{settings.site_url.rstrip('/')}/newsletter/unsubscribe#token={quote(unsubscribe_token)}", quote=True)
        footer += f'<p><a href="{unsub}">Unsubscribe from Save Sixes Rd emails</a></p>'
    elif not test:
        footer += '<p>Each recipient receives their own unsubscribe link.</p>'
    return ('<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:#f5f3eb;color:#17392e">'
        '<main style="max-width:620px;margin:32px auto;padding:28px;font:17px/1.65 Georgia,serif;background:white">'
        '<p style="font:700 14px Arial,sans-serif;letter-spacing:2px">SAVE SIXES RD</p>'
        + ('<p><strong>TEST EMAIL</strong></p>' if test else '')
        + f"<h1>{escape(snapshot['title'])}</h1>" + paragraphs(campaign.introduction)
        + paragraphs(snapshot['content']) + f'<p><a href="{url}">View original post →</a></p><hr>'
        + '<footer style="font:13px/1.6 Arial,sans-serif">Save Sixes Rd<br>'
        + escape(settings.newsletter_postal_address) + footer + '</footer></main></body></html>')

async def campaign_view(db, item):
    counts = dict((await db.execute(select(EmailDelivery.status, func.count()).where(EmailDelivery.campaign_id == item.id).group_by(EmailDelivery.status))).all())
    return {'id': item.id, 'post_id': item.post_id, 'subject': item.subject, 'introduction': item.introduction,
        'list_ids': item.audience, 'status': item.status, 'created_at': item.created_at,
        'queued_at': item.queued_at, 'completed_at': item.completed_at, 'counts': counts}

async def preview_data(db, item):
    post = await db.get(Post, item.post_id)
    if not post or post.status != 'published':
        raise HTTPException(409, 'Publish this blog post before sharing it by email.')
    ids = await audience_ids(db, item.audience)
    snapshot = snapshot_for(post)
    fingerprint = hashlib.sha256(json.dumps([item.subject, item.introduction, item.audience, snapshot, ids], sort_keys=True).encode()).hexdigest()
    return {'recipient_count': len(ids), 'preview_token': fingerprint, 'html': render_email(item, snapshot)}, ids, snapshot

def campaign_router(require_admin):
    router = APIRouter(prefix='/admin/campaigns', tags=['Blog emails'], dependencies=[Depends(require_admin)])

    async def get_campaign(db, campaign_id, lock=False):
        query = select(EmailCampaign).where(EmailCampaign.id == campaign_id)
        item = await db.scalar(query.with_for_update() if lock else query)
        if not item: raise HTTPException(404, 'Email draft not found.')
        return item

    async def validate_edit(db, payload):
        if not payload.subject.strip(): raise HTTPException(422, 'Enter an email subject.')
        post = await db.get(Post, payload.post_id)
        if not post or post.status != 'published': raise HTTPException(409, 'Choose a published blog post.')
        found = set(await db.scalars(select(MailingList.id).where(MailingList.id.in_(payload.list_ids))))
        if found != set(payload.list_ids): raise HTTPException(422, 'A selected mailing list no longer exists.')

    @router.get('')
    async def campaigns(db: AsyncSession = Depends(get_db)):
        items = await db.scalars(select(EmailCampaign).order_by(EmailCampaign.created_at.desc()).limit(200))
        return [await campaign_view(db, item) for item in items]

    @router.post('', status_code=201)
    async def create(payload: CampaignEdit, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
        await validate_edit(db, payload)
        item = EmailCampaign(post_id=payload.post_id, subject=payload.subject.strip(), introduction=payload.introduction,
            audience=sorted(set(payload.list_ids)), created_by=admin.id)
        db.add(item)
        await db.commit()
        return await campaign_view(db, item)

    @router.get('/{campaign_id}')
    async def detail(campaign_id: int, db: AsyncSession = Depends(get_db)):
        item = await get_campaign(db, campaign_id)
        result = await campaign_view(db, item)
        if item.status == 'draft':
            try: result['preview'] = (await preview_data(db, item))[0]
            except HTTPException as exc:
                if exc.status_code != 409: raise
                result['preview'] = {'html': '<p>Choose a published post and save to preview this email.</p>', 'recipient_count': 0}
        else:
            result['preview'] = {'html': render_email(item, item.snapshot), 'recipient_count': sum(result['counts'].values())}
        result['deliveries'] = [{'email': email, 'status': status, 'error': error} for email, status, error in (await db.execute(
            select(Subscriber.email, EmailDelivery.status, EmailDelivery.error).join(EmailDelivery).where(EmailDelivery.campaign_id == item.id).order_by(EmailDelivery.id).limit(5000))).all()]
        return result

    @router.patch('/{campaign_id}')
    async def edit(campaign_id: int, payload: CampaignEdit, db: AsyncSession = Depends(get_db)):
        item = await get_campaign(db, campaign_id, True)
        if item.status != 'draft': raise HTTPException(409, 'Only drafts can be edited.')
        await validate_edit(db, payload)
        item.post_id, item.subject, item.introduction, item.audience = payload.post_id, payload.subject.strip(), payload.introduction, sorted(set(payload.list_ids))
        await db.commit()
        return await campaign_view(db, item)

    @router.post('/{campaign_id}/test')
    async def send_test(campaign_id: int, payload: TestSend, db: AsyncSession = Depends(get_db), plunk: PlunkClient = Depends(get_plunk)):
        item = await get_campaign(db, campaign_id)
        if item.status == 'draft': snapshot = (await preview_data(db, item))[2]
        else: snapshot = item.snapshot
        provider_id = await plunk.send_email(str(payload.email).lower(), '[TEST] ' + item.subject,
            render_email(item, snapshot, test=True), 'test-' + secrets.token_hex(20))
        return {'message': 'Plunk accepted the test email. Check your inbox to confirm delivery.', 'provider_id': provider_id}

    @router.post('/{campaign_id}/send', status_code=202)
    async def queue(campaign_id: int, payload: SendCampaign, db: AsyncSession = Depends(get_db)):
        settings = get_settings()
        if not settings.campaign_worker_enabled or not settings.plunk_secret_key:
            raise HTTPException(503, 'Email sending is currently paused.')
        item = await get_campaign(db, campaign_id, True)
        if item.status != 'draft': raise HTTPException(409, 'This email has already been queued or sent.')
        preview, ids, snapshot = await preview_data(db, item)
        if preview['preview_token'] != payload.preview_token or len(ids) != payload.expected_recipients:
            raise HTTPException(409, 'The post, draft or audience changed. Refresh the preview before sending.')
        item.snapshot, item.status, item.queued_at = snapshot, 'queued', now()
        for subscriber_id in ids:
            db.add(EmailDelivery(campaign_id=item.id, subscriber_id=subscriber_id, idempotency_key='sixes-' + secrets.token_hex(24)))
        await db.commit()
        return await campaign_view(db, item)

    @router.post('/{campaign_id}/cancel')
    async def cancel(campaign_id: int, db: AsyncSession = Depends(get_db)):
        item = await get_campaign(db, campaign_id, True)
        if item.status not in ('queued', 'sending'): raise HTTPException(409, 'This email is not queued.')
        await db.execute(update(EmailDelivery).where(EmailDelivery.campaign_id == item.id, EmailDelivery.status == 'queued').values(status='cancelled', finished_at=now()))
        # A provider request already in progress may still complete.
        item.status = 'cancelled'
        await db.commit()
        return await campaign_view(db, item)
    return router

async def process_delivery(factory, plunk):
    async with factory() as db:
        # A crash after provider acceptance cannot safely be retried.
        await db.execute(update(EmailDelivery).where(EmailDelivery.status == 'sending', EmailDelivery.started_at < now() - timedelta(minutes=5)).values(
            status='unknown', error='Worker interrupted. Check Plunk before sending again.', finished_at=now()))
        delivery = await db.scalar(select(EmailDelivery).where(EmailDelivery.status == 'queued').order_by(EmailDelivery.id).with_for_update(skip_locked=True).limit(1))
        if delivery:
            delivery.status, delivery.started_at = 'sending', now()
            delivery_id = delivery.id
        else: delivery_id = None
        await db.commit()
    if delivery_id:
        async with factory() as db:
            delivery = await db.get(EmailDelivery, delivery_id)
            campaign = await db.get(EmailCampaign, delivery.campaign_id)
            # The lock orders an unsubscribe against this particular send.
            contact = await db.scalar(select(Subscriber).where(Subscriber.id == delivery.subscriber_id).with_for_update())
            if campaign.status == 'cancelled': delivery.status = 'cancelled'
            elif not contact or contact.status != 'subscribed': delivery.status = 'skipped'
            else:
                try:
                    delivery.provider_id = await plunk.send_email(contact.email, campaign.subject,
                        render_email(campaign, campaign.snapshot, contact.unsubscribe_token), delivery.idempotency_key, contact.unsubscribe_token)
                    delivery.status = 'accepted'
                except PlunkError as exc:
                    delivery.status = 'unknown' if exc.uncertain else 'failed'
                    delivery.error = str(exc)[:500]
            delivery.finished_at = now()
            await db.commit()
    async with factory() as db:
        for campaign in await db.scalars(select(EmailCampaign).where(EmailCampaign.status.in_(['queued', 'sending'])).with_for_update(skip_locked=True)):
            states = list(await db.scalars(select(EmailDelivery.status).where(EmailDelivery.campaign_id == campaign.id)))
            if any(s in ('queued', 'sending') for s in states): campaign.status = 'sending'
            else:
                campaign.status = 'attention' if any(s in ('failed', 'unknown') for s in states) else 'completed'
                campaign.completed_at = now()
        await db.commit()
    return bool(delivery_id)

async def campaign_worker():
    async with httpx.AsyncClient(follow_redirects=False) as client:
        while True:
            try:
                worked = await process_delivery(session_factory, PlunkClient(client))
            except asyncio.CancelledError: raise
            except Exception:
                logging.getLogger(__name__).error('Campaign worker failed; outstanding sends will be reconciled without automatic retry.')
                worked = False
            await asyncio.sleep(0.2 if worked else 5)
