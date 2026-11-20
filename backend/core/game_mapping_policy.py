"""Bounded retries for unknown identities; deferral never establishes exclusion."""
from datetime import timedelta


PERSISTENT_REASONS = frozenset({
    'title_year_evidence', 'ambiguous_candidates', 'unknown_competitor',
    'unresolved_alias_scope', 'igdb_reference', 'explicit_identity_conflict',
    'credit_parent', 'expansion_parent', 'variant_parent_scope', 'version_parent_mismatch',
})


def source_title(title):
    return {key: title.get(key) for key in ('id', 'title', 'year')}


def prepare(outcome, title, previous=None):
    value = {**outcome, 'source_title': source_title(title)}
    if value.get('retry_after'):
        same = previous and previous.get('source_title') == value['source_title']
        attempts = (previous.get('retry_attempts', 0) if same else 0) + 1
        value['retry_attempts'] = attempts
        if attempts >= 3:
            # Finish this discovery pass with an unknown result. Daily admission
            # can try again; an outage must not keep a profile looping forever.
            value.pop('retry_after')
            value['deferred_retry'] = True
        elif value['retry_after'] >= 60:
            value['retry_after'] = min(3600, value['retry_after'] * 2 ** (attempts - 1))
    return value


def lifetime(value):
    if value.get('retry_after'):
        return timedelta(seconds=value['retry_after'])
    if value['status'] in ('verified', 'excluded'):
        return timedelta(days=7)
    if not value.get('deferred_retry') and (value['status'] == 'unmapped'
            or value.get('reason') in PERSISTENT_REASONS):
        return timedelta(days=30)
    return timedelta(days=1)


def corroboration_useful(decision):
    # Positive support cannot break a tie between two exact, released,
    # non-port identities: decide() rejects it before considering other proof.
    # Cancelled ports retain their special independently verified launch path.
    if decision.get('reason') != 'ambiguous_candidates':
        return True
    tied = [row for row in decision['evidence']['candidates']
            if row['rank'] == 3 and row['year_match'] and not row['edition']
            and row['game_class'] not in ('unknown', 'port')]
    return len(tied) < 2
