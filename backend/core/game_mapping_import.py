"""Stream official public reference feeds; only completed generations publish."""
import csv
import gzip
import io
import json
import hashlib
import re
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

from core.game_mapping_index import begin, discard, publish, write
from core.game_imdb_enrichment import now
from core.wikidata_claims import best_claims, qualified, snak_value


@contextmanager
def imdb_feed(name, path=None):
    if path:
        with gzip.open(path, 'rt', encoding='utf-8', newline='') as handle:
            yield csv.DictReader(handle, delimiter='\t')
    else:
        request = Request('https://datasets.imdbws.com/' + name + '.tsv.gz',
                          headers={'User-Agent': 'AnyList game reference importer/1'})
        with urlopen(request, timeout=60) as response, gzip.GzipFile(fileobj=response) as compressed:
            with io.TextIOWrapper(compressed, encoding='utf-8', newline='') as handle:
                yield csv.DictReader(handle, delimiter='\t')


def imdb_subset(target, *, basics=None, akas=None):
    """Runs in an importer process, never on the ASGI event loop."""
    csv.field_size_limit(16 * 1024 * 1024)
    games = {}
    with imdb_feed('title.basics', basics) as rows:
        if not {'tconst', 'titleType', 'primaryTitle', 'originalTitle', 'startYear'} <= set(rows.fieldnames or []):
            raise ValueError('Unexpected IMDb basics schema')
        for row in rows:
            if row['titleType'] != 'videoGame':
                continue
            native = row['tconst']
            if not re.fullmatch(r'tt[0-9]+', native) or native in games or not row['primaryTitle'].strip():
                raise ValueError('Invalid or duplicate IMDb game')
            year = int(row['startYear']) if row['startYear'].isdigit() else None
            original = row['originalTitle']
            aliases = [original] if original not in ('\\N', row['primaryTitle']) else []
            games[native] = {'id': native, 'title': row['primaryTitle'], 'year': year, 'aliases': aliases, 'akas': []}
    if not games:
        raise ValueError('Empty IMDb game feed')
    with imdb_feed('title.akas', akas) as rows:
        if not {'titleId', 'title', 'types', 'attributes', 'isOriginalTitle'} <= set(rows.fieldnames or []):
            raise ValueError('Unexpected IMDb AKA schema')
        for row in rows:
            game = games.get(row['titleId'])
            if game is None:
                continue
            game['akas'].append({key: row.get(key) for key in ('title', 'region', 'language', 'types', 'attributes', 'isOriginalTitle')})
            types = set(row['types'].split('\x02'))
            # Episode/working-title labels are preserved but cannot silently
            # turn a grouped work or an abandoned name into an exact identity.
            trusted = row['isOriginalTitle'] == '1' or bool(types & {'original', 'imdbDisplay', 'alternative'})
            if trusted and 'working' not in types and 'episode' not in row['attributes'].casefold():
                game['aliases'].append(row['title'])
    with Path(target).open('w', encoding='utf-8') as output:
        for game in games.values():
            game['aliases'] = list(dict.fromkeys(name for name in game['aliases'] if name and name != game['title']))
            output.write(json.dumps(game, ensure_ascii=True) + '\n')
        output.write(json.dumps({'_complete': True, 'source': 'imdb', 'rows': len(games), 'observed_at': now().isoformat()}) + '\n')
    return len(games)


def wiki_record(item):
    imdb = [snak_value(row.get('mainsnak')) for row in best_claims(item, 'P345')]
    imdb = [value for value in imdb if isinstance(value, str) and re.fullmatch(r'tt[0-9]+', value)]
    slugs = best_claims(item, 'P5794')
    numeric = [snak_value(row.get('mainsnak')) for row in best_claims(item, 'P9043')]
    numeric += [value for row in slugs for value in qualified(row, 'P9043')]
    ids = sorted({int(value) for value in numeric if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value) and int(value) <= 2147483647})
    if not imdb or not (ids or slugs):
        return None
    title = ((item.get('labels') or {}).get('en') or {}).get('value') or item['id']
    return {'id': item['id'], 'title': title, 'imdb_ids': sorted(set(imdb)), 'igdb_ids': ids, 'data': item}


async def import_jsonl(factory, source, path, *, observed_at=None):
    if observed_at is not None and observed_at.tzinfo:
        observed_at=observed_at.astimezone(timezone.utc).replace(tzinfo=None)
    generation = await begin(factory, source, observed_at=observed_at)
    count, batch, seen, completed, feed_stamp = 0, [], set(), False, observed_at
    try:
        with Path(path).open(encoding='utf-8') as handle:
            for line in handle:
                line = line.strip().rstrip(',')
                if line==']' and source=='wikidata':completed=True
                if not line or line in ('[', ']'):
                    continue
                data = json.loads(line)
                if data.get('_complete') is True:
                    if data.get('source')!=source or data.get('rows')!=count:raise ValueError('Incomplete reference export')
                    if data.get('observed_at'):
                        stamp=datetime.fromisoformat(data['observed_at'])
                        feed_stamp=stamp.astimezone(timezone.utc).replace(tzinfo=None) if stamp.tzinfo else stamp
                    completed=True;continue
                if completed:raise ValueError('Unexpected data after completion marker')
                if source == 'imdb':
                    if not re.fullmatch(r'tt[0-9]+', data.get('id', '')) or not isinstance(data.get('title'), str):
                        raise ValueError('Invalid IMDb subset')
                    record = {**data, 'imdb_ids': [data['id']], 'data': data}
                elif source == 'wikidata':
                    record = wiki_record(data)
                    if record is None:
                        continue
                else:
                    raise ValueError('Unsupported reference feed')
                if record['id'] in seen:raise ValueError('Duplicate reference identity')
                seen.add(record['id'])
                batch.append(record)
                count += 1
                if len(batch) == 500:
                    await write(factory, generation, batch)
                    batch = []
            await write(factory, generation, batch)
        if not completed:raise ValueError('Reference stream has no completion marker')
        if feed_stamp is None:raise ValueError('The source observation time is required')
        if feed_stamp>now()+timedelta(minutes=5):raise ValueError('Reference observation time is in the future')
        from models.game_mapping import GameMappingGeneration
        async with factory() as db:
            row=await db.get(GameMappingGeneration,generation)
            row.observed_at=feed_stamp;await db.commit()
        await publish(factory, generation, {'complete': True, 'rows': count, 'aliases_complete': source == 'imdb'})
        return {'generation': generation, 'rows': count}
    except BaseException:
        await discard(factory, generation)
        raise


async def refresh_wikidata(factory):
    """Refresh the relevant local game graph, retaining the last good generation."""
    import tempfile
    from sqlalchemy import select
    from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot
    from core.game_browse_sources import GameBrowseSources
    from core.game_credit_sources import GameCreditSources
    sources = GameBrowseSources(factory)
    wiki = GameCreditSources(sources)
    async with factory() as db:
        qids = set(await db.scalars(select(CatalogueIdentity.external_id).join(CatalogueEntity,
            CatalogueEntity.id == CatalogueIdentity.entity_id).where(CatalogueIdentity.namespace == 'wikidata.item',
                CatalogueEntity.kind == 'game')))
        # Newly discovered person titles can have useful source items before a
        # canonical game has been ingested. Include already retrieved evidence.
        cached = await db.scalars(select(MetadataSnapshot.payload['entities']).where(
            MetadataSnapshot.provider == 'wikidata', MetadataSnapshot.kind == 'game_credit_items',
            MetadataSnapshot.expires_at.is_not(None)))
        for values in cached:
            if isinstance(values, dict):
                qids.update(key for key, item in values.items() if isinstance(item, dict) and wiki_record(item))
    if not qids:
        return {'rows': 0, 'skipped': True}
    with tempfile.TemporaryDirectory(prefix='anylist-mapping-') as folder:
        target = Path(folder) / 'wikidata.jsonl'
        with target.open('w', encoding='utf-8') as handle:
            ordered = sorted(qids)
            count=0;stamps=[]
            for offset in range(0, len(ordered), 50):
                result = await wiki.entities(ordered[offset:offset + 50])
                if result['stale']:
                    raise ValueError('Stale Wikidata refresh')
                if set(result['data']['entities'])!=set(ordered[offset:offset+50]):
                    raise ValueError('Incomplete Wikidata refresh')
                stamps.append(result.get('fetched_at') or now().isoformat())
                for item in result['data']['entities'].values():
                    if wiki_record(item):count+=1
                    handle.write(json.dumps(item) + '\n')
            handle.write(json.dumps({'_complete':True,'source':'wikidata','rows':count,'observed_at':min(stamps)})+'\n')
        return await import_jsonl(factory, 'wikidata', target)


async def refresh_cached_wikidata(factory):
    """Periodically index bounded, already retrieved game claims; no HTTP I/O."""
    from sqlalchemy import select, func, column, literal, true
    from sqlalchemy.dialects.postgresql import JSONB
    from models.catalogue import MetadataSnapshot
    from models.game_mapping import GameMappingGeneration
    async with factory() as db:
        headers=list((await db.execute(select(MetadataSnapshot.id,MetadataSnapshot.fetched_at).where(
            MetadataSnapshot.provider=='wikidata',MetadataSnapshot.kind=='game_credit_items',
            MetadataSnapshot.expires_at>now(),MetadataSnapshot.fetched_at.is_not(None)))).all())
        if not headers:return {'rows':0,'skipped':True}
        signature=hashlib.sha256(json.dumps(sorted((row.id,row.fetched_at.isoformat()) for row in headers)).encode()).hexdigest()
        current=await db.scalar(select(GameMappingGeneration.coverage).where(GameMappingGeneration.source=='wikidata',
            GameMappingGeneration.active.is_(True)))
        if current and current.get('cache_signature')==signature:return {'rows':current['rows'],'skipped':True}
        entities=func.jsonb_each(MetadataSnapshot.payload['entities']).table_valued(column('key'),column('value',JSONB)).lateral()
        value=entities.c.value
        claims=func.jsonb_build_object(*[part for prop in ('P345','P5794','P9043','P1733')
            for part in (prop,func.coalesce(value['claims'][prop],literal([],type_=JSONB)))])
        compact=func.jsonb_build_object('id',value['id'],'lastrevid',value['lastrevid'],'labels',value['labels'],'claims',claims)
        payloads=list(await db.scalars(select(compact).select_from(MetadataSnapshot).join(entities, true()).where(
            MetadataSnapshot.id.in_([row.id for row in headers]))))
    records={}
    for item in payloads:
        record=wiki_record(item)
        if record is None:continue
        previous=records.get(record['id'])
        if previous is None or (item.get('lastrevid') or 0)>(previous['data'].get('lastrevid') or 0):records[record['id']]=record
    if not records:return {'rows':0,'skipped':True}
    generation=await begin(factory,'wikidata',observed_at=min(row.fetched_at for row in headers))
    try:
        values=list(records.values())
        for offset in range(0,len(values),500):await write(factory,generation,values[offset:offset+500])
        await publish(factory,generation,{'complete':True,'scope':'retrieved_game_evidence','rows':len(values),'cache_signature':signature})
        return {'rows':len(values),'generation':generation}
    except BaseException:
        await discard(factory,generation);raise
