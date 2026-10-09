"""Public, bounded Open Library reads. Bibliography is not character identity."""

import re
from urllib.parse import urlparse

from .config import settings
from .catalogue_normalize import document, entity, identity, isbn
from .catalogue_providers import ProviderError

BASE = "https://openlibrary.org"
EDITION_LIMIT = 20
AUTHOR_LIMIT = 10


def olid(value, suffix):
    value = str(value or "").rsplit("/", 1)[-1]
    if not re.fullmatch(r"OL[1-9][0-9]*" + suffix, value):
        raise ProviderError("invalid_request")
    return value


def text_value(value):
    return value.get("value") if isinstance(value, dict) else value


def cover(ids, author=False):
    value = next((x for x in ids or [] if isinstance(x, int) and x > 0), None)
    return (
        f"https://covers.openlibrary.org/{'a' if author else 'b'}/id/{value}-L.jpg"
        if value
        else None
    )


async def get(http, path, **kwargs):
    # The email identifies the application; it is server configuration, not API output.
    contact = settings.openlibrary_contact_email
    agent = "AnyList catalogue/1" + (f" ({contact})" if contact else "")
    result = await http.request(
        "openlibrary", "GET", BASE + path, headers={"User-Agent": agent}, **kwargs
    )
    if not isinstance(result, dict):
        raise ProviderError("bad_response")
    return result


async def search(http, query, page, limit):
    result = await get(
        http,
        "/search.json",
        params={
            "q": query,
            "page": page,
            "limit": limit,
            "fields": "key,title,author_name,cover_i,first_publish_year",
        },
    )
    return [
        {
            "external_id": olid(row["key"], "W"),
            "name": row["title"],
            "image_url": cover([row.get("cover_i")]),
            "authors": row.get("author_name", []),
            "first_publish_year": row.get("first_publish_year"),
        }
        for row in result.get("docs", [])[:limit]
    ]


async def lookup_isbn(http, value):
    value = isbn(value, 13) or isbn(value, 10)
    if not value:
        raise ProviderError("invalid_request")
    row = await get(http, f"/isbn/{value}.json")
    if row.get("_redirect"):
        target = urlparse(row["_redirect"])
        if (
            target.scheme not in ("", "https")
            or target.netloc not in ("", "openlibrary.org")
            or target.query
        ):
            raise ProviderError("bad_response")
        match = re.fullmatch(r"/books/(OL[1-9][0-9]*M)\.json", target.path)
        if not match:
            raise ProviderError("bad_response")
        row = await get(http, target.path)
    found = {
        isbn(x, length) for length in (10, 13) for x in row.get(f"isbn_{length}", [])
    }
    if value not in found:
        raise ProviderError("identity_conflict")
    return olid(row.get("key"), "M")


async def detail(http, external_id, page=1):
    if not 1 <= page <= 10000:
        raise ProviderError("invalid_request")
    external_id = olid(external_id, "[WM]")
    selected = None
    if external_id.endswith("M"):
        selected = await get(http, f"/books/{external_id}.json")
        if olid(selected.get("key"), "M") != external_id:
            raise ProviderError("identity_conflict")
        works = selected.get("works", [])
        if len(works) != 1:
            raise ProviderError("identity_conflict")
        external_id = olid(works[0].get("key"), "W")
    work = await get(http, f"/works/{external_id}.json")
    if olid(work.get("key"), "W") != external_id:
        raise ProviderError("identity_conflict")
    availability = await get(
        http,
        "/search.json",
        params={
            "q": "key:/works/" + external_id,
            "limit": 1,
            "fields": "key,ia,ebook_access,public_scan_b,has_fulltext,availability",
        },
    )
    matches = availability.get("docs", [])
    available = (
        matches[0]
        if matches and olid(matches[0].get("key"), "W") == external_id
        else {}
    )
    editions = await get(
        http,
        f"/works/{external_id}/editions.json",
        params={
            "limit": EDITION_LIMIT,
            "offset": (page - 1) * EDITION_LIMIT,
        },
    )
    entries = editions.get("entries", [])[:EDITION_LIMIT]
    if selected and not any(e.get("key") == selected["key"] for e in entries):
        entries = [selected, *entries]
    authors = {}
    author_refs = work.get("authors", [])
    for ref in author_refs:
        ref = ref.get("author", ref)
        id = olid(ref.get("key"), "A")
        if id in authors:
            continue
        if len(authors) >= AUTHOR_LIMIT:
            break
        author = await get(http, f"/authors/{id}.json")
        if olid(author.get("key"), "A") != id:
            raise ProviderError("identity_conflict")
        authors[id] = author
    raw = {
        **work,
        "_editions": entries,
        "_authors": authors,
        "_availability": available,
        "_coverage": {
            "edition_page": page,
            "edition_limit": EDITION_LIMIT,
            "editions_complete": page == 1 and editions.get("size", 0) <= EDITION_LIMIT,
            "has_more_editions": editions.get("size", 0) > page * EDITION_LIMIT,
            "edition_count": editions.get("size"),
            "authors_complete": len(
                {olid(a.get("author", a).get("key"), "A") for a in author_refs}
            )
            <= AUTHOR_LIMIT,
        },
    }
    return normalize(raw), raw


def normalize(raw):
    work_id = olid(raw.get("key"), "W")
    attrs = {
        key: raw.get(key)
        for key in (
            "subjects",
            "subject_places",
            "subject_times",
            "subject_people",
            "first_publish_date",
            "original_languages",
            "dewey_number",
            "lc_classifications",
            "first_sentence",
            "excerpts",
        )
    }
    available = raw.get("_availability", {})
    attrs.update(
        {
            key: available.get(key)
            for key in ("ebook_access", "public_scan_b", "has_fulltext", "availability")
        }
    )
    attrs["reading_links"] = [
        "https://archive.org/details/" + id
        for id in available.get("ia", [])
        if isinstance(id, str) and re.fullmatch(r"[A-Za-z0-9._-]+", id)
    ]
    # Subject people remain labels; never manufacture characters or contributors.
    doc = document(
        entity(
            "book",
            "openlibrary.book",
            {"id": work_id},
            name=raw.get("title"),
            description=text_value(raw.get("description")),
            artwork=cover(raw.get("covers")),
            attributes=attrs,
        )
    )
    authors = raw.get("_authors", {})
    for ref in raw.get("authors", []):
        id = olid(ref.get("author", ref).get("key"), "A")
        author = authors.get(id, {})
        doc["credits"].append(
            {
                "contributor": entity(
                    "person",
                    "openlibrary.author",
                    {"id": id, **author},
                    description=text_value(author.get("bio")),
                    artwork=cover(author.get("photos"), True),
                    attributes={
                        "birth_date": author.get("birth_date"),
                        "death_date": author.get("death_date"),
                        "alternate_names": author.get("alternate_names"),
                        "remote_ids": author.get("remote_ids"),
                    },
                ),
                "role": "author",
                "role_label": "Author",
                "source_key": "author:" + id,
            }
        )
    for row in raw.get("_editions", []):
        id = olid(row.get("key"), "M")
        if {olid(w.get("key"), "W") for w in row.get("works", [])} != {work_id}:
            raise ProviderError("identity_conflict")
        edition = entity(
            "edition",
            "openlibrary.edition",
            {"id": id},
            name=row.get("title"),
            description=text_value(row.get("description")),
            artwork=cover(row.get("covers")),
            attributes={
                key: row.get(key)
                for key in (
                    "publishers",
                    "publish_places",
                    "publish_date",
                    "languages",
                    "edition_name",
                    "lccn",
                    "oclc_numbers",
                    "dewey_decimal_class",
                    "lc_classifications",
                    "table_of_contents",
                    "first_sentence",
                    "notes",
                    "contributions",
                    "series",
                    "identifiers",
                    "ocaid",
                    "source_records",
                )
            },
        )
        for length in (10, 13):
            for value in row.get(f"isbn_{length}", []):
                value = isbn(value, length)
                if value:
                    edition["identities"].append(identity(f"isbn.{length}", value))
        # Do not invent a full date from a year or a bibliographic date string.
        published = row.get("publish_date", "")
        doc["editions"].append(
            {
                "entity": edition,
                "release_date": published
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", published)
                else None,
                "format": row.get("physical_format"),
                "pages": row.get("number_of_pages"),
                "language": row.get("languages", [{}])[0]
                .get("key", "")
                .rsplit("/", 1)[-1]
                if row.get("languages")
                else None,
            }
        )
    doc["coverage"] = raw.get("_coverage", {})
    return doc
