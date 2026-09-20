"""Provision passwords for existing local preview users, without touching providers."""
import asyncio
import os
from pathlib import Path
import secrets
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ.setdefault('SECRET_KEY','local-development-only-not-for-deployment')
LOCAL_DB='postgresql+asyncpg://media_tracker:local-development-only@127.0.0.1:55438/media_tracker'
os.environ.setdefault('DATABASE_URL',LOCAL_DB)
from sqlalchemy import select
from db import AsyncSessionLocal,engine
from models import User
from core.security import get_password_hash,verify_password

async def main():
    if os.environ['DATABASE_URL']!=LOCAL_DB:raise SystemExit('Local preview database only.')
    target=ROOT/'.venv'/'LOCAL-LOGIN.txt'
    existing=target.read_text().splitlines() if target.exists() else []
    previous=dict(line.split(': ',1) for line in existing if ': ' in line)
    lines=['AnyList local preview - http://localhost:7340/login',
        'Use provider-test for your private imported lists; preview contains synthetic examples.',
        'These passwords are only for this local app, not your streaming accounts.','']
    async with AsyncSessionLocal() as db:
        for username in ('provider-test','preview'):
            user=(await db.execute(select(User).where(User.username==username))).scalar_one_or_none()
            if not user:continue
            password=previous.get(username)
            if not user.password_hash:
                password=secrets.token_urlsafe(18)
                user.password_hash=get_password_hash(password)
            elif not password or not verify_password(password,user.password_hash):
                # Never undo a password the tester deliberately changed.
                lines.append(f'{username}: Use the password you set in Settings.')
                continue
            user.email_confirmed=True
            lines.append(f'{username}: {password}')
        await db.commit()
    target.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    await engine.dispose()
    print('Local login details saved in .venv/LOCAL-LOGIN.txt')

if __name__=='__main__':asyncio.run(main())
