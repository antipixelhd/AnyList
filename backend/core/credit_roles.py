"""Roles that represent credited participation in a person's works timeline."""
import re

CREATOR_ROLES = frozenset(('director', 'supervising director', 'creator', 'showrunner',
                           'head writer', 'author', 'developer'))


def creator_role(role):
    return re.sub(r'[_\s-]+', ' ', str(role or '').strip().casefold()) in CREATOR_ROLES


def work_credit(role, character=None):
    label = re.sub(r'[_\s-]+', ' ', str(role or '').strip().casefold())
    if label in ('', 'contributor', 'unknown', 'acknowledgement', 'acknowledgements', 'acknowledgment',
                 'acknowledgments', 'dedication', 'dedicatee', 'in memory of', 'in loving memory of', 'inspiration', 'original concept'):
        return False
    if re.search(r'\b(?:thanks?|ideas?|uncredited|archive footage|archive sound)\b', label):
        return False
    return not re.search(r'\b(?:uncredited|archive footage|archive sound)\b', str(character or ''), re.I)
