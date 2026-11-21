"""Game tracking stores durations in tenths of an hour."""

SECONDS_PER_TENTH = 360


def round_game_seconds(seconds: int) -> int:
    return ((seconds + SECONDS_PER_TENTH // 2) // SECONDS_PER_TENTH) * SECONDS_PER_TENTH
