"""One idle-time feeder for the canonical Games mapping pipeline.

Games establish bounded contributor demand. Contributor careers do not expand
the graph recursively. All evidence, bindings and cursors remain canonical.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Float, String, cast, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from core.config import settings
from core.game_work import Priority, now, work_context, wake, _lock_key
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot
from models.game_runner import GameMapperEdge, GameMapperInterest, GameMapperItem, GameMapperState
from models.games import GameChildState, GameEntry, GameFavorite

log = logging.getLogger(__name__)
CLASSES = {1:'Lists', 2:'Viewed games', 3:'Browse highlights'}
_wake=None


def wake_runner():
    if _wake is not None:_wake.set()


async def feed_lock(db):
    # Interest changes and their cached graph weights publish atomically.
    # This transaction never owns provider work.
    if not db.info.get('game_feed_lock'):
        await db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':_lock_key('game-mapper-feed')})
        transaction=db.get_transaction()
        db.info['game_feed_lock']=transaction
    elif db.info['game_feed_lock'] is not db.get_transaction():
        db.info.pop('game_feed_lock',None)
        await feed_lock(db)


async def state(db):
    await db.execute(insert(GameMapperState).values(id=1,enabled=True,payload={}).on_conflict_do_nothing())
    return await db.get(GameMapperState,1,populate_existing=True)


async def add_items(db, kind, values):
    """Public identity is the queue identity; repeated observations are idempotent."""
    if not values:return
    await feed_lock(db)
    rows=[{'key':f'{kind}:{native}','kind':kind,'native':str(native),'entity_id':entity,
        'category':4,'weight':1,'counts':{'4':1},'next_at':now(),'processed':0} for native,entity in sorted(set(values))]
    statement=insert(GameMapperItem).values(rows)
    await db.execute(statement.on_conflict_do_update(index_elements=['key'],
        set_={'entity_id':func.coalesce(GameMapperItem.entity_id,statement.excluded.entity_id)}))


async def observe(db, games, *, category, source, user_id=None, ttl=timedelta(days=30)):
    games=list({str(native):entity for native,entity in games if str(native).isdigit()}.items())
    if not games:return
    fresh=(await db.execute(select(GameMapperInterest.game).where(GameMapperInterest.source==source,
        GameMapperInterest.game.in_([native for native,_ in games]),GameMapperInterest.category==category,
        GameMapperInterest.expires_at>now()+ttl-min(timedelta(hours=1),ttl/2)))).scalars().all()
    if len(fresh)==len(games):return
    await feed_lock(db)
    await add_items(db,'game',games)
    statement=insert(GameMapperInterest).values([{'source':source,'game':native,'category':category,
        'user_id':user_id,'expires_at':now()+ttl} for native,_ in games])
    await db.execute(statement.on_conflict_do_update(index_elements=['source','game'],
        set_={'expires_at':statement.excluded.expires_at,'category':category}))
    await refresh_weights(db,[native for native,_ in games])
    wake()
    wake_runner()


async def refresh_weights(db, natives, *, extra_targets=()):
    if not natives:return
    await feed_lock(db)
    stamp=now()
    rows=(await db.execute(select(GameMapperInterest.game,GameMapperInterest.category,
        func.count(func.distinct(func.coalesce(cast(GameMapperInterest.user_id,String),GameMapperInterest.source))))
        .where(GameMapperInterest.game.in_(natives),GameMapperInterest.expires_at>stamp)
        .group_by(GameMapperInterest.game,GameMapperInterest.category))).all()
    counts={native:{} for native in natives}
    for native,category,count in rows:counts[native][str(category)]=count
    await write_weights(db,{f'game:{native}':values for native,values in counts.items()})
    targets=sorted(set(extra_targets)|set(await db.scalars(select(GameMapperEdge.target).where(GameMapperEdge.game.in_(natives)).distinct())))
    if not targets:return
    game=aliased(GameMapperItem)
    rows=(await db.execute(select(GameMapperEdge.target,game.counts).join(game,game.key=='game:'+GameMapperEdge.game)
        .where(GameMapperEdge.target.in_(targets)))).all()
    combined={target:{} for target in targets}
    for target,values in rows:
        for category,count in values.items():combined[target][category]=combined[target].get(category,0)+count
    await write_weights(db,combined)


async def write_weights(db,counts):
    values=[]
    for key,weights in counts.items():
        category=min(map(int,weights),default=4)
        values.append({'key':key,'counts':weights,'category':category,'weight':weights.get(str(category),0)})
    if values:
        await db.execute(text('''UPDATE game_mapper_items AS item SET counts=value.counts,
            category=value.category,weight=value.weight FROM jsonb_to_recordset(CAST(:values AS jsonb))
            AS value(key text,counts jsonb,category integer,weight integer) WHERE item.key=value.key'''),
            {'values':json.dumps(values)})


async def observe_work(work_id, engine, user_id, category=2, *, removed=False):
    """Post-response hook; no provider calls and no request-owned transaction."""
    from core.game_source_cache import session_factory
    async with session_factory(engine)() as db:
        native=await db.scalar(select(CatalogueIdentity.external_id).join(CatalogueEntity,
            CatalogueEntity.id==CatalogueIdentity.entity_id).where(CatalogueIdentity.entity_id==work_id,
            CatalogueIdentity.namespace=='igdb.game',or_(CatalogueEntity.attributes['igdb_id'].astext.is_(None),
                CatalogueIdentity.external_id==CatalogueEntity.attributes['igdb_id'].astext)))
        if not native:return
        source=f'{"list" if category==1 else "view"}:{user_id}'
        if category==1:
            removed=not await db.scalar(select(or_(
                select(GameEntry.id).where(GameEntry.user_id==user_id,GameEntry.work_id==work_id).exists(),
                select(GameChildState.id).where(GameChildState.user_id==user_id,GameChildState.work_id==work_id,GameChildState.base_entry_id.is_not(None)).exists(),
                select(GameFavorite.user_id).where(GameFavorite.user_id==user_id,GameFavorite.work_id==work_id).exists())))
        if removed:
            await feed_lock(db)
            await db.execute(delete(GameMapperInterest).where(GameMapperInterest.source==source,GameMapperInterest.game==native))
            await refresh_weights(db,[native])
        else:
            await observe(db,[(native,work_id)],category=category,source=source,user_id=user_id)
        await db.commit()


async def observe_browse(engine, user_id, category, cards):
    from core.game_source_cache import session_factory
    async with session_factory(engine)() as db:
        source=f'browse:{user_id}:{category}'
        rows=(await db.execute(select(GameMapperInterest.game,GameMapperInterest.expires_at).where(GameMapperInterest.source==source))).all()
        desired={str(card['igdb_id']) for card in cards[:6]}
        if {row.game for row in rows}==desired and all(row.expires_at>now()+timedelta(hours=1) for row in rows):return
        await feed_lock(db)
        previous=list(await db.scalars(select(GameMapperInterest.game).where(GameMapperInterest.source==source)))
        await db.execute(delete(GameMapperInterest).where(GameMapperInterest.source==source))
        await observe(db,[(str(card['igdb_id']),card.get('id')) for card in cards[:6]],category=3,
            source=source,user_id=user_id,ttl=timedelta(hours=2))
        await refresh_weights(db,previous)
        await db.commit()


class GameBackground:
    def __init__(self,sessions):
        from core.game_browse_sources import GameBrowseSources
        from core.game_imdb_enrichment import GameIMDbEnrichment
        from core.game_imdb_person import GameIMDbPersonEnrichment
        from core.game_imdb_discovery import GamePersonDiscovery
        from core.game_company_works import GameCompanyWorks
        self.sessions=sessions
        self.sources=GameBrowseSources(sessions)
        self.credits=GameIMDbEnrichment(sessions,page_budget=1)
        self.people=GameIMDbPersonEnrichment(sessions,page_budget=1)
        self.discovery=GamePersonDiscovery(sessions)
        self.companies=GameCompanyWorks(sessions,page_budget=1)

    async def seed_lists(self,db,runner):
        payload=dict(runner.payload)
        seed_due=payload.get('lists_due','')<=now().isoformat()
        await feed_lock(db)
        from sqlalchemy import tuple_
        phase=payload.get('list_source',0)
        model=(GameEntry,GameChildState,GameFavorite)[phase]
        order=(model.user_id,model.work_id) if phase==2 else (model.id,)
        cursor=payload.get('list_cursor',0)
        after=tuple_(*order)>tuple(cursor) if phase==2 and isinstance(cursor,list) else model.user_id>0 if phase==2 else model.id>cursor
        query=select(order[-1],model.user_id,model.work_id,CatalogueIdentity.external_id).join(
            CatalogueIdentity,CatalogueIdentity.entity_id==model.work_id).join(CatalogueEntity,CatalogueEntity.id==model.work_id)
        if phase==1:query=query.where(GameChildState.base_entry_id.is_not(None))
        rows=(await db.execute(query
            .where(after,CatalogueIdentity.namespace=='igdb.game',
                or_(CatalogueEntity.attributes['igdb_id'].astext.is_(None),
                    CatalogueIdentity.external_id==CatalogueEntity.attributes['igdb_id'].astext))
            .order_by(*order).limit(250))).all() if seed_due else []
        # Validate removals from imports/account deletion too, without loading libraries.
        removed=[]
        if payload.get('cleanup_due','')<=now().isoformat():
            work=select(CatalogueIdentity.entity_id).where(CatalogueIdentity.namespace=='igdb.game',
                CatalogueIdentity.external_id==GameMapperInterest.game).correlate(GameMapperInterest).scalar_subquery()
            existing=or_(select(GameEntry.id).where(GameEntry.user_id==GameMapperInterest.user_id,GameEntry.work_id==work).exists(),
                select(GameChildState.id).where(GameChildState.user_id==GameMapperInterest.user_id,GameChildState.work_id==work,
                    GameChildState.base_entry_id.is_not(None)).exists(),
                select(GameFavorite.user_id).where(GameFavorite.user_id==GameMapperInterest.user_id,GameFavorite.work_id==work).exists())
            scan=select(GameMapperInterest.source,GameMapperInterest.game,existing.label('present')).where(GameMapperInterest.category==1)
            if payload.get('cleanup_cursor'):
                scan=scan.where(tuple_(GameMapperInterest.source,GameMapperInterest.game)>tuple(payload['cleanup_cursor']))
            batch=(await db.execute(scan.order_by(GameMapperInterest.source,GameMapperInterest.game).limit(250))).all()
            stale=[(row.source,row.game) for row in batch if not row.present]
            if stale:
                await db.execute(delete(GameMapperInterest).where(tuple_(GameMapperInterest.source,GameMapperInterest.game).in_(stale)))
                removed=[native for _,native in stale]
            payload['cleanup_cursor']=[batch[-1].source,batch[-1].game] if len(batch)==250 else None
            payload['cleanup_due']=(now()+timedelta(seconds=0 if len(batch)==250 else 600)).isoformat()
        await refresh_weights(db,removed)
        if rows:
            by_user={}
            for _,user,work,native in rows:by_user.setdefault(user,[]).append((native,work))
            for user,games in by_user.items():
                await observe(db,games,category=1,source=f'list:{user}',user_id=user,ttl=timedelta(days=1))
            payload['list_cursor']=[rows[-1][1],rows[-1][2]] if phase==2 else rows[-1][0]
        if seed_due and len(rows)<250:
            payload.update(list_cursor=0,list_source=(phase+1)%3,
                lists_due=(now()+timedelta(seconds=600 if phase==2 else 0)).isoformat())
        runner.payload=payload
        return bool(rows)

    async def seed_browse(self,runner,*,highlights=False):
        """Refresh one six-card highlight, or advance a durable catalogue cursor."""
        from core.game_browse import GameBrowse, CATEGORIES, filters
        payload=dict(runner.payload)
        category=list(CATEGORIES)[payload.get('browse_category',0)%len(CATEGORIES)]
        if highlights:
            browse=GameBrowse(self.sources)
            query=await filters(self.sources,category=category)
            page=await browse.page(query,limit=6)
            cards=page['rows']
            changes={'browse_category':payload.get('browse_category',0)+1,
                'highlights_due':(now()+timedelta(seconds=1 if payload.get('browse_category',0)<5 else 120)).isoformat()}
        else:
            offset=payload.get('catalogue_offset',0)
            if offset<=10000:
                data=(await self.sources.catalogue_feed('popular',offset=offset))['data']
                cards=data['rows']
                changes={'catalogue_offset':data['next_offset'] if data['has_more'] else 10001}
                advance=True
            else:
                # Ranked feeds have finite windows. Keyset traversal eventually
                # reaches the rest without deep offsets or expiring UI cursors.
                from core.game_browse_sources import CARD_FIELDS, card
                raw=await self.sources.igdb('games',f'fields {CARD_FIELDS}; where id>{int(payload.get("catalogue_id",0))}; sort id asc; limit 100;')
                cards=[value for row in raw if (value:=card(row)) and not value['is_erotic']]
                changes={'catalogue_id':max((row['id'] for row in raw),default=payload.get('catalogue_id',0))}
                advance=bool(raw)
            changes['browse_due']=(now()+timedelta(seconds=1 if advance else 3600)).isoformat()
        async with self.sessions() as db:
            await feed_lock(db)
            if highlights:
                old=list(await db.scalars(select(GameMapperInterest.game).where(GameMapperInterest.source==f'highlight:{category}')))
                await db.execute(delete(GameMapperInterest).where(GameMapperInterest.source==f'highlight:{category}'))
                await observe(db,[(str(card['igdb_id']),None) for card in cards],category=3,
                    source=f'highlight:{category}',ttl=timedelta(hours=2))
                await refresh_weights(db,old)
            else:
                await observe(db,[(str(card['igdb_id']),None) for card in cards],category=4,
                    source='catalogue',ttl=timedelta(days=3650))
            stored=await db.get(GameMapperState,1)
            stored.payload={**stored.payload,**changes}
            await db.commit()

    async def canonical_job(self,provider,kind,native,entity,service):
        from core.game_imdb_enrichment import request, snapshot
        async with self.sessions() as db:
            await request(db,native,kind=kind,provider=provider,entity_id=entity)
            row=(await db.execute(snapshot(kind,native,provider=provider).with_only_columns(
                MetadataSnapshot.next_attempt_at,MetadataSnapshot.lease_until,MetadataSnapshot.expires_at,
                MetadataSnapshot.error_code))).one()
            await db.commit()
        if row.lease_until and row.lease_until>now():return now()+timedelta(seconds=2)
        if row.next_attempt_at:
            if row.next_attempt_at>now():return row.next_attempt_at
            # The automatic runner owns one stage globally and deliberately
            # bypasses FIFO service semaphores. Canonical DB leases still fence it.
            await service._run(native)
            async with self.sessions() as db:
                row=(await db.execute(snapshot(kind,native,provider=provider).with_only_columns(
                    MetadataSnapshot.next_attempt_at,MetadataSnapshot.expires_at,MetadataSnapshot.error_code))).one()
            if row.error_code:
                from core.catalogue_providers import ProviderError
                raise ProviderError(row.error_code,max(1,((row.next_attempt_at or row.expires_at or now()+timedelta(hours=1))-now()).total_seconds()))
            if row.next_attempt_at:return max(now(),row.next_attempt_at)
        return row.expires_at or now()+timedelta(days=1)

    async def game(self,item):
        from core import catalogue
        from core.catalogue_normalize import normalize_igdb
        from core.game_company_works import prepare_primary_game
        from core.game_company_source import FIELDS
        from core.game_types import capabilities, mobile_only
        from core.game_imdb_enrichment import JOB, snapshot
        async def load():
            return {'rows':await self.sources.igdb('games',f'fields {FIELDS},involved_companies.publisher,involved_companies.company.name,involved_companies.company.logo.url; where id={int(item.native)}; limit 1;')}
        cached=await self.sources.cache.get('igdb','game_mapper_primary',item.native,load,ttl=timedelta(days=7),max_stale=timedelta(days=30))
        rows=cached['data']['rows']
        if not rows:return now()+timedelta(days=7)
        doc=normalize_igdb(rows[0])
        if mobile_only(rows[0]) or rows[0].get('version_parent') or not capabilities(doc['work']['attributes']).searchable:
            async with self.sessions() as db:
                await feed_lock(db)
                old=list(await db.scalars(select(GameMapperEdge.target).where(GameMapperEdge.game==item.native)))
                await db.execute(delete(GameMapperEdge).where(GameMapperEdge.game==item.native))
                await refresh_weights(db,[item.native],extra_targets=old)
                await db.commit()
            return now()+timedelta(days=30)
        if item.entity_id is None or item.checked_at is None or cached['fetched_at']>item.checked_at.isoformat():
            async with self.sessions() as db:
                await catalogue.write_lock(db)
                doc['work']=await prepare_primary_game(db,doc['work'])
                work=await catalogue.ingest_document(db,doc,'igdb')
                await db.commit()
            # The canonical root publishes before its scheduling hint. Holding
            # its row lock while updating this FK can invert an observer's locks.
            async with self.sessions() as db:
                await feed_lock(db)
                stored=await db.get(GameMapperItem,item.key);stored.entity_id=work.id
                item.entity_id=work.id
                await db.commit()
        deadline=await self.canonical_job('imdb',JOB,item.entity_id,item.entity_id,self.credits)
        primary_due=now()+timedelta(minutes=1) if cached.get('stale') else datetime.fromisoformat(cached['fetched_at'])+timedelta(days=7)
        deadline=min(deadline,primary_due)
        async with self.sessions() as db:
            await feed_lock(db)
            job=(await db.execute(snapshot(JOB,item.entity_id).with_only_columns(
                MetadataSnapshot.next_attempt_at,MetadataSnapshot.lease_until))).one()
            if job.next_attempt_at or job.lease_until and job.lease_until>now():return deadline
            from core.game_credit_projection import displayed_credits
            from core.game_principal_credits import select_credits, ACTING
            credits=await displayed_credits(db,item.entity_id)
            rows=[{'id':person.canonical_person_id or person.canonical_company_id or person.id,
                'kind':person.kind,'role':credit.role,'role_label':credit.role_label,'position':credit.position,
                'attributes':credit.attributes,'character_label':credit.character_label} for credit,person in credits]
            chosen=select_credits(rows)
            cast_ids=list(dict.fromkeys(row['id'] for row in chosen if row['kind']=='person' and row['role'] in ACTING))[:8]
            crew=list(dict.fromkeys(row['id'] for row in chosen if row['kind']=='person' and row['role'] not in ACTING))[:8]
            if len(crew)<8:
                from core.game_credit_roles import ACTING as ALL_ACTING
                crew=list(dict.fromkeys([*crew,*(row['id'] for row in rows if row['kind']=='person'
                    and row['role'] not in ALL_ACTING and not row['attributes'].get('uncredited')
                    and not (row['attributes'].get('game_role') or {}).get('uncredited'))]))[:8]
            company_rows=[row for row in chosen if row['kind']=='organization']
            leads=[next((row['id'] for row in company_rows if row['role']==role),None) for role in ('developer','publisher')]
            companies=list(dict.fromkeys([*(value for value in leads if value),*(row['id'] for row in company_rows)]))[:2]
            people=set(cast_ids+crew)
            identities=(await db.execute(select(CatalogueIdentity.entity_id,CatalogueIdentity.namespace,CatalogueIdentity.external_id)
                .where(CatalogueIdentity.entity_id.in_(people|set(companies)),CatalogueIdentity.namespace.in_(('imdb.person','igdb.company'))))).all()
            targets=[]
            for entity,namespace,native in identities:
                kind='person' if namespace=='imdb.person' else 'studio'
                if entity not in (people if kind=='person' else companies):continue
                await add_items(db,kind,[(native,entity)])
                targets.append(f'{kind}:{native}')
            previous=list(await db.scalars(select(GameMapperEdge.target).where(GameMapperEdge.game==item.native)))
            await db.execute(delete(GameMapperEdge).where(GameMapperEdge.game==item.native))
            if targets:
                await db.execute(insert(GameMapperEdge).values([{'game':item.native,'target':target} for target in sorted(set(targets))]).on_conflict_do_nothing())
            await refresh_weights(db,[item.native],extra_targets=previous)
            await db.commit()
        return deadline

    async def contributor(self,item):
        if item.kind=='studio':
            from core.game_company_works import JOB
            return await self.canonical_job('igdb',JOB,item.native,item.entity_id,self.companies)
        from core.game_imdb_person import JOB, COMPONENT
        deadline=await self.canonical_job('imdb',JOB,item.native,item.entity_id,self.people)
        from core.game_imdb_discovery import JOB as DISCOVERY
        from core.game_imdb_enrichment import snapshot
        async with self.sessions() as db:
            component=(await db.execute(snapshot(COMPONENT,item.native).with_only_columns(
                MetadataSnapshot.payload['generation'].astext,MetadataSnapshot.coverage['unmapped_games'].as_integer()))).one_or_none()
            previous=await db.scalar(snapshot(DISCOVERY,item.native).with_only_columns(MetadataSnapshot.id))
            if component and component[0] and (component[1] or previous):
                from core.game_imdb_discovery import request
                await request(db,item.native,component[0],item.entity_id)
                await db.commit()
            job=(await db.execute(snapshot(DISCOVERY,item.native).with_only_columns(
                MetadataSnapshot.next_attempt_at,MetadataSnapshot.expires_at))).one_or_none()
        if job and job.next_attempt_at:
            if job.next_attempt_at<=now():
                await self.discovery._run(item.native)
            async with self.sessions() as db:
                job=(await db.execute(snapshot(DISCOVERY,item.native).with_only_columns(
                    MetadataSnapshot.next_attempt_at,MetadataSnapshot.expires_at,MetadataSnapshot.error_code))).one()
            if job.error_code:
                from core.catalogue_providers import ProviderError
                raise ProviderError(job.error_code,max(1,((job.next_attempt_at or now()+timedelta(hours=1))-now()).total_seconds()))
            if job.next_attempt_at:return max(now(),job.next_attempt_at)
        return min(deadline,job.expires_at) if job and job.expires_at else deadline

    async def tick(self):
        if not settings.game_background_mapper_enabled:return False
        # One automatic stage across processes. The connection owns no catalogue
        # lock and is released on crash; ordinary user workers run independently.
        async with self.sessions() as guard:
            if not await guard.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':_lock_key('game-background-runner')}):return False
            async with self.sessions() as db:
                runner=await state(db)
                if not runner.enabled:return False
                runner.heartbeat=now()
                runner.lease_token=uuid.uuid4().hex;runner.lease_until=now()+timedelta(minutes=2)
                seeded=await self.seed_lists(db,runner)
                await db.commit()
                # Cheap bounded housekeeping follows the same relevance graph.
                expired=list(await db.scalars(select(GameMapperInterest.game).where(GameMapperInterest.expires_at<=now()).limit(250)))
                if expired:
                    await feed_lock(db)
                    await db.execute(delete(GameMapperInterest).where(GameMapperInterest.game.in_(expired),GameMapperInterest.expires_at<=now()))
                    await refresh_weights(db,expired)
                item=await db.scalar(select(GameMapperItem).where(GameMapperItem.next_at<=now(),GameMapperItem.weight>0)
                    .order_by(GameMapperItem.category,GameMapperItem.checked_at.is_not(None),
                        cast(GameMapperItem.quanta,Float)/GameMapperItem.weight,
                        GameMapperItem.weight.desc(),
                        GameMapperItem.next_at,GameMapperItem.key).limit(1))
                if item:
                    runner.payload={**runner.payload,'current':item.key}
                deep_count=await db.scalar(select(func.count()).select_from(GameMapperItem).where(
                    GameMapperItem.kind=='game',GameMapperItem.category==4,GameMapperItem.next_at<=now(),GameMapperItem.weight>0))
                await db.commit()
            try:
                with work_context(Priority.AUTOMATIC,'automatic',replace=True):
                    if (not item or item.category>=3) and runner.payload.get('highlights_due','')<=now().isoformat():
                        await self.seed_browse(runner,highlights=True)
                    elif (not item or item.category==4) and deep_count==0 and runner.payload.get('browse_due','')<=now().isoformat():
                        await self.seed_browse(runner)
                    elif item:
                        deadline=await (self.game(item) if item.kind=='game' else self.contributor(item))
                        async with self.sessions() as db:
                            stored=await db.get(GameMapperItem,item.key)
                            stored.next_at=max(now()+timedelta(milliseconds=25),deadline)
                            stored.checked_at=now();stored.error=None
                            stored.quanta+=1
                            if deadline>now()+timedelta(minutes=1):stored.processed+=1
                            await db.commit()
                    else:return seeded
                return True
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():raise
                # Provider admission yielded; the canonical cursor/lease was
                # released by its owner. No attempts/negative evidence change.
                if item:
                    async with self.sessions() as db:
                        stored=await db.get(GameMapperItem,item.key);stored.next_at=now()+timedelta(seconds=1)
                        await db.commit()
                return False
            except Exception as error:
                async with self.sessions() as db:
                    if item:
                        stored=await db.get(GameMapperItem,item.key)
                        stored.error=getattr(error,'code','retry_pending')
                        stored.next_at=now()+timedelta(seconds=max(60,getattr(error,'retry_after',0) or 0))
                    else:
                        stored=await db.get(GameMapperState,1)
                        stored.payload={**stored.payload,'browse_due':(now()+timedelta(minutes=1)).isoformat()}
                    await db.commit()
                log.warning('Automatic game mapping will retry (%s)',type(error).__name__)
                return False
            finally:
                async with self.sessions() as db:
                    runner=await db.get(GameMapperState,1)
                    runner.lease_token=runner.lease_until=None
                    runner.payload={**runner.payload,'current':None}
                    await db.commit()

    async def close(self):
        await self.discovery.source.close()


async def summary(db):
    runner=await state(db)
    rows=(await db.execute(select(GameMapperItem.kind,GameMapperItem.category,
        func.count().label('total'),func.count().filter(GameMapperItem.weight>0,
            or_(GameMapperItem.next_at<=now(),GameMapperItem.error.is_not(None),GameMapperItem.processed==0)).label('queued'),
        func.sum(GameMapperItem.processed).label('processed'),func.count().filter(GameMapperItem.processed>0).label('checked'),
        func.count().filter(GameMapperItem.error.is_not(None)).label('retries'))
        .group_by(GameMapperItem.kind,GameMapperItem.category))).all()
    games={str(category):sum(row.queued for row in rows if row.kind=='game' and row.category==category) for category in CLASSES}
    active=bool(runner.lease_until and runner.lease_until>now())
    return {'enabled':runner.enabled,'available':settings.game_background_mapper_enabled,
        'state':'disabled' if not settings.game_background_mapper_enabled else 'stopped' if not runner.enabled else 'running' if active else 'idle',
        'heartbeat':runner.heartbeat.isoformat()+'Z' if runner.heartbeat else None,
        'current':runner.payload.get('current') if active else None,
        'queued_games':sum(games.values()),'categories':games,
        'people':sum(row.total for row in rows if row.kind=='person'),
        'studios':sum(row.total for row in rows if row.kind=='studio'),
        'processed':sum(row.processed or 0 for row in rows),
        'processed_games':sum(row.checked for row in rows if row.kind=='game'),
        'retries':sum(row.retries for row in rows)}


async def scheduler():
    global _wake
    from db import AsyncSessionLocal
    service=GameBackground(AsyncSessionLocal)
    _wake=asyncio.Event()
    try:
        while True:
            _wake.clear()
            try:worked=await service.tick()
            except asyncio.CancelledError:raise
            except Exception:
                log.exception('Automatic game runner will retry')
                worked=False
            if worked:await asyncio.sleep(0)
            else:
                try:await asyncio.wait_for(_wake.wait(),2)
                except TimeoutError:pass
    finally:
        _wake=None
        await service.close()
