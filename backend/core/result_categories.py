"""Category exclusions shared by profile defaults and catalogue filtering."""

EXCLUDABLE_CATEGORIES = frozenset({99, 10402, 10770, 10763, 10764, 10766, 10767})


def default_excluded_categories() -> list[int]:
    return sorted(EXCLUDABLE_CATEGORIES)
