"""Explicit, local-only synthetic UI fixtures; no connected accounts or credentials."""
import asyncio
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
os.environ.setdefault('SECRET_KEY','local-development-only-not-for-deployment')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://media_tracker:local-development-only@127.0.0.1:55438/media_tracker')
from sqlalchemy import select
from db import AsyncSessionLocal, engine
from models import User, UserProfileData, Media, GlobalSettings, UserSettings
from models.base import PrivacyLevel, MediaType
from models.tracking import TrackedEntry

async def main():
    if ':55438/media_tracker' not in os.environ['DATABASE_URL']:
        raise SystemExit('This fixture only runs against the isolated local database.')
    async with AsyncSessionLocal() as db:
        user=(await db.execute(select(User).where(User.username=='preview'))).scalar_one_or_none()
        if user: return
        user=User(username='preview',email='preview@example.test',api_key=__import__('secrets').token_hex(32))
        db.add(user); await db.flush()
        db.add(UserProfileData(user_id=user.id,display_name='Alex',bio='Films that stay with you. Series worth coming back to.',privacy_level=PrivacyLevel.public))
        db.add(UserSettings(user_id=user.id))
        gs=await db.get(GlobalSettings,1)
        if not gs: gs=GlobalSettings(id=1); db.add(gs)
        gs.enable_logged_out_navigation=True
        fixtures=[('Severance','watching',8.5,12,'2022-02-18','/pPHpeI2X1qEd1CS1SeyrdhZ4qnT.jpg'),('The Bear','watching',8,18,'2022-06-23',None),('Dark','completed',9.5,26,'2017-12-01',None),('Better Call Saul','completed',9,63,'2015-02-08',None),('The Last of Us','paused',7.5,4,'2023-01-15',None),('Silo','planning',None,0,'2023-05-05',None)]
        for name,status,score,progress,released,poster in fixtures:
            media=Media(title=name,media_type=MediaType.series,poster_path=poster,release_date=released,overview='Synthetic local preview entry. Metadata and scores are demonstration fixtures.',tmdb_data={'genres':[{'name':'Drama'}],'seasons':[{'name':'Season 1','season_number':1,'episode_count':10,'air_date':released}]})
            db.add(media);await db.flush()
            db.add(TrackedEntry(user_id=user.id,media_id=media.id,status=status,manual_score=score,progress=progress,favorite=status=='completed'))
        await db.commit()
    await engine.dispose()

asyncio.run(main())
