"""Game performance roles retain their source meaning across public projections."""

ACTING = ('actor', 'voice_actor', 'narrator', 'archive_sound', 'archive_footage')
SOURCE_ACTING = frozenset((*ACTING, 'actress', 'cast'))
CREDIT_POLICY_VERSION = 3


def source_role(row):
    """Use explicit performance jobs; a miscellaneous category alone proves nothing."""
    category=(row.get('category') or {}).get('id')
    if category=='voice_actors_dubbing':return 'voice_actor'
    if category=='miscellaneous' and any(
        isinstance(job,dict) and isinstance(job.get('text'),str)
        and job['text'].strip().casefold().startswith('voice actor:')
        and job['text'].split(':',1)[1].strip() for job in row.get('jobs') or []):
        return 'voice_actor'
    return category


def voice_characters(row):
    if source_role(row)!='voice_actor':return []
    return [job['text'].split(':',1)[1].strip() for job in row.get('jobs') or []
        if isinstance(job,dict) and isinstance(job.get('text'),str)
        and job['text'].strip().casefold().startswith('voice actor:') and job['text'].split(':',1)[1].strip()]
