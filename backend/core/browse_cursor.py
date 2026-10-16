"""Independent positions for the local, cached catalogue and provider streams."""
import base64
import json


def decode_search_cursor(value):
    if not value:
        return None
    try:
        positions=json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        if not isinstance(positions,list) or len(positions)!=3:
            raise ValueError
        remote,local,feed=positions
        if any(type(position) is not int for position in positions) or not 1<=remote<=501 or not -1<=local<=10000000 or not -1<=feed<=10000000:
            raise ValueError
        return remote,local,feed
    except (ValueError,TypeError,UnicodeError) as error:
        raise ValueError('Invalid search cursor') from error


def encode_search_cursor(remote,local,feed):
    return base64.urlsafe_b64encode(json.dumps([remote,local,feed],separators=(',',':')).encode()).decode().rstrip('=')
