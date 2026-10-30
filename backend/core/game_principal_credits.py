"""Principal Games presentation from stored credits, without provider acquisition.

Complete source credits remain stored. Jobs match exactly: assistants, support,
thanks and broad IMDb departments do not become creative leads by substring.
"""
import re
from collections import defaultdict

ACTING = {'actor', 'voice_actor', 'narrator'}
CAST_LIMIT = 24
STAFF_LIMIT = 16
ROLE_GROUPS = (
    ({'director', 'game director', 'creative director', 'creator', 'original creator'}, 4),
    ({'designer', 'game designer', 'lead designer', 'lead game designer', 'design director'}, 4),
    ({'writer', 'lead writer', 'head writer', 'narrative director', 'narrative designer', 'story', 'screenplay', 'author'}, 4),
    ({'composer', 'music', 'original music composer', 'music director', 'audio director', 'sound director'}, 2),
    ({'art director', 'artistic director', 'lead artist', 'character designer', 'animation director', 'lead animator'}, 2),
    ({'lead programmer', 'lead engineer', 'technical director', 'programming director'}, 2),
    ({'producer', 'executive producer', 'lead producer'}, 2),
)

def normalize(value):
    return re.sub(r'[_\s-]+', ' ', str(value or '').strip().casefold())

def group(row):
    attributes=row.get('attributes') or {}
    jobs=(attributes.get('game_role') or {}).get('jobs') or []
    # Detailed jobs override a broad category such as "Producer" or "Music".
    labels=jobs or [row.get('role_label') or row.get('role')]
    ranks=[i for i,(roles,_) in enumerate(ROLE_GROUPS) if any(normalize(label.split(':',1)[0]) in roles for label in labels if isinstance(label,str))]
    return min(ranks) if ranks else None

def select_credits(rows):
    people=defaultdict(list);companies=[]
    for row in rows:
        if row['kind']=='organization':
            if row['role'] in ('developer','publisher'):companies.append(row)
            continue
        attrs=row.get('attributes') or {};performance=attrs.get('game_role') or {}
        if attrs.get('uncredited') or performance.get('uncredited'):
            continue
        if re.search(r'\b(uncredited|archive footage|archive sound)\b',str(row.get('character_label') or ''),re.I):
            continue
        if row['role'] in ACTING or group(row) is not None:
            people[row['id']].append(row)
    def position(entries):
        return min((r['position'] for r in entries if type(r.get('position')) is int and r['position']>=0),default=10**9)
    cast={key:[r for r in entries if r['role'] in ACTING] for key,entries in people.items()}
    cast={key:entries for key,entries in cast.items() if entries}
    chosen_cast=sorted(cast,key=lambda key:(position(cast[key]),str(key)))[:CAST_LIMIT]
    staff={key:[r for r in entries if r['role'] not in ACTING and group(r) is not None] for key,entries in people.items()}
    staff={key:entries for key,entries in staff.items() if entries}
    counts=defaultdict(int);chosen_staff=[]
    for key in sorted(staff,key=lambda key:(min(group(r) for r in staff[key]),position(staff[key]),str(key))):
        rank=min(group(r) for r in staff[key])
        if len(chosen_staff)>=STAFF_LIMIT or counts[rank]>=ROLE_GROUPS[rank][1]:continue
        chosen_staff.append(key);counts[rank]+=1
    return [r for key in chosen_cast for r in cast[key]]+[r for key in chosen_staff for r in staff[key]]+companies
