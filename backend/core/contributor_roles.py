"""Primary professions and the two views of one person's credits."""

PERSON_ROLES = ('actor', 'staff')

PROFESSIONS = {
    'acting': 'Actor',
    'actor': 'Actor',
    'actress': 'Actor',
    'voice actor': 'Voice Actor',
    'directing': 'Director',
    'director': 'Director',
    'writing': 'Writer',
    'writer': 'Writer',
    'production': 'Producer',
    'producer': 'Producer',
    'camera': 'Camera',
    'cinematographer': 'Cinematographer',
    'sound': 'Sound',
    'composer': 'Composer',
    'editing': 'Editor',
    'editor': 'Editor',
    'art': 'Art',
    'costume & make-up': 'Costume & Make-Up',
    'visual effects': 'Visual Effects',
}


def primary_profession(department):
    # IMDb may supply several professions in provider order.
    first = str(department or '').split('/', 1)[0].replace('_', ' ').strip().casefold()
    label = PROFESSIONS.get(first)
    if not label:
        return None, None
    return ('actor' if label in ('Actor', 'Voice Actor') else 'staff'), label


def preferred_role(department, counts, *, fallback='actor'):
    primary, _ = primary_profession(department)
    if primary:
        return primary
    if counts.get('actor', 0) != counts.get('staff', 0):
        return max(PERSON_ROLES, key=lambda role: counts.get(role, 0))
    return fallback
