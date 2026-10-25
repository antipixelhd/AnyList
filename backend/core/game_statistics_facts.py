"""Parent-only game facts; no watch-history or child-state arithmetic."""
import asyncio
import math
import statistics
import re
from collections import defaultdict
from datetime import date
from urllib.parse import urlencode

from sqlalchemy import func,select
from sqlalchemy.orm import aliased

from core.game_types import INDEPENDENT
from core.game_contributor_works import ACTING
from core.catalogue_normalize import image
from models.catalogue import CatalogueEntity,CatalogueCredit,MetadataSnapshot
from models.games import GameEntry,GameAchievement,GameAchievementUnlock

STATUSES=('planning','playing','paused','completed','dropped')
BASELINES=(('main_story_seconds','Main Story'),('main_sides_seconds','Main + Sides'),('completionist_seconds','Completionist'))


async def load_facts(db,user_id):
    entry,work=GameEntry,CatalogueEntity
    query=select(entry.work_id.label('id'),work.name.label('title'),work.image_url.label('poster'),entry.status,
        entry.score,entry.finish_date,entry.total_seconds,entry.completion_seconds,entry.replay_count,
        entry.achievement_mode,entry.achievement_count,entry.achievement_percentage,
        work.attributes['genres'].label('genres'),work.attributes['release_date'].astext.label('release_date'),
        work.attributes['genre_filters'].label('genre_filters'),
        work.attributes['completion_estimates'].label('estimates'),
        func.jsonb_build_object('total',work.attributes['achievement_catalogue']['total']).label('achievements')).join(
        work,work.id==entry.work_id).where(entry.user_id==user_id,work.kind=='game',
        work.attributes['game_class'].astext.in_(INDEPENDENT-{'port'}),work.attributes['adult'].astext.is_distinct_from('true'))
    result=await db.execute(query)
    facts=await asyncio.to_thread(lambda:[dict(row) for row in result.mappings()])
    if not facts:return [],[]
    if any(not row.get('genre_filters') for row in facts):
        references=await db.scalar(select(MetadataSnapshot.payload['rows']).where(MetadataSnapshot.provider=='igdb',
            MetadataSnapshot.kind=='game_browse_refs',MetadataSnapshot.external_id=='v1:genres'))
        genres={row['name'].casefold():f"igdb.genre:{row['id']}" for row in references or []
            if isinstance(row,dict) and isinstance(row.get('name'),str) and type(row.get('id')) is int and row['id']>0}
        for row in facts:row['genre_filters']={**genres,**(row.get('genre_filters') or {})}
    if not any(row['status']!='planning' for row in facts):return facts,[]
    ids=query.with_only_columns(entry.work_id).where(entry.status!='planning')
    unlocked=dict((await db.execute(select(GameAchievementUnlock.work_id,func.count()).join(
        GameAchievement,GameAchievement.id==GameAchievementUnlock.achievement_id).where(GameAchievement.active.is_(True),
        GameAchievementUnlock.user_id==user_id,GameAchievementUnlock.work_id.in_(ids)).group_by(GameAchievementUnlock.work_id))).all())
    for fact in facts:fact['exact_count']=unlocked.get(fact['id'],0)
    person,root=aliased(work),aliased(work)
    result=await db.execute(select(CatalogueCredit.work_id,
        func.coalesce(root.id,person.id).label('id'),func.coalesce(root.name,person.name).label('name'),
        func.coalesce(root.image_url,person.image_url).label('image'),person.kind,CatalogueCredit.role,
        CatalogueCredit.role_label).join(person,person.id==CatalogueCredit.contributor_id).outerjoin(root,
            root.id==func.coalesce(person.canonical_person_id,person.canonical_company_id)).where(
        CatalogueCredit.work_id.in_(ids),CatalogueCredit.attributes['source_active'].astext.is_distinct_from('false')).distinct())
    credits=await asyncio.to_thread(lambda:[dict(row) for row in result.mappings()])
    return facts,credits


def nonnegative(value):return value if type(value) is int and value>=0 else None


def estimate(fact,key):
    value=(fact.get('estimates') or {}).get(key)
    return value if type(value) is int and value>0 else None


def year(value):
    if isinstance(value,date):return str(value.year)
    value=str(value or '')[:4]
    return value if len(value)==4 and value.isdigit() and int(value)>0 else None


def metric(key,label,rows,*,time=True):
    scores=[row['score'] for row in rows if row.get('score') is not None]
    durations=[row['total_seconds'] for row in rows if nonnegative(row.get('total_seconds')) is not None] if time else []
    return {'key':str(key),'label':label,'games':len(rows),'recorded_seconds':sum(durations) if durations else None,
        'known_time_games':len(durations),'mean_score':statistics.fmean(scores) if scores else None,'rated_games':len(scores)}


def title(row):
    return {'key':f"game:{row['id']}",'title':row['title'],'poster':image(row.get('poster')),
        'href':f"/games/{row['id']}",'score':row.get('score')}


def grouped(rows,key_fn,*,time=True):
    groups=defaultdict(list)
    for row in rows:
        key=key_fn(row)
        if key is not None:groups[str(key)].append(row)
    return [metric(key,key,group,time=time) for key,group in sorted(groups.items())]


def build_statistics(facts,credits):
    played=[row for row in facts if row['status']!='planning'];planned=[row for row in facts if row['status']=='planning']
    completed=[row for row in played if row['status']=='completed'];all_metrics=metric('all','Games',played)
    known_percentages=[];counts=[]
    for row in played:
        mode=row.get('achievement_mode');total=(row.get('achievements') or {}).get('total')
        count=nonnegative(row.get('exact_count')) if mode=='targeted' else nonnegative(row.get('achievement_count')) if mode=='count' else None
        percentage=row.get('achievement_percentage') if mode=='percentage' else count/total*100 if count is not None and type(total) is int and total>0 and count<=total else None
        if type(percentage) in (int,float) and math.isfinite(percentage) and 0<=percentage<=100:known_percentages.append(percentage)
        if count is not None:counts.append(count)
    planned_estimates=[value for row in planned if (value:=estimate(row,'main_sides_seconds')) is not None]
    comparisons=[]
    valid_completed=[row for row in completed if row.get('finish_date') is not None and nonnegative(row.get('completion_seconds')) is not None
        and (row.get('total_seconds') is None or row['total_seconds']>=row['completion_seconds'])]
    for key,label in BASELINES:
        pairs=[(row['completion_seconds'],baseline) for row in valid_completed if (baseline:=estimate(row,key)) is not None]
        differences=[actual-baseline for actual,baseline in pairs]
        comparisons.append({'key':key,'label':label,'games':len(pairs),
            'mean_difference_seconds':statistics.fmean(differences) if pairs else None,
            'mean_absolute_difference_seconds':statistics.fmean(abs(value) for value in differences) if pairs else None,
            'mean_relative_percent':statistics.fmean((actual-baseline)/baseline*100 for actual,baseline in pairs) if pairs else None})
    hours=[];bounds=[0,1,5,10,20,50,100,200]
    for index,lower in enumerate(bounds):
        upper=bounds[index+1] if index+1<len(bounds) else None
        rows=[row for row in played if (seconds:=nonnegative(row.get('total_seconds'))) is not None
            and seconds>=lower*3600 and (upper is None or seconds<upper*3600)]
        hours.append({**metric(str(lower),'<1h' if lower==0 else f'{lower}h+',rows),
            'lower_seconds':lower*3600,'upper_seconds':upper*3600 if upper is not None else None})
    scores=[{**metric(str(half/2),str(half/2),[row for row in played if row.get('score') is not None and math.ceil(row['score']*2)==half])} for half in range(1,21)]
    score_values=[row['score'] for row in played if row.get('score') is not None]
    replays=[row['replay_count'] for row in played if nonnegative(row.get('replay_count')) is not None]
    overview={'domain':'game','totals':{'listed_games':len(facts),'played_games':len(played),'completed_games':len(completed),
        'recorded_seconds':all_metrics['recorded_seconds'],'known_time_games':all_metrics['known_time_games'],
        'mean_score':all_metrics['mean_score'],'rated_games':all_metrics['rated_games'],
        'standard_deviation':statistics.pstdev(score_values) if score_values else None,
        'replays':sum(replays),'replay_games':len(replays),
        'achievement_percentage':statistics.fmean(known_percentages) if known_percentages else None,
        'achievement_progress_games':len(known_percentages),'achievement_count':sum(counts) if counts else None,'achievement_count_games':len(counts)},
        'planned':{'games':len(planned),'estimated_seconds':sum(planned_estimates) if planned_estimates else None,'known_estimate_games':len(planned_estimates)},
        'statuses':[{'key':status,'label':'Planned' if status=='planning' else status.title(),'games':sum(row['status']==status for row in facts)} for status in STATUSES],
        'scores':scores,'hours':hours,'release_years':grouped(played,lambda row:year(row.get('release_date'))),
        'completion_years':grouped(completed,lambda row:year(row.get('finish_date')),time=False),'comparisons':comparisons,
        'coverage':{'unknown_time_games':len(played)-all_metrics['known_time_games'],
            'unknown_achievement_games':len(played)-len(known_percentages),
            'undated_completions':sum(row.get('finish_date') is None for row in completed),
            'comparison_eligible_games':len(valid_completed),'unknown_release_games':sum(year(row.get('release_date')) is None for row in played)}}
    sections={name:{} for name in ('genres','actors','staff','developers','publishers')};by_id={row['id']:row for row in played}
    def add(section,key,label,row,*,href=None,image=None,role=None):
        group=sections[section].setdefault(str(key),{'label':label,'rows':{},'href':href,'image':image,'roles':set()})
        group['rows'][row['id']]=row
        if href and not group['href']:group['href']=href
        if role:group['roles'].add(role)
    for row in played:
        for genre in row.get('genres') or []:
            label=genre if isinstance(genre,str) else genre.get('name') if isinstance(genre,dict) else None
            if isinstance(label,str) and label.strip():
                reference=(row.get('genre_filters') or {}).get(label.casefold())
                href='/browse/games?'+urlencode({'genres':reference}) if isinstance(reference,str) and re.fullmatch(r'igdb\.genre:[1-9][0-9]*',reference) else None
                add('genres',label.casefold(),label,row,href=href)
    for credit in credits:
        row=by_id.get(credit['work_id'])
        if not row:continue
        role=credit['role'];company=credit['kind']=='organization'
        section='developers' if company and role=='developer' else 'publishers' if company and role=='publisher' else None
        if not company:section='actors' if role in ACTING else 'staff' if role!='thanks' else None
        if section:
            path='studio' if company else 'actors' if section=='actors' else 'person'
            add(section,credit['id'],credit['name'],row,href=f"/{path}/catalogue:{credit['id']}?media_type=game",image=credit.get('image'),role=credit.get('role_label') or role.replace('_',' ').title())
    payload={'overview':overview}
    for section,groups in sections.items():
        values=[]
        for key,group in groups.items():
            rows=list(group['rows'].values());top=sorted(rows,key=lambda row:(-(row.get('score') or 0),row['title'],row['id']))[:6]
            values.append({**metric(key,group['label'],rows),'href':group['href'],'image':image(group['image']),
                'roles':sorted(group['roles']),'top_titles':[title(row) for row in top]})
        payload[section]=sorted(values,key=lambda row:(-row['games'],row['label'].casefold(),row['key']))
    return payload
