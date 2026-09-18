"""Write a short-lived login state for the synthetic local preview account."""
import asyncio, json, os, sys, time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend'))
os.environ.setdefault('SECRET_KEY','local-development-only-not-for-deployment')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://media_tracker:local-development-only@127.0.0.1:55438/media_tracker')
from db import AsyncSessionLocal, engine
from models import User
from sqlalchemy import select
from core.security import create_access_token

async def main():
    if ':55438/media_tracker' not in os.environ['DATABASE_URL']:
        raise SystemExit('Local preview database only')
    async with AsyncSessionLocal() as db:
        user=(await db.execute(select(User).where(User.username=='preview'))).scalar_one()
        token=create_access_token(str(user.id))
        state={'cookies':[{'name':'token','value':token,'domain':'127.0.0.1','path':'/','expires':int(time.time())+1800,'httpOnly':True,'secure':False,'sameSite':'Lax'}],'origins':[]}
        (root/'.venv/preview-browser.json').write_text(json.dumps(state))
    await engine.dispose()
asyncio.run(main())
