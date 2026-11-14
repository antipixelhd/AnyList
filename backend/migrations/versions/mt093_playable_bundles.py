"""Allow independently tracked game compilations without changing their identities."""
from alembic import op
import sqlalchemy as sa

revision='mt093_playable_bundles'
down_revision='mt092_season_rating_popup'
branch_labels=depends_on=None
OLD="'game','standalone_expansion','remake','remaster','expanded_game'"
NEW="'game','bundle','standalone_expansion','remake','remaster','expanded_game'"
FUNCTIONS=('game_require_independent()','game_require_child()','game_protect_work()',
           'refresh_game_contributor_search(integer[])')


def replace(old,new):
    db=op.get_bind()
    for name in FUNCTIONS:
        definition=db.scalar(sa.text('SELECT pg_get_functiondef(to_regprocedure(:name))'),{'name':name})
        if definition and old in definition:op.execute(definition.replace(old,new))


def upgrade():
    replace(OLD,NEW)
    db=op.get_bind()
    if db.scalar(sa.text("SELECT to_regprocedure('refresh_game_contributor_search(integer[])') IS NOT NULL")):
        op.execute("""SELECT refresh_game_contributor_search(ARRAY(
            SELECT DISTINCT coalesce(p.canonical_person_id,p.canonical_company_id,p.id)
            FROM catalogue_credits c JOIN catalogue_entities w ON w.id=c.work_id
            JOIN catalogue_entities p ON p.id=c.contributor_id
            WHERE w.kind='game' AND w.attributes->>'game_class'='bundle'))""")


def downgrade():
    db=op.get_bind()
    if db.scalar(sa.text("""SELECT EXISTS(SELECT 1 FROM catalogue_entities e WHERE e.attributes->>'game_class'='bundle'
        AND (EXISTS(SELECT 1 FROM game_entries g WHERE g.work_id=e.id)
            OR EXISTS(SELECT 1 FROM game_favorites f WHERE f.work_id=e.id)))""")):
        raise RuntimeError('Tracked compilations must be preserved; cannot disable bundle tracking')
    replace(NEW,OLD)
