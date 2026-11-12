"""Work-scoped game characters and performances; screen roles are a separate graph."""
from types import SimpleNamespace
from sqlalchemy import select,func,or_
from sqlalchemy.orm import load_only
from core.catalogue_normalize import image
from core.game_credit_roles import ACTING
from models.catalogue import CatalogueEntity, CatalogueCredit, CharacterAppearance, CharacterPerformance


async def displayed_credits(db,work_id):
    fields=('id','work_id','contributor_id','provider','role','role_label','position',
        'character_label','character_image_url','screen_character_id')
    query=select(*(getattr(CatalogueCredit,key) for key in fields),
        CatalogueCredit.attributes['languages'].label('languages'),
        CatalogueCredit.attributes['game_role'].label('game_role'),
        CatalogueCredit.attributes['uncredited'].label('uncredited'),CatalogueEntity).join(
            CatalogueEntity,CatalogueEntity.id==CatalogueCredit.contributor_id).where(
            CatalogueCredit.work_id==work_id,
            or_(CatalogueCredit.scope!='person',select(CharacterPerformance.id).where(
                CharacterPerformance.credit_id==CatalogueCredit.id,CharacterPerformance.source=='reviewed').exists()),
            ~func.coalesce(CatalogueCredit.attributes['source_active'].astext=='false',False))
    query=query.options(load_only(CatalogueEntity.id,CatalogueEntity.kind,CatalogueEntity.name,
        CatalogueEntity.image_url,CatalogueEntity.canonical_person_id,CatalogueEntity.canonical_company_id))
    rows=(await db.execute(query.order_by(CatalogueCredit.position.is_(None),CatalogueCredit.position,CatalogueCredit.id))).all()
    # Display reads never hydrate raw statements/references into ORM objects.
    # Full evidence remains available in the source records for future adapters.
    return [(SimpleNamespace(**{key:getattr(row,key) for key in fields},
        attributes={'languages':row.languages,'game_role':row.game_role,'uncredited':row.uncredited}),row.CatalogueEntity) for row in rows]


async def character_projection(db,work_id,credits):
    appearances=(await db.execute(select(CharacterAppearance.id,CharacterAppearance.character_id,
        CharacterAppearance.provider,CatalogueEntity.name,CatalogueEntity.image_url).join(
            CatalogueEntity,CatalogueEntity.id==CharacterAppearance.character_id)
        .where(CharacterAppearance.work_id==work_id).order_by(CharacterAppearance.id))).all()
    characters={};by_appearance={}
    for row in appearances:
        character=characters.setdefault(row.character_id,{'id':row.character_id,'name':row.name,
            'image':image(row.image_url),'providers':[],'performers':[]})
        if row.provider not in character['providers']:character['providers'].append(row.provider)
        by_appearance[row.id]=character
    actors={credit.id:(credit,person) for credit,person in credits if credit.role in ACTING}
    performances=(await db.execute(select(CharacterPerformance.credit_id,CharacterPerformance.appearance_id,
        CharacterPerformance.language,CharacterPerformance.source).where(CharacterPerformance.work_id==work_id,
            CharacterPerformance.credit_id.in_(actors)).order_by(CharacterPerformance.id))).all() if actors else []
    by_credit={}
    for row in performances:
        character=by_appearance.get(row.appearance_id)
        if not character:continue
        credit,person=actors[row.credit_id]
        by_credit.setdefault(row.credit_id,[]).append({'id':character['id'],'name':character['name'],
            'image':character['image'],'language':row.language,'source':row.source,
            'role':credit.role,'role_label':credit.role_label})
        actor={'id':person.id,'name':person.name,'image':image(person.image_url),
            'language':row.language,'role':credit.role,'role_label':credit.role_label,'url':f'/actors/catalogue:{person.id}?media_type=game'}
        if actor not in character['performers']:character['performers'].append(actor)
    return by_credit,list(characters.values())
