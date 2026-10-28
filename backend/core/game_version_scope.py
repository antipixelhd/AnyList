"""Keep an original IMDb work distinct from a linked later remake/remaster."""
from core.game_title_match import title_formats,years
from core.game_types import INDEPENDENT,igdb_game_class

VERSION=1


def reference_game(raw):
    return {key:raw[key] for key in ('id','name','game_type','parent_game','version_parent','first_release_date','release_dates') if key in raw}


def predates_version(header,attributes):
    year=(header.get('releaseYear') or {}).get('year')
    released=str(attributes.get('release_date') or '')[:4]
    return attributes.get('game_class') in ('remake','remaster') and type(year) is int and released.isdigit() and year<int(released)


def legacy_scope_condition(identity,entity):
    from sqlalchemy import and_,func
    source_year=identity.evidence['imdb_identity']['releaseYear']['year'].astext
    released=func.left(entity.attributes['release_date'].astext,4)
    return and_(identity.source=='wikidata',func.coalesce(identity.evidence['version_scope_policy'].as_integer(),0)!=VERSION,
        entity.attributes['game_class'].astext.in_(('remake','remaster')),
        source_year.op('~')('^[0-9]{4}$'),released.op('~')('^[0-9]{4}$'),source_year<released)


def original_parent(title,raw):
    parent=raw.get('parent_game')
    year=title.get('year');released=years(raw)
    if (igdb_game_class(raw) not in ('remake','remaster') or raw.get('version_parent')
            or type(year) is not int or not released or year>=min(released)
            or not isinstance(parent,dict) or type(parent.get('id')) is not int
            or parent['id']<=0 or parent['id']==raw.get('id')
            or igdb_game_class(parent) not in INDEPENDENT-{'port'}
            or not set().union(*(title_formats(name) for name in [title.get('title'),*title.get('aliases',[])]))
                & title_formats(parent.get('name'))):return None
    return parent['id']


def correction_reference(proof,previous,native):
    """A correction needs a checked original and the exact old version claim."""
    from core.game_title_match import valid_binding_evidence,VERSION
    scope=proof.get('version_reference') or {};raw=scope.get('game') or {}
    if (scope.get('version')!=1 or original_parent(proof.get('imdb') or {},raw)!=int(native)
            or str(raw.get('id'))!=previous.get('igdb_id')
            or any((scope.get('wikidata') or {}).get(key)!=(previous.get('wikidata') or {}).get(key)
                for key in ('wikidata_id','P345','P5794','P9043'))
            or not scope.get('wikidata',{}).get('wikidata_id')
            or not valid_binding_evidence({'matcher_version':VERSION,'igdb_id':native,'matching':proof},
                (proof.get('imdb') or {}).get('id'))):return False
    return True
