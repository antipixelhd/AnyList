"""Game performance roles retain their source meaning across public projections."""

ACTING = ('actor', 'voice_actor', 'narrator', 'archive_sound', 'archive_footage')
SOURCE_ACTING = frozenset((*ACTING, 'actress', 'cast'))
CREDIT_POLICY_VERSION = 2
