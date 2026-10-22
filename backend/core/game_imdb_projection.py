"""IMDb credit facts for an existing game; no title or character-name matching."""
import hashlib
import json
import re

from core.catalogue_normalize import document, identity

PERSON_ID=re.compile(r'nm[0-9]+\Z')
ROLE=re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
VERSIONS={'English version':'en','French version':'fr','German version':'de',
    'Italian version':'it','Spanish version':'es','Japanese version':'ja',
    'Polish version':'pl','Russian version':'ru','Portuguese version':'pt',
    'Brazilian Portuguese version':'pt-BR','Korean version':'ko','Chinese version':'zh'}


def texts(rows,field):
    return [row[field] for row in rows or [] if isinstance(row,dict)
            and isinstance(row.get(field),str) and row[field].strip()]


def credit_document(igdb_id,title_id,rows,*,generation,offset=0,occurrences=None,page_number=0):
    # The enrichment publisher verifies this identity already belongs to its
    # requested catalogue entity. IMDb never supplies a competing game record.
    doc=document({'kind':'game','identities':[identity('igdb.game',igdb_id)],
                  'name':None,'attributes':{}})
    occurrences=dict(occurrences or {})
    omitted=0
    for position,raw in enumerate(rows,offset):
        person=raw.get('name') or {}
        native,name=person.get('id'),(person.get('nameText') or {}).get('text')
        category=raw.get('category') or {}
        role=category.get('id')
        if (not isinstance(native,str) or not PERSON_ID.fullmatch(native) or
            not isinstance(name,str) or not name.strip() or not isinstance(role,str) or not ROLE.fullmatch(role)):
            omitted+=1
            continue
        attributes=texts(raw.get('attributes'),'text')
        characters=texts(raw.get('characters'),'name')
        jobs=texts(raw.get('jobs'),'text')
        fingerprint=json.dumps([native,role,raw.get('characters'),raw.get('attributes'),raw.get('jobs')],
                               ensure_ascii=False,sort_keys=True,separators=(',',':'))
        digest=hashlib.sha256(fingerprint.encode()).hexdigest()
        ordinal=occurrences.get(digest,0)
        occurrences[digest]=ordinal+1
        cast=role in ('actor','actress','cast')
        voice=True if cast and any(value.casefold()=='voice' for value in attributes) else None
        canonical_role='voice_actor' if voice else 'actor' if cast else role
        languages=list(dict.fromkeys(VERSIONS[value] for value in attributes if value in VERSIONS))
        doc['credits'].append({
            'contributor':{'kind':'person','identities':[identity('imdb.person',native)],
                'name':name[:500],'attributes':{'imdb_id':native}},
            'role':canonical_role,'role_label':('Voice actor' if voice else 'Actor') if cast
                else (jobs[0] if len(jobs)==1 else category.get('text') or role.replace('_',' ').title())[:200],
            'source_key':f'{title_id}:{digest}:{ordinal}','position':position,
            'character_label':' / '.join(characters)[:500] or None,
            'attributes':{'work_domain':'game','source_active':True,'imdb_title_id':title_id,
                'imdb_generation':generation,'source_page':page_number,
                'imdb_category':category,'imdb_jobs':raw.get('jobs'),
                'imdb_characters':raw.get('characters'),'imdb_attributes':raw.get('attributes'),
                'voice':voice,'uncredited':True if any(value.casefold()=='uncredited' for value in attributes) else None,
                'credited_as':next((value[3:] for value in attributes if value.startswith('as ')),None),
                'languages':languages,'language_evidence':[value for value in attributes if value in VERSIONS],
                'game_role':{'characters':characters,'text_characters':characters,'jobs':jobs,
                    'performance_attributes':raw.get('attributes'),'voice':voice,
                    'credited_as':next((value[3:] for value in attributes if value.startswith('as ')),None),
                    'uncredited':True if any(value.casefold()=='uncredited' for value in attributes) else None},
                'source_url':f'https://www.imdb.com/title/{title_id}/fullcredits/'},
        })
    doc['coverage']={'credits_complete':False,'source_complete':False,'contributors':'partial',
        'source':'imdb','omitted_rows':omitted,'source_page':page_number}
    return doc,occurrences
