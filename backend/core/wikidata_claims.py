"""Pure Wikibase statement primitives shared by identity and credit adapters."""
import re

ITEM = re.compile(r'Q[1-9][0-9]*\Z')


def snak_value(snak):
    if not isinstance(snak,dict) or snak.get('snaktype')!='value':
        return None
    value=(snak.get('datavalue') or {}).get('value')
    return value.get('id') if isinstance(value,dict) and value.get('entity-type')=='item' else value


def claims(item,prop):
    return [row for row in (item.get('claims') or {}).get(prop,[])
            if isinstance(row,dict) and row.get('rank')!='deprecated']


def best_claims(item,prop):
    rows=claims(item,prop)
    preferred=[row for row in rows if row.get('rank')=='preferred']
    return preferred or rows


def values(item,prop):
    return [value for row in claims(item,prop) if (value:=snak_value(row.get('mainsnak'))) is not None]


def qualified(row,prop):
    return [value for snak in (row.get('qualifiers') or {}).get(prop,[])
            if (value:=snak_value(snak)) is not None]


def item_ids(rows):
    return {value for value in rows if isinstance(value,str) and ITEM.fullmatch(value)}
