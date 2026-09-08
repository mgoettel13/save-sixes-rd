"""Site-owned subscriber consent and list memberships; Plunk delivers the emails."""
import csv
import hashlib
import hmac
import io
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import case, select, func, or_, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from .config import get_settings
from .db import get_db
from .models import NewsletterRateLimit, Subscriber, MailingList, ListMembership
from .mailing_import import ImportBatch, ImportPreview, parse_import
from .plunk import PlunkClient, get_plunk

CONSENT = "Receive Save Sixes Rd campaign news, meeting updates, and ways to get involved. Unsubscribe anytime."
def now():
    return datetime.now(timezone.utc)

class Signup(BaseModel):
    email: EmailStr
    website: str = Field(default="", max_length=300)
    consent: Literal[True]

class ContactEdit(BaseModel):
    first_name: str = Field(default="", max_length=150)
    last_name: str = Field(default="", max_length=150)
    list_ids: list[int] = Field(default_factory=list, max_length=50)

class ContactAdd(ContactEdit):
    email: EmailStr
    consent_confirmed: Literal[True]

class SubscriptionEdit(BaseModel):
    subscribed: Literal[False]

class ListEdit(BaseModel):
    name: str = Field(min_length=1, max_length=150, pattern=r"^[^,\r\n]+$")
    description: str = Field(default="", max_length=500)

class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=150)

async def list_ids_for(db, subscriber_id):
    return list(await db.scalars(select(ListMembership.list_id).where(ListMembership.subscriber_id == subscriber_id)))

async def public_contact(db, contact):
    return {"id": contact.id, "email": contact.email, "first_name": contact.first_name, "last_name": contact.last_name,
        "status": contact.status, "subscribed": contact.status == "subscribed", "source": contact.source,
        "created_at": contact.created_at, "list_ids": await list_ids_for(db, contact.id),
        "source_details": contact.source_details, "consent": contact.consent}

async def reserve_signup(db, kind, value, seconds, maximum):
    digest = hmac.new(get_settings().jwt_secret.encode(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()
    window = int(time.time()) // seconds
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(NewsletterRateLimit).values(key=digest, window=window, count=1)
    statement = statement.on_conflict_do_update(index_elements=[NewsletterRateLimit.key],
        set_={"window": window, "count": case((NewsletterRateLimit.window == window, NewsletterRateLimit.count + 1), else_=1)}
    ).returning(NewsletterRateLimit.count)
    count = await db.scalar(statement)
    await db.commit()
    if count > maximum:
        raise HTTPException(429, "Please wait before requesting another confirmation email.")

async def set_memberships(db, contact, ids):
    ids = set(ids)
    found = set(await db.scalars(select(MailingList.id).where(MailingList.id.in_(ids)))) if ids else set()
    if found != ids:
        raise HTTPException(422, "One of the selected mailing lists no longer exists.")
    await db.execute(delete(ListMembership).where(ListMembership.subscriber_id == contact.id))
    for list_id in ids:
        db.add(ListMembership(subscriber_id=contact.id, list_id=list_id))

async def import_rows(db, rows):
    results = []
    for row in rows:
        email = str(row.email).lower()
        if row.status == "unknown":
            results.append({"email": email, "result": "skipped", "message": "Subscription status is unknown."})
            continue
        contact = await db.scalar(select(Subscriber).where(Subscriber.email == email).with_for_update())
        if contact is None:
            contact = Subscriber(email=email, first_name=row.first_name, last_name=row.last_name,
                status=row.status, source="squarespace", source_details=row.source_details,
                consent="Squarespace export: Accepts Marketing=" + str(row.status == "subscribed").lower(),
                unsubscribe_token=secrets.token_urlsafe(32),
                confirmed_at=now() if row.status == "subscribed" else None,
                unsubscribed_at=now() if row.status == "unsubscribed" else None)
            db.add(contact)
            await db.flush()
            outcome = "created"
        else:
            outcome = "updated"
            if row.first_name: contact.first_name = row.first_name
            if row.last_name: contact.last_name = row.last_name
            contact.source_details = row.source_details
            if row.status == "unsubscribed":
                contact.status = "unsubscribed"
                contact.unsubscribed_at = contact.unsubscribed_at or now()
                contact.confirm_hash = None
        existing_ids = set(await list_ids_for(db, contact.id))
        for name in row.lists:
            mailing_list = await db.scalar(select(MailingList).where(MailingList.name == name.strip()))
            if not mailing_list:
                mailing_list = MailingList(name=name.strip(), description="Imported from Squarespace")
                db.add(mailing_list)
                await db.flush()
            if mailing_list.id not in existing_ids:
                db.add(ListMembership(subscriber_id=contact.id, list_id=mailing_list.id))
                existing_ids.add(mailing_list.id)
        await db.flush()
        results.append({"email": email, "result": outcome, "subscribed": contact.status == "subscribed"})
    await db.commit()
    return results

def mailing_router(require_admin):
    router = APIRouter(tags=["Mailing list"])
    admin = APIRouter(prefix="/admin/mailing-list", dependencies=[Depends(require_admin)])

    @router.post("/newsletter/signup", status_code=202)
    async def signup(payload: Signup, request: Request, db: AsyncSession = Depends(get_db), plunk: PlunkClient = Depends(get_plunk)):
        message = {"message": "If confirmation is needed, check your inbox for a confirmation link."}
        if payload.website: return message
        settings = get_settings()
        if not settings.newsletter_enabled or not settings.plunk_secret_key or not settings.plunk_from_address:
            raise HTTPException(503, "Email updates are temporarily unavailable. Please try again later.")
        email = str(payload.email).lower()
        await reserve_signup(db, "ip", request.client.host if request.client else "unknown", 3600, 10)
        await reserve_signup(db, "email", email, 86400, 3)
        contact = await db.scalar(select(Subscriber).where(Subscriber.email == email).with_for_update())
        if contact and contact.status == "subscribed": return message
        token = secrets.token_urlsafe(32)
        if not contact:
            contact = Subscriber(email=email, unsubscribe_token=secrets.token_urlsafe(32), status="pending", source="website")
            db.add(contact)
        contact.confirm_hash = hashlib.sha256(token.encode()).hexdigest()
        contact.confirm_expires_at = now() + timedelta(hours=24)
        contact.consent = CONSENT
        await db.flush()
        await db.commit()
        await plunk.send_confirmation(email, token, contact.unsubscribe_token)
        return message

    @router.post("/newsletter/confirm")
    async def confirm(payload: TokenRequest, db: AsyncSession = Depends(get_db)):
        contact = await db.scalar(select(Subscriber).where(Subscriber.confirm_hash == hashlib.sha256(payload.token.encode()).hexdigest(),
            Subscriber.confirm_expires_at > now()).with_for_update())
        if not contact: raise HTTPException(400, "This confirmation link has expired or has already been used. Please sign up again if needed.")
        contact.status = "subscribed"
        contact.confirmed_at = now()
        contact.confirm_hash = None
        contact.confirm_expires_at = None
        contact.unsubscribed_at = None
        mailing_list = await db.scalar(select(MailingList).where(MailingList.name == "master"))
        if mailing_list and mailing_list.id not in await list_ids_for(db, contact.id):
            db.add(ListMembership(subscriber_id=contact.id, list_id=mailing_list.id))
        await db.commit()
        return {"message": "You're subscribed to Save Sixes Rd updates."}

    @router.post("/newsletter/unsubscribe")
    async def unsubscribe_public(request: Request, token: str | None = Query(default=None, min_length=20, max_length=150), db: AsyncSession = Depends(get_db)):
        if not token:
            try: token = TokenRequest.model_validate(await request.json()).token
            except Exception: raise HTTPException(422, "A valid unsubscribe token is required.")
        contact = await db.scalar(select(Subscriber).where(Subscriber.unsubscribe_token == token).with_for_update())
        if not contact: raise HTTPException(400, "This unsubscribe link is invalid.")
        contact.status = "unsubscribed"
        contact.unsubscribed_at = now()
        contact.confirm_hash = None
        await db.commit()
        return {"message": "You have been unsubscribed from Save Sixes Rd emails."}

    @admin.get("/status")
    async def configuration():
        settings = get_settings()
        return {"configured": bool(settings.plunk_secret_key), "sender_configured": bool(settings.plunk_from_address),
            "sender": settings.plunk_from_address, "reply_to": settings.plunk_reply_to, "signup_enabled": settings.newsletter_enabled}

    @admin.post("/import/preview")
    async def preview(payload: ImportPreview):
        try: return parse_import(payload)
        except (ValueError, csv.Error) as exc: raise HTTPException(422, str(exc)) from exc

    @admin.post("/import")
    async def import_contacts(payload: ImportBatch, db: AsyncSession = Depends(get_db)):
        return {"results": await import_rows(db, payload.rows)}

    @admin.get("/lists")
    async def lists(db: AsyncSession = Depends(get_db)):
        result = []
        for item in await db.scalars(select(MailingList).order_by(MailingList.name)):
            rows = list(await db.scalars(select(Subscriber.status).join(ListMembership).where(ListMembership.list_id == item.id)))
            result.append({"id": item.id, "name": item.name, "description": item.description, "contacts": len(rows), "subscribed": rows.count("subscribed")})
        return result

    @admin.post("/lists", status_code=201)
    async def create_list(payload: ListEdit, db: AsyncSession = Depends(get_db)):
        name = payload.name.strip()
        if not name: raise HTTPException(422, "Enter a list name.")
        if await db.scalar(select(MailingList.id).where(func.lower(MailingList.name) == name.lower())):
            raise HTTPException(409, "A mailing list with this name already exists.")
        item = MailingList(name=name, description=payload.description)
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return {"id": item.id, "name": item.name, "description": item.description}

    @admin.patch("/lists/{list_id}")
    async def edit_list(list_id: int, payload: ListEdit, db: AsyncSession = Depends(get_db)):
        item = await db.get(MailingList, list_id)
        if not item: raise HTTPException(404, "Mailing list not found.")
        name = payload.name.strip()
        if not name: raise HTTPException(422, "Enter a list name.")
        duplicate = await db.scalar(select(MailingList.id).where(func.lower(MailingList.name) == name.lower(), MailingList.id != list_id))
        if duplicate: raise HTTPException(409, "A mailing list with this name already exists.")
        item.name = name
        item.description = payload.description
        await db.commit()
        return {"id": item.id, "name": item.name, "description": item.description}

    @admin.get("/contacts")
    async def contacts(search: str = Query(default="", max_length=320), subscribed: bool | None = None,
        list_id: int | None = None, cursor: int = Query(default=0, ge=0), db: AsyncSession = Depends(get_db)):
        query = select(Subscriber)
        if search: query = query.where(or_(Subscriber.email.icontains(search, autoescape=True), Subscriber.first_name.icontains(search, autoescape=True), Subscriber.last_name.icontains(search, autoescape=True)))
        if subscribed is not None: query = query.where(Subscriber.status == "subscribed" if subscribed else Subscriber.status != "subscribed")
        if list_id: query = query.join(ListMembership).where(ListMembership.list_id == list_id)
        total = await db.scalar(select(func.count()).select_from(query.subquery()))
        rows = list(await db.scalars(query.where(Subscriber.id > cursor).order_by(Subscriber.id).limit(51)))
        return {"contacts": [await public_contact(db, item) for item in rows[:50]], "total": total,
                "has_more": len(rows) > 50, "cursor": rows[49].id if len(rows) > 50 else None}

    @admin.get("/export")
    async def export(db: AsyncSession = Depends(get_db)):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Email", "First Name", "Last Name", "Status", "Mailing Lists", "Source", "Created On", "Subscriber Since", "Subscriber Source", "Accepts Marketing"])
        names = {item.id: item.name for item in await db.scalars(select(MailingList))}
        for item in await db.scalars(select(Subscriber).order_by(Subscriber.id)):
            source = item.source_details or {}
            cells = [item.email, item.first_name, item.last_name, item.status, ", ".join(names[k] for k in await list_ids_for(db, item.id)), item.source,
                source.get("createdon") or str(item.created_at), source.get("subscribersince", ""), source.get("subscribersource", ""), str(item.status == "subscribed").lower()]
            writer.writerow(["'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v for v in cells])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="save-sixes-mailing-list.csv"', "Cache-Control": "no-store"})

    @admin.post("/contacts", status_code=201)
    async def add(payload: ContactAdd, db: AsyncSession = Depends(get_db)):
        email = str(payload.email).lower()
        if await db.scalar(select(Subscriber.id).where(Subscriber.email == email)):
            raise HTTPException(409, "This address is already in the mailing list. Its subscription status has been preserved.")
        contact = Subscriber(email=email, first_name=payload.first_name, last_name=payload.last_name, status="subscribed", source="admin",
            consent="Existing permission confirmed by administrator", confirmed_at=now(), unsubscribe_token=secrets.token_urlsafe(32))
        db.add(contact)
        await db.flush()
        await set_memberships(db, contact, payload.list_ids)
        await db.commit()
        await db.refresh(contact)
        return await public_contact(db, contact)

    @admin.get("/contacts/{contact_id}")
    async def detail(contact_id: int, db: AsyncSession = Depends(get_db)):
        contact = await db.get(Subscriber, contact_id)
        if not contact: raise HTTPException(404, "Contact not found.")
        return await public_contact(db, contact)

    @admin.patch("/contacts/{contact_id}")
    async def edit(contact_id: int, payload: ContactEdit, db: AsyncSession = Depends(get_db)):
        contact = await db.get(Subscriber, contact_id)
        if not contact: raise HTTPException(404, "Contact not found.")
        contact.first_name = payload.first_name
        contact.last_name = payload.last_name
        await set_memberships(db, contact, payload.list_ids)
        await db.commit()
        await db.refresh(contact)
        return await public_contact(db, contact)

    @admin.patch("/contacts/{contact_id}/subscription")
    async def unsubscribe(contact_id: int, payload: SubscriptionEdit, db: AsyncSession = Depends(get_db)):
        contact = await db.get(Subscriber, contact_id)
        if not contact: raise HTTPException(404, "Contact not found.")
        contact.status = "unsubscribed"
        contact.unsubscribed_at = now()
        contact.confirm_hash = None
        await db.commit()
        await db.refresh(contact)
        return await public_contact(db, contact)

    router.include_router(admin)
    return router
