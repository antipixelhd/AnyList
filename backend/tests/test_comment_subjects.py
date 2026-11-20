"""The legacy TMDB comment namespace must not accept catalogue game IDs."""
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

os.environ.setdefault('SECRET_KEY', 'comment-subject-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from fastapi import FastAPI

from db import get_db
from dependencies import get_current_user, get_current_user_or_api_key
from routers.comments import router


class CommentSubjectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.viewer=SimpleNamespace(id=1,username='fixture',display_name='Fixture',profile=None,
                                    role='user',is_admin=False,games_access=False)
        result=Mock()
        result.scalar_one_or_none.return_value=None
        result.scalars.return_value.all.return_value=[]
        self.db=SimpleNamespace(execute=AsyncMock(return_value=result),add=Mock(),
                                commit=AsyncMock(),refresh=AsyncMock())
        async def refresh(comment):
            comment.id=10
            comment.created_at=datetime.now(timezone.utc)
        self.db.refresh.side_effect=refresh
        app=FastAPI();app.include_router(router,prefix='/comments')
        async def session():yield self.db
        app.dependency_overrides[get_db]=session
        app.dependency_overrides[get_current_user]=lambda:self.viewer
        app.dependency_overrides[get_current_user_or_api_key]=lambda:self.viewer
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    async def test_game_subject_reads_and_writes_are_rejected_before_sql(self):
        for approved in (False,True):
            self.viewer.games_access=approved
            for method in ('GET','POST'):
                with self.subTest(approved=approved,method=method):
                    values={'media_type':'game','tmdb_id':194}
                    if method=='GET':response=await self.client.get('/comments',params=values)
                    else:response=await self.client.post('/comments',json={**values,'content':'Game note'})
                    self.assertEqual(response.status_code,422,response.text)
        self.db.execute.assert_not_awaited()
        self.db.add.assert_not_called()
        self.db.commit.assert_not_awaited()

    async def test_existing_movie_series_and_person_subjects_remain_available(self):
        for kind in ('movie','series','season','episode','person'):
            with self.subTest(kind=kind):
                self.assertEqual((await self.client.get('/comments',params={'media_type':kind,'tmdb_id':1})).status_code,200)
                response=await self.client.post('/comments',json={'media_type':kind,'tmdb_id':1,'content':'Fixture'})
                self.assertEqual(response.status_code,200,response.text)

    async def test_unknown_subject_namespace_is_rejected(self):
        response=await self.client.post('/comments',json={'media_type':'catalogue:game','tmdb_id':194,'content':'Fixture'})
        self.assertEqual(response.status_code,422,response.text)
        self.db.execute.assert_not_awaited()

    async def test_existing_unsupported_subject_cannot_be_edited_or_deleted(self):
        comment=SimpleNamespace(media_type='game',user_id=self.viewer.id,content='Retained')
        result=Mock();result.scalar_one_or_none.return_value=comment
        self.db.execute.return_value=result
        for admin in (False,True):
            self.viewer.is_admin=admin;self.viewer.role='admin' if admin else 'user'
            for method in ('PATCH','DELETE'):
                with self.subTest(admin=admin,method=method):
                    response=await self.client.request(method,'/comments/10',
                        **({'json':{'content':'Changed'}} if method=='PATCH' else {}))
                    self.assertEqual(response.status_code,404,response.text)
        self.assertEqual(comment.content,'Retained')
        self.db.commit.assert_not_awaited()
        self.db.refresh.assert_not_awaited()

    async def test_supported_subject_edit_and_delete_keep_ownership_checks(self):
        comment=SimpleNamespace(media_type='movie',user_id=self.viewer.id,id=10,content='Old',
                                is_spoiler=False,updated_at=None)
        result=Mock();result.scalar_one_or_none.return_value=comment
        self.db.execute.return_value=result
        response=await self.client.patch('/comments/10',json={'content':'Corrected'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(comment.content,'Corrected')
        self.viewer.id=2
        for method in ('PATCH','DELETE'):
            response=await self.client.request(method,'/comments/10',
                **({'json':{'content':'Someone else'}} if method=='PATCH' else {}))
            self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(comment.content,'Corrected')
        self.viewer.id=1
        self.assertEqual((await self.client.delete('/comments/10')).status_code,200)
