import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.db import Base, get_db
from app.models import AdminUser, Post, Subscriber, EmailDelivery, EmailCampaign
from app.mailing import mailing_router, now
from app.mailing_import import ImportPreview, parse_import
from app.campaigns import campaign_router, process_delivery
from app.plunk import PlunkClient, PlunkError, get_plunk

class FakePlunk:
    def __init__(self): self.sends=[]; self.confirmations=[]; self.failure=None
    async def send_confirmation(self, email, token, unsubscribe_token): self.confirmations.append((email, token, unsubscribe_token))
    async def send_email(self, *args):
        self.sends.append(args)
        if self.failure: raise self.failure
        return 'provider-id'

@asynccontextmanager
async def harness(tmp_path):
    engine=create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'test.db'}")
    factory=async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(AdminUser(id=1,email='admin@example.com',password_hash='unused'))
        db.add(Post(id=1,title='Actual blog <title>',slug='actual-blog',content='First paragraph\n\n<script>alert(1)</script>',excerpt='An update',author_id=1,status='published',published_at=now()))
        await db.commit()
    async def database():
        async with factory() as db: yield db
    admin=lambda: SimpleNamespace(id=1)
    app=FastAPI(); app.include_router(mailing_router(admin)); app.include_router(campaign_router(admin))
    fake=FakePlunk();app.dependency_overrides[get_db]=database;app.dependency_overrides[get_plunk]=lambda:fake
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client: yield client,factory,fake
    await engine.dispose()

def test_csv_source_consent_lists_and_duplicate_suppression():
    result=parse_import(ImportPreview(csv_text='Email,Accepts Marketing,Mailing Lists,Subscriber Since\nA@example.com,true,master,2025-11-01\na@example.com,false,HOA_Meeting,2025-11-01\nb@example.com,true,"master, HOA_Meeting",2025-12-01\nbad,true,,\n'))
    assert result['duplicates']==1 and len(result['errors'])==1
    assert result['rows'][0]['status']=='unsubscribed'
    assert result['rows'][0]['lists']==['HOA_Meeting','master']
    assert result['rows'][1]['source_details']['subscribersince']=='2025-12-01'
    assert parse_import(ImportPreview(csv_text='Email\na@example.com'))['rows'][0]['status']=='unknown'
    with pytest.raises(ValueError): parse_import(ImportPreview(csv_text='Email,Email\na@example.com,b@example.com'))


def test_import_repeatable_preserves_optout_and_memberships(tmp_path):
    async def run():
        async with harness(tmp_path) as (client,factory,fake):
            payload={'consent_confirmed':True,'rows':[{'email':'a@example.com','status':'unsubscribed','lists':['master']},{'email':'b@example.com','status':'subscribed','lists':['master','HOA_Meeting']},{'email':'c@example.com','status':'unknown'}]}
            assert (await client.post('/admin/mailing-list/import',json=payload)).status_code==200
            payload['rows'][0]['status']='subscribed'
            assert (await client.post('/admin/mailing-list/import',json=payload)).status_code==200
            data=(await client.get('/admin/mailing-list/contacts')).json()
            assert data['total']==2 and data['contacts'][0]['subscribed'] is False
            lists=(await client.get('/admin/mailing-list/lists')).json()
            assert {l['name']:l['contacts'] for l in lists}=={'master':2,'HOA_Meeting':1}
            assert not fake.sends and not fake.confirmations
            assert (await client.post('/admin/mailing-list/contacts',json={'email':'a@example.com','consent_confirmed':True})).status_code==409
            assert (await client.patch('/admin/mailing-list/contacts/1/subscription',json={'subscribed':True})).status_code==422
            exported=await client.get('/admin/mailing-list/export')
            assert exported.status_code==200 and 'HOA_Meeting' in exported.text
            assert 'unsubscribe_token' not in exported.text
    asyncio.run(run())


def test_signup_confirmation_unsubscribe_old_link_and_limits(tmp_path):
    async def run():
        async with harness(tmp_path) as (client,factory,fake):
            assert (await client.post('/newsletter/signup',json={'email':'a@example.com'})).status_code==422
            signup={'email':'a@example.com','consent':True}
            assert (await client.post('/newsletter/signup',json=signup)).status_code==202
            _,token,unsub=fake.confirmations[-1]
            async with factory() as db: assert (await db.scalar(select(Subscriber))).status=='pending'
            assert (await client.get('/newsletter/confirm',params={'token':token})).status_code==405
            assert (await client.post('/newsletter/confirm',json={'token':token})).status_code==200
            assert (await client.post('/newsletter/unsubscribe',params={'token':unsub},content='List-Unsubscribe=One-Click')).status_code==200
            assert (await client.post('/newsletter/confirm',json={'token':token})).status_code==400
            assert (await client.post('/newsletter/signup',json=signup)).status_code==202
            async with factory() as db: assert (await db.scalar(select(Subscriber))).status=='unsubscribed'
            assert (await client.post('/newsletter/signup',json=signup)).status_code==202
            assert (await client.post('/newsletter/signup',json=signup)).status_code==429
            assert len(fake.confirmations)==3
            assert (await client.post('/newsletter/signup',json={**signup,'website':'bot'})).status_code==202
            assert len(fake.confirmations)==3
    asyncio.run(run())


async def prepare_campaign(client):
    await client.post('/admin/mailing-list/import',json={'consent_confirmed':True,'rows':[
        {'email':'a@example.com','status':'subscribed','lists':['master','HOA_Meeting']},
        {'email':'b@example.com','status':'subscribed','lists':['master']},
        {'email':'c@example.com','status':'unsubscribed','lists':['master']} ]})
    lists=(await client.get('/admin/mailing-list/lists')).json()
    response=await client.post('/admin/campaigns',json={'post_id':1,'subject':'Blog update','list_ids':[l['id'] for l in lists]})
    assert response.status_code==201, response.text
    data=(await client.get('/admin/campaigns/1')).json()
    assert data['preview']['recipient_count']==2
    assert '<script>' not in data['preview']['html'] and '&lt;script&gt;' in data['preview']['html']
    return {'expected_recipients':2,'preview_token':data['preview']['preview_token']}


def test_campaign_dedup_queue_guard_unsubscribe_before_send(tmp_path):
    async def run():
        async with harness(tmp_path) as (client,factory,fake):
            payload=await prepare_campaign(client)
            assert (await client.post('/admin/campaigns/1/test',json={'email':'test@example.com'})).status_code==200
            assert len(fake.sends)==1 and fake.sends[0][0]=='test@example.com'
            assert (await client.post('/admin/campaigns/1/send',json=payload)).status_code==202
            assert (await client.post('/admin/campaigns/1/send',json=payload)).status_code==409
            await client.patch('/admin/mailing-list/contacts/1/subscription',json={'subscribed':False})
            for _ in range(3): await process_delivery(factory,fake)
            assert len(fake.sends)==2 and fake.sends[1][0]=='b@example.com'
            data=(await client.get('/admin/campaigns/1')).json()
            assert data['counts']=={'accepted':1,'skipped':1} and data['status']=='completed'
            assert (await client.patch('/admin/campaigns/1',json={'post_id':1,'subject':'changed'})).status_code==409
    asyncio.run(run())


def test_changed_audience_requires_new_preview(tmp_path):
    async def run():
        async with harness(tmp_path) as (client,factory,fake):
            payload=await prepare_campaign(client)
            await client.patch('/admin/mailing-list/contacts/1/subscription',json={'subscribed':False})
            assert (await client.post('/admin/campaigns/1/send',json=payload)).status_code==409
            async with factory() as db: assert await db.scalar(select(func.count()).select_from(EmailDelivery))==0
    asyncio.run(run())


def test_uncertain_provider_result_is_not_retried(tmp_path):
    async def run():
        async with harness(tmp_path) as (client,factory,fake):
            payload=await prepare_campaign(client)
            await client.post('/admin/campaigns/1/send',json=payload)
            fake.failure=PlunkError('Timeout; check provider.',uncertain=True)
            for _ in range(4): await process_delivery(factory,fake)
            assert len(fake.sends)==2
            data=(await client.get('/admin/campaigns/1')).json()
            assert data['counts']=={'unknown':2} and data['status']=='attention'
    asyncio.run(run())


def test_cancel_prevents_remaining_delivery(tmp_path):
    async def run():
        async with harness(tmp_path) as (client,factory,fake):
            payload=await prepare_campaign(client)
            await client.post('/admin/campaigns/1/send',json=payload)
            assert (await client.post('/admin/campaigns/1/cancel')).status_code==200
            await process_delivery(factory,fake)
            assert not fake.sends
            assert (await client.get('/admin/campaigns/1')).json()['counts']=={'cancelled':2}
    asyncio.run(run())


def test_admin_auth():
    from app.main import app
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            for path in ['/admin/mailing-list/contacts','/admin/mailing-list/lists','/admin/mailing-list/export','/admin/campaigns']:
                assert (await client.get(path)).status_code==401
    asyncio.run(run())


def test_provider_contract_and_safe_failures():
    import json
    async def run():
        def respond(request):
            assert request.headers['idempotency-key']=='stable-id'
            body=json.loads(request.content)
            assert 'subscribed' not in body and 'List-Unsubscribe-Post' in body['headers']
            return httpx.Response(200,json={'success':True,'data':{'emails':[{'email':'provider-id'}]}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await PlunkClient(client).send_email('a@example.com','subject','body','stable-id','u'*32)=='provider-id'
        for code,uncertain in [(403,False),(409,True),(429,False),(500,True)]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(code,text='secret-provider-response'))) as client:
                with pytest.raises(PlunkError) as exc: await PlunkClient(client).send_email('a@example.com','s','b','k')
                assert exc.value.uncertain==uncertain and 'secret-provider-response' not in str(exc.value)
    asyncio.run(run())
