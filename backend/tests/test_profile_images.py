"""Profile uploads are bounded, decoded and normalized; provider artwork is separate."""
import io
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from PIL import Image

os.environ.setdefault('SECRET_KEY','profile-test')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core import profile_images,image_cache,backup
from schemas import BackgroundCrop


def png(width=800,height=400):
    output=io.BytesIO();Image.new('RGB',(width,height),'#3db4f2').save(output,'PNG');return output.getvalue()


class ProfileImageTests(unittest.TestCase):
    def test_share_card_keeps_uploaded_background_separate_from_remote_metadata(self):
        from core.share_preview import profile_card
        card=profile_card(SimpleNamespace(id=7,username='player',display_name=None,
            profile=SimpleNamespace(background_url='/api/proxy/profile/background/7')),None)
        self.assertEqual(card['background_upload_user_id'],7)
    def test_uploads_are_real_webp_at_their_target_dimensions(self):
        for kind,dimensions in profile_images.DIMENSIONS.items():
            with self.subTest(kind=kind):
                encoded=profile_images.normalize(png(),kind,BackgroundCrop(x=20,y=70,zoom=1.5))
                with Image.open(io.BytesIO(encoded)) as result:
                    self.assertEqual(result.format,'WEBP');self.assertEqual(result.size,dimensions)

    def test_upload_validation_uses_decoded_content_not_file_name(self):
        for content in (b'<svg onload="alert(1)"></svg>',b'not an image'):
            with self.assertRaises(ValueError):profile_images.normalize(content,'avatar',BackgroundCrop())
        with patch.object(profile_images.Image,'open') as opened:
            opened.return_value.__enter__.return_value=SimpleNamespace(format='PNG',is_animated=False,width=6000,height=5000)
            with self.assertRaisesRegex(ValueError,'25 megapixels'):profile_images.normalize(b'large','avatar',BackgroundCrop())
        output=io.BytesIO()
        Image.new('RGB',(20,20),'red').save(output,'PNG',save_all=True,append_images=[Image.new('RGB',(20,20),'blue')],duration=100)
        with self.assertRaisesRegex(ValueError,'still'):profile_images.normalize(output.getvalue(),'avatar',BackgroundCrop())

    def test_exif_orientation_is_applied_before_cropping(self):
        image=Image.new('RGB',(200,100),'red');image.paste('blue',(100,0,200,100))
        exif=Image.Exif();exif[274]=6
        output=io.BytesIO();image.save(output,'JPEG',exif=exif)
        encoded=profile_images.normalize(output.getvalue(),'avatar',BackgroundCrop(y=100))
        with Image.open(io.BytesIO(encoded)) as result:
            red,_,blue=result.convert('RGB').getpixel((200,200))
            self.assertGreater(blue,red);self.assertEqual(len(result.getexif()),0)

    def test_public_artwork_cache_keys_are_exact_and_origin_bound(self):
        for url,bucket in [('https://cdn2.steamgriddb.com/hero/abc123.png','steamgriddb'),
            ('https://images.igdb.com/igdb/image/upload/t_original/ar123.jpg','igdb')]:
            size,path=image_cache.parse_image_url(url)
            self.assertEqual(size,bucket);self.assertEqual(image_cache.upstream_image_url(size,path),url)
        for url in ['https://cdn2.steamgriddb.com.evil.test/hero/abc.png',
            'https://cdn2.steamgriddb.com/hero/../../etc/passwd','https://images.igdb.com/other.jpg']:
            self.assertEqual(image_cache.parse_image_url(url),(None,None))

    def test_backup_includes_uploaded_banners_but_not_provider_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);(folder/'7.banner.webp').write_bytes(b'banner')
            rows=[{'user_id':7,'avatar_path':None,'background_url':'/api/proxy/profile/background/7'}]
            self.assertEqual(backup.avatar_backup_files(folder,rows),{'7.banner.webp':b'banner'})


import test_games_pg as fixture
from sqlalchemy import select,text
from models import UserProfileData,GlobalSettings

@unittest.skipUnless(fixture.URL,'Requires explicit disposable PostgreSQL')
class ProfileImageDatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.GamesDatabaseTests.asyncSetUp
    cleanup=fixture.GamesDatabaseTests.cleanup

    async def test_banner_removal_holds_write_lock_until_file_is_deleted(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(profile_images.settings,'data_dir',Path(temporary)):
            await self.client.post('/profile/me/background',files={'file':('image.png',png(),'image/png')})
            run_in_thread=asyncio.to_thread
            async def delete_while_locked(operation,*args,**kwargs):
                async with self.Session() as competing:
                    self.assertFalse(await competing.scalar(text('SELECT pg_try_advisory_xact_lock(1935897199, 1)')))
                return await run_in_thread(operation,*args,**kwargs)
            with patch('routers.profile.asyncio.to_thread',side_effect=delete_while_locked) as delete:
                self.assertEqual((await self.client.delete('/profile/me/background')).status_code,200)
                delete.assert_awaited_once()
            async with self.Session() as competing:
                self.assertTrue(await competing.scalar(text('SELECT pg_try_advisory_xact_lock(1935897199, 1)')))

    async def test_upload_serve_and_remove_keep_avatar_and_banner_independent(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(profile_images.settings,'data_dir',Path(temporary)):
            for kind in ('avatar','background'):
                response=await self.client.post(f'/profile/me/{kind}',files={'file':('image.png',png(),'image/png')},
                    data={'crop':'{"x":25,"y":50,"zoom":1.5}'})
                self.assertEqual(response.status_code,200,response.text)
                with Image.open(Path(temporary)/'avatars'/profile_images.filename(self.user.id,kind)) as image:
                    self.assertEqual(image.format,'WEBP');self.assertEqual(image.size,profile_images.DIMENSIONS[kind])
            self.assertEqual((await self.client.get(f'/profile/background/{self.user.id}')).status_code,200)
            self.viewer=None
            self.assertEqual((await self.client.get(f'/profile/background/{self.user.id}')).status_code,200)
            self.viewer=self.user
            self.assertEqual((await self.client.delete('/profile/me/avatar')).status_code,200)
            self.assertTrue((Path(temporary)/'avatars'/profile_images.filename(self.user.id,'background')).exists())
            self.assertEqual((await self.client.get(f'/profile/background/{self.user.id}')).status_code,200)
            await self.client.delete('/profile/me/background')
            self.assertEqual((await self.client.get(f'/profile/background/{self.user.id}')).status_code,404)
            self.assertFalse((Path(temporary)/'avatars'/profile_images.filename(self.user.id,'background')).exists())

    async def test_invalid_oversized_and_unauthenticated_uploads_are_rejected(self):
        self.assertEqual((await self.client.post('/profile/me/avatar',files={'file':('a.png',b'bad','image/png')})).status_code,400)
        self.assertEqual((await self.client.post('/profile/me/avatar',files={'file':('a.png',b'x'*(3*1024*1024+1),'image/png')})).status_code,413)
        self.assertEqual((await self.client.post('/profile/me/background',files={'file':('a.png',png(),'image/png')},data={'crop':'{"zoom":8}'})).status_code,422)
        self.viewer=None
        self.assertEqual((await self.client.post('/profile/me/background',files={'file':('a.png',png(),'image/png')})).status_code,401)

    async def test_gallery_choice_obeys_cache_setting_and_profile_privacy(self):
        url='https://cdn2.steamgriddb.com/hero/abc123.png'
        with patch('routers.profile.cache_profile_background',AsyncMock()) as warm:
            response=await self.client.put('/profile/me/background',json={'image':url,'crop':{'x':20,'y':75,'zoom':1.2}})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['background_url'],url);warm.assert_not_awaited()
            with patch('core.image_cache.download_and_cache_image',AsyncMock()) as download:
                direct=await self.client.get(f'/profile/background/{self.user.id}')
                self.assertEqual(direct.status_code,307);self.assertEqual(direct.headers['location'],url)
                download.assert_not_awaited()
            async with self.Session() as db:
                config=await db.get(GlobalSettings,1);config.image_cache_enabled=True;await db.commit()
            response=await self.client.put('/profile/me/background',json={'image':url})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['background_url'],f'/api/proxy/profile/background/{self.user.id}')
            warm.assert_awaited_once()
        with tempfile.TemporaryDirectory() as temporary:
            cached=Path(temporary)/'abc.png';cached.write_bytes(png())
            with (patch('core.image_cache.download_and_cache_image',AsyncMock(return_value=str(cached))) as download,
                patch('core.image_cache.prune_cache_bg',AsyncMock())):
                result=await self.client.get(f'/profile/background/{self.user.id}')
                self.assertEqual(result.status_code,200);download.assert_awaited_once()
                async with self.Session() as db:
                    from models.base import PrivacyLevel
                    profile=await db.scalar(select(UserProfileData).where(UserProfileData.user_id==self.user.id))
                    profile.privacy_level=PrivacyLevel.private;await db.commit()
                self.viewer=None
                self.assertEqual((await self.client.get(f'/profile/background/{self.user.id}')).status_code,403)

    async def test_arbitrary_background_urls_cannot_be_saved(self):
        self.assertEqual((await self.client.patch('/profile/me',json={'background_url':'https://example.test/a.jpg'})).status_code,422)
        for url in ('http://127.0.0.1/a.png','https://cdn2.steamgriddb.com.evil.test/hero/abc.png'):
            self.assertEqual((await self.client.put('/profile/me/background',json={'image':url})).status_code,422)
