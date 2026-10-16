"""Company presentation and provider-declared aliases, never name-based merges."""
import re


def studio_name(value):
    name = re.sub(r'\s+', ' ', str(value or '')).strip()
    # Display-only legal suffixes. Historical names and separate production
    # labels remain separate identities, including Marvel Films/Marvel Studios.
    return re.sub(r'(?:,?\s+(?:LLC|L\.?L\.?C\.?|Inc\.?|Ltd\.?|Limited|GmbH|Corp\.?))+$',
                  '', name, flags=re.I).strip() or name


def studio_attributes(raw):
    aliases = raw.get('aliases') or []
    aliases = [item.get('name') if isinstance(item, dict) else item for item in aliases]
    return {**({'origin_country': raw.get('origin_country') or raw.get('country')}
               if raw.get('origin_country') or raw.get('country') else {}),
            'also_known_as': sorted({name.strip() for name in [raw.get('name'),
                *(raw.get('also_known_as') or []), *aliases] if isinstance(name, str) and name.strip()})}
