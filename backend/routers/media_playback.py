"""Playback sources, subtitles, stream proxies, and session reports."""

import asyncio
import logging
import re
import urllib.parse
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from db import AsyncSessionLocal, get_db
from dependencies import get_current_user
from models.base import CollectionSource, MediaType
from models.collection import Collection, CollectionFile
from models.connections import MediaServerConnection
from models.media import Media
from models.users import User

logger = logging.getLogger(__name__)
router = APIRouter()

class SessionReportRequest(BaseModel):
    connection_id: int
    state: str  # "playing" | "progress" | "paused" | "stopped"
    position_ms: int = 0
    duration_ms: int = 0
    file_id: int | None = None
    plex_session_id: str | None = None  # Plex Universal Transcoder session ID for keepalive pings


def _srt_to_vtt(srt: str) -> str:
    vtt = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt)
    return "WEBVTT\n\n" + vtt.strip()


@router.get("/playback/{type}/{tmdb_id}")
async def get_playback_sources(
    type: MediaType,
    tmdb_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    media_id: int | None = Query(None),
):
    """Return available local-server playback sources for a movie or episode."""
    if type not in (MediaType.movie, MediaType.episode):
        raise HTTPException(400, "Only movie/episode streaming supported")

    if media_id:
        media_q = await db.execute(select(Media.id).where(Media.id == media_id))
        media_ids = [r[0] for r in media_q.all()]
    else:
        # Collect ALL Media rows for this tmdb_id — a manually-matched movie may
        # have its CollectionFile on a different row than the one .first() would pick.
        media_q = await db.execute(
            select(Media.id).where(Media.tmdb_id == tmdb_id, Media.media_type == type)
        )
        media_ids = [r[0] for r in media_q.all()]
    if not media_ids:
        return []

    files_q = await db.execute(
        select(CollectionFile, MediaServerConnection)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .outerjoin(MediaServerConnection, MediaServerConnection.id == CollectionFile.connection_id)
        .where(
            Collection.media_id.in_(media_ids),
            Collection.user_id == current_user.id,
            CollectionFile.source.in_([CollectionSource.jellyfin, CollectionSource.emby, CollectionSource.plex]),
            CollectionFile.connection_id.isnot(None),
            CollectionFile.source_id.isnot(None),
        )
    )

    from core import jellyfin as jellyfin_core
    from core import plex as plex_core

    sources = []
    for cf, conn in files_q.all():
        if not conn:
            continue
        resolution = cf.resolution
        subtitles: list[dict] = []
        audio_tracks: list[dict] = []

        if cf.source.value in ("jellyfin", "emby") and cf.source_id:
            try:
                item = await jellyfin_core.get_item(conn.url, conn.token, cf.source_id, user_id=conn.server_user_id)
                if item:
                    if resolution is None:
                        q = jellyfin_core.extract_quality(item.get("MediaStreams", []))
                        resolution = q.get("resolution")
                    for stream in item.get("MediaStreams", []):
                        if stream.get("Type") == "Subtitle":
                            codec = (stream.get("Codec") or "").lower()
                            # Skip image-based subtitle formats — they cannot be served as VTT
                            if codec in {"hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvbsub", "dvb_subtitle"}:
                                continue
                            lang = stream.get("Language") or None
                            label = stream.get("DisplayTitle") or stream.get("Title") or lang or "Subtitle"
                            subtitles.append({
                                "index": stream.get("Index"),
                                "language": lang,
                                "label": label,
                                "codec": codec,
                            })
                        elif stream.get("Type") == "Audio":
                            lang = stream.get("Language") or None
                            label = stream.get("DisplayTitle") or stream.get("Title") or lang or "Audio"
                            audio_tracks.append({
                                "index": stream.get("Index"),
                                "language": lang,
                                "label": label,
                                "codec": stream.get("Codec"),
                            })
            except Exception:
                pass

        elif cf.source.value == "plex" and cf.source_id:
            _plex_image_codecs = {"pgssub", "vobsub", "dvd_subtitle", "dvbsub"}
            plex_item_valid = False
            try:
                item = await plex_core.get_item(conn.url, conn.token, cf.source_id)
                if item:
                    plex_item_valid = True
                    media_list = item.get("Media", [])
                    if media_list and media_list[0].get("Part"):
                        for stream in media_list[0]["Part"][0].get("Stream", []):
                            stype = stream.get("streamType")
                            if stype == 3:
                                codec = (stream.get("codec") or "").lower()
                                if codec in _plex_image_codecs:
                                    continue
                                skey = stream.get("key")
                                if not skey:
                                    continue
                                lang = stream.get("languageCode") or stream.get("languageTag") or None
                                label = stream.get("displayTitle") or stream.get("title") or lang or "Subtitle"
                                subtitles.append({
                                    "index": stream.get("id"),
                                    "key": skey,
                                    "language": lang,
                                    "label": label,
                                    "codec": codec,
                                })
                            elif stype == 2:
                                lang = stream.get("languageCode") or stream.get("languageTag") or None
                                label = stream.get("displayTitle") or stream.get("title") or lang or "Audio"
                                audio_tracks.append({
                                    "index": stream.get("id"),
                                    "language": lang,
                                    "label": label,
                                    "codec": stream.get("codec"),
                                })
            except Exception:
                pass
            if not plex_item_valid:
                continue

        sources.append({
            "file_id": cf.id,
            "connection_id": cf.connection_id,
            "source": cf.source.value,
            "name": conn.name or cf.source.value.title(),
            "resolution": resolution,
            "video_codec": cf.video_codec,
            "audio_codec": cf.audio_codec,
            "subtitles": subtitles,
            "audio_tracks": audio_tracks,
        })

    return sources


@router.get("/subtitles/{type}/{tmdb_id}")
async def get_subtitle(
    type: MediaType,
    tmdb_id: int,
    connection_id: int,
    stream_index: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    media_id: int | None = Query(None),
    file_id: int | None = Query(None),
    key: str | None = Query(None),
    session: str | None = Query(None),
):
    """Proxy a subtitle track as WebVTT from a Jellyfin, Emby, or Plex server."""
    if type not in (MediaType.movie, MediaType.episode):
        raise HTTPException(400, "Only movie/episode subtitles supported")

    if media_id:
        media_ids = [media_id]
    else:
        media_id_q = await db.execute(
            select(Media.id).where(Media.tmdb_id == tmdb_id, Media.media_type == type)
        )
        media_ids = [r[0] for r in media_id_q.all()]
    if not media_ids:
        raise HTTPException(404, "Not in library")

    sub_cf_filters = [
        Collection.media_id.in_(media_ids),
        Collection.user_id == current_user.id,
        CollectionFile.connection_id == connection_id,
    ]
    if file_id is not None:
        sub_cf_filters.append(CollectionFile.id == file_id)
    cf_q = await db.execute(
        select(CollectionFile, MediaServerConnection)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .outerjoin(MediaServerConnection, MediaServerConnection.id == CollectionFile.connection_id)
        .where(*sub_cf_filters)
    )
    row = cf_q.first()
    if not row:
        raise HTTPException(404, "Source not found")

    cf, conn = row
    if not conn or not cf.source_id:
        raise HTTPException(400, "No valid connection configured")

    cache_headers = {"Cache-Control": "private, max-age=3600"}

    if cf.source.value in ("jellyfin", "emby"):
        sub_url = (
            f"{conn.url.rstrip('/')}/Videos/{cf.source_id}"
            f"/{cf.source_id}/Subtitles/{stream_index}/0/Stream.vtt"
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            res = await client.get(sub_url, headers={"Authorization": f'MediaBrowser Token="{conn.token}"'})
        if res.status_code >= 400:
            raise HTTPException(502, "Subtitle not available")
        return Response(content=res.content, media_type="text/vtt", headers=cache_headers)

    elif cf.source.value == "plex":
        plex_headers = {"X-Plex-Token": conn.token}
        if key and key != "None":
            # External sidecar subtitle — fetch directly via the key Plex provided.
            sub_url = f"{conn.url.rstrip('/')}{key}"
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                res = await client.get(sub_url, headers=plex_headers)
        else:
            # Embedded subtitle: Plex serves text-based embedded streams (SRT, ASS)
            # at /library/streams/{id}. Try that first; fall back to the item-metadata
            # lookup so we're resilient to stream-ID changes.
            sub_url = f"{conn.url.rstrip('/')}/library/streams/{stream_index}"
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                res = await client.get(sub_url, headers=plex_headers)
            if res.status_code >= 400:
                # Secondary fallback: re-fetch the item and use whatever key Plex has.
                logger.warning(
                    "plex subtitle /library/streams/%d returned %d — trying item metadata fallback",
                    stream_index, res.status_code,
                )
                from core import plex as plex_core
                item = await plex_core.get_item(conn.url, conn.token, cf.source_id)
                fallback_key: str | None = None
                if item:
                    for stream in item.get("Media", [{}])[0].get("Part", [{}])[0].get("Stream", []):
                        if int(stream.get("streamType") or 0) == 3 and str(stream.get("id")) == str(stream_index):
                            fallback_key = stream.get("key")
                            break
                if fallback_key:
                    sub_url = f"{conn.url.rstrip('/')}{fallback_key}"
                    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                        res = await client.get(sub_url, headers=plex_headers)
                else:
                    logger.warning("plex subtitle stream %d: no key available and /library/streams failed", stream_index)
                    raise HTTPException(502, "Subtitle not available from Plex")
        if res.status_code >= 400:
            raise HTTPException(502, "Subtitle not available from Plex")
        content = res.text
        if not content.strip().startswith("WEBVTT"):
            content = _srt_to_vtt(content)
        return Response(content=content.encode("utf-8"), media_type="text/vtt", headers=cache_headers)

    else:
        raise HTTPException(400, f"Subtitles not supported for source: {cf.source.value}")


@router.get("/stream/{type}/{tmdb_id}")
async def stream_media(
    type: MediaType,
    tmdb_id: int,
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    media_id: int | None = Query(None),
    file_id: int | None = Query(None),
):
    """Proxy a direct video stream from a Plex server (Jellyfin/Emby use the HLS endpoint)."""
    if type not in (MediaType.movie, MediaType.episode):
        raise HTTPException(400, "Only movie/episode streaming supported")

    if media_id:
        media_ids = [media_id]
    else:
        media_id_q = await db.execute(
            select(Media.id).where(Media.tmdb_id == tmdb_id, Media.media_type == type)
        )
        media_ids = [r[0] for r in media_id_q.all()]
    if not media_ids:
        raise HTTPException(404, "Not in library")

    cf_filters = [
        Collection.media_id.in_(media_ids),
        Collection.user_id == current_user.id,
        CollectionFile.connection_id == connection_id,
    ]
    if file_id is not None:
        cf_filters.append(CollectionFile.id == file_id)
    cf_q = await db.execute(
        select(CollectionFile, MediaServerConnection)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .outerjoin(MediaServerConnection, MediaServerConnection.id == CollectionFile.connection_id)
        .where(*cf_filters)
    )
    row = cf_q.first()
    if not row:
        raise HTTPException(404, "Source not found")

    cf, conn = row
    if not conn or not cf.source_id:
        raise HTTPException(400, "No valid connection configured")

    upstream_headers: dict[str, str] = {}
    range_header = request.headers.get("Range")
    # Always send a Range header upstream — without it Plex/Jellyfin return 200 OK
    # with the full Content-Length, and Firefox downloads the entire file before
    # starting playback. Forcing 206 Partial Content lets Firefox stream correctly.
    upstream_headers["Range"] = range_header if range_header else "bytes=0-"

    if cf.source.value in ("jellyfin", "emby"):
        upstream_headers["Authorization"] = f'MediaBrowser Token="{conn.token}"'
        stream_url = f"{conn.url.rstrip('/')}/Videos/{cf.source_id}/stream"
        params: dict = {"Static": "true"}
    elif cf.source.value == "plex":
        from core import plex as plex_core
        item = await plex_core.get_item(conn.url, conn.token, cf.source_id)
        if not item:
            raise HTTPException(502, "Could not fetch item from Plex")
        media_list = item.get("Media", [])
        if not media_list or not media_list[0].get("Part"):
            raise HTTPException(502, "No media part found in Plex")
        part_key = media_list[0]["Part"][0]["key"]
        stream_url = f"{conn.url.rstrip('/')}{part_key}"
        upstream_headers["X-Plex-Token"] = conn.token
        params = {}
    else:
        raise HTTPException(400, f"Streaming not supported for source: {cf.source.value}")

    try:
        client = httpx.AsyncClient(timeout=httpx.Timeout(None))
        upstream_req = client.build_request("GET", stream_url, headers=upstream_headers, params=params)
        upstream_res = await client.send(upstream_req, stream=True)
    except Exception as e:
        raise HTTPException(502, f"Could not connect to media server: {e}")

    res_headers: dict[str, str] = {"Accept-Ranges": "bytes"}
    for h in ("Content-Type", "Content-Length", "Content-Range"):
        v = upstream_res.headers.get(h.lower())
        if v:
            if h == "Content-Type" and v.lower() in ("video/x-matroska", "video/mkv"):
                v = "video/webm"
            res_headers[h] = v

    async def cleanup() -> None:
        await upstream_res.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream_res.aiter_bytes(65536),
        status_code=upstream_res.status_code,
        headers=res_headers,
        background=BackgroundTask(cleanup),
    )


def _rewrite_m3u8_urls(content: str, connection_id: int, request_path: str, inherit_qs: str = "") -> str:
    """Rewrite every URL line in an M3U8 manifest to route through our HLS segment proxy.

    inherit_qs: raw query string from the parent manifest URL (e.g. "DeviceId=scrob&PlaySessionId=...&api_key=...").
    Jellyfin variant playlists use bare relative paths like "0.ts" with no query params; Jellyfin
    still needs DeviceId and PlaySessionId on those requests to locate the active transcoding job.
    Inheriting the parent query string restores them.
    """
    base_dir = request_path.rsplit("/", 1)[0] if "/" in request_path else ""
    lines = content.splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        # Resolve absolute vs relative path
        if stripped.startswith("http://") or stripped.startswith("https://"):
            parsed = urllib.parse.urlparse(stripped)
            path_qs = parsed.path + ("?" + parsed.query if parsed.query else "")
        elif stripped.startswith("/"):
            path_qs = stripped
        else:
            # Relative — resolve against the directory of the current manifest and
            # inherit the parent manifest's session query params (DeviceId, PlaySessionId, api_key).
            path_qs = base_dir + "/" + stripped
            if inherit_qs and "?" not in path_qs:
                path_qs += "?" + inherit_qs
        encoded = urllib.parse.quote(path_qs, safe="")
        out.append(f"/api/proxy/media/hls-segment?connection_id={connection_id}&path={encoded}")
    return "\n".join(out)


async def _get_conn_for_user(connection_id: int, user_id: int, db: AsyncSession) -> MediaServerConnection:
    """Fetch a MediaServerConnection and verify it belongs to the given user."""
    result = await db.execute(
        select(MediaServerConnection).where(
            MediaServerConnection.id == connection_id,
            MediaServerConnection.user_id == user_id,
        )
    )
    conn = result.scalars().first()
    if not conn:
        raise HTTPException(404, "Connection not found")
    return conn


@router.get("/hls/{type}/{tmdb_id}")
async def hls_master_manifest(
    type: MediaType,
    tmdb_id: int,
    connection_id: int,
    audio_stream_index: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    media_id: int | None = Query(None),
    file_id: int | None = Query(None),
    video_codecs: str | None = Query(None),
    audio_codecs: str | None = Query(None),
):
    """Fetch and rewrite an HLS master M3U8 from Emby/Jellyfin so all segment URLs
    are proxied through this server. Creates a proper transcoding session on the server."""
    if type not in (MediaType.movie, MediaType.episode):
        raise HTTPException(400, "Only movie/episode HLS supported")

    if media_id:
        media_ids = [media_id]
    else:
        media_id_q = await db.execute(
            select(Media.id).where(Media.tmdb_id == tmdb_id, Media.media_type == type)
        )
        media_ids = [r[0] for r in media_id_q.all()]
    if not media_ids:
        raise HTTPException(404, "Not in library")

    hls_cf_filters = [
        Collection.media_id.in_(media_ids),
        Collection.user_id == current_user.id,
        CollectionFile.connection_id == connection_id,
    ]
    if file_id is not None:
        hls_cf_filters.append(CollectionFile.id == file_id)
    cf_q = await db.execute(
        select(CollectionFile, MediaServerConnection)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .outerjoin(MediaServerConnection, MediaServerConnection.id == CollectionFile.connection_id)
        .where(*hls_cf_filters)
    )
    row = cf_q.first()
    if not row:
        raise HTTPException(404, "Source not found")

    cf, conn = row
    if not conn or not cf.source_id:
        raise HTTPException(400, "No valid connection configured")
    if cf.source.value not in ("jellyfin", "emby"):
        raise HTTPException(400, "HLS streaming is only supported for Jellyfin/Emby sources")

    manifest_path = f"/Videos/{cf.source_id}/master.m3u8"
    manifest_url = f"{conn.url.rstrip('/')}{manifest_path}"

    # Generate a PlaySessionId so Emby/Jellyfin can track the transcoding session.
    # Emby requires POST /PlaybackInfo with this ID to initialise the session before
    # the first segment is requested; without it Emby throws "Value cannot be null (key)".
    device_id = f"scrob-{current_user.id}"
    play_session_id = uuid.uuid4().hex
    tag = ""
    media_source_id = cf.source_id
    try:
        info_url = f"{conn.url.rstrip('/')}/Items/{cf.source_id}/PlaybackInfo"
        common_headers = {"Authorization": f'MediaBrowser Token="{conn.token}"', "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=10) as info_client:
            if conn.type == "emby":
                # Emby requires POST to initialise the transcoding session and bind PlaySessionId
                info_body: dict = {
                    "DeviceId": device_id,
                    "PlaySessionId": play_session_id,
                    "MaxStreamingBitrate": 140_000_000,
                }
                if conn.server_user_id:
                    info_body["UserId"] = conn.server_user_id
                info_res = await info_client.post(info_url, json=info_body, headers=common_headers)
            else:
                # Jellyfin accepts GET; POST also works but GET is simpler
                info_params: dict = {}
                if conn.server_user_id:
                    info_params["UserId"] = conn.server_user_id
                info_res = await info_client.get(info_url, params=info_params, headers=common_headers)
        info_data = info_res.json()
        # Server may echo back or generate a session ID — use whichever is set
        play_session_id = info_data.get("PlaySessionId") or play_session_id
        media_sources = info_data.get("MediaSources", [])
        source_meta = next((s for s in media_sources if s.get("Id") == cf.source_id), None)
        if source_meta is None and media_sources:
            source_meta = media_sources[0]
        if source_meta:
            tag = source_meta.get("ETag", "")
            media_source_id = source_meta.get("Id", cf.source_id)
    except Exception:
        pass  # proceed without Tag; non-Emby servers may not require it

    params: dict[str, str] = {
        "VideoCodec": "copy",
        "AudioCodec": audio_codecs or "aac,mp3,ac3,eac3",
        "MediaSourceId": media_source_id,
        "DeviceId": device_id,
        "PlaySessionId": play_session_id,
        "api_key": conn.token,
    }
    if audio_stream_index is not None:
        params["AudioStreamIndex"] = str(audio_stream_index)
    if tag:
        params["Tag"] = tag
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(
                manifest_url,
                params=params,
                headers={"Authorization": f'MediaBrowser Token="{conn.token}"'},
            )
        if res.status_code != 200:
            logger.warning("jellyfin/emby hls manifest %s: %s", res.status_code, res.text[:300])
            raise HTTPException(502, f"Media server returned {res.status_code} for HLS manifest — body: {res.text[:300]}")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("jellyfin/emby hls manifest exception: %s | url=%s", e, manifest_url)
        raise HTTPException(502, f"Could not fetch HLS manifest: {e}")

    rewritten = _rewrite_m3u8_urls(res.text, connection_id, manifest_path)
    return Response(
        content=rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/plex-hls/{type}/{tmdb_id}")
async def plex_hls_manifest(
    type: MediaType,
    tmdb_id: int,
    connection_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    media_id: int | None = Query(None),
    file_id: int | None = Query(None),
    video_codecs: str | None = Query(None),
    audio_codecs: str | None = Query(None),
    session_id: str | None = Query(None),
):
    """Start a Plex universal transcoding session and return a rewritten HLS manifest."""
    if type not in (MediaType.movie, MediaType.episode):
        raise HTTPException(400, "Only movie/episode HLS supported")

    if media_id:
        media_ids = [media_id]
    else:
        media_id_q = await db.execute(
            select(Media.id).where(Media.tmdb_id == tmdb_id, Media.media_type == type)
        )
        media_ids = [r[0] for r in media_id_q.all()]
    if not media_ids:
        raise HTTPException(404, "Not in library")

    plex_hls_filters = [
        Collection.media_id.in_(media_ids),
        Collection.user_id == current_user.id,
        CollectionFile.connection_id == connection_id,
    ]
    if file_id is not None:
        plex_hls_filters.append(CollectionFile.id == file_id)
    cf_q = await db.execute(
        select(CollectionFile, MediaServerConnection)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .outerjoin(MediaServerConnection, MediaServerConnection.id == CollectionFile.connection_id)
        .where(*plex_hls_filters)
    )
    row = cf_q.first()
    if not row:
        raise HTTPException(404, "Source not found")

    cf, conn = row
    if not conn or not cf.source_id:
        raise HTTPException(400, "No valid connection configured")
    if cf.source.value != "plex":
        raise HTTPException(400, "This endpoint is only for Plex sources")

    session_id = session_id or str(uuid.uuid4())
    manifest_path = "/video/:/transcode/universal/start.m3u8"
    manifest_url = f"{conn.url.rstrip('/')}{manifest_path}"

    _video_codecs = video_codecs or "h264,hevc,av1,vp9"
    _audio_codecs = audio_codecs or "aac,mp3,ac3,eac3"

    # Plex subtitles transcode strictly requires single codecs
    _video_codec = _video_codecs.split(",")[0]
    _audio_codec = _audio_codecs.split(",")[0]

    plex_headers = {
        "X-Plex-Token": conn.token,
        "X-Plex-Client-Identifier": session_id,
        "X-Plex-Product": "Scrob",
        "X-Plex-Version": "1.0.0",
        "X-Plex-Platform": "Chrome",
        "X-Plex-Platform-Version": "120.0",
        "X-Plex-Device": "Browser",
        "X-Plex-Device-Name": "Scrob",
    }
    params: dict[str, str] = {
        "path": f"/library/metadata/{cf.source_id}",
        "mediaIndex": "0",
        "partIndex": "0",
        "protocol": "hls",
        "fastSeek": "1",
        "directPlay": "0",
        "directStream": "1",
        "videoCodec": _video_codec,
        "audioCodec": _audio_codec,
        "maxVideoBitrate": "40000",
        "videoResolution": "3840x2160",
        "videoQuality": "100",
        "copyts": "1",
        "offset": "0",
        "subtitles": "auto",
        "subtitleIndex": "-1",
        "session": session_id,
        **plex_headers,
    }

    debug_req = httpx.Request("GET", manifest_url, params=params, headers=plex_headers)
    logger.info("plex-hls request: %s", debug_req.url)

    res = None
    last_error: str = "Unknown error"
    decision_url = f"{conn.url.rstrip('/')}/video/:/transcode/universal/decision"
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                # Pre-register transcode decision to ensure Plex accepts the subsequent start.m3u8 request
                # without returning a "lacking decision" 400 Bad Request error.
                dec_res = await client.get(decision_url, params=params, headers=plex_headers)
                if dec_res.status_code != 200:
                    logger.warning("plex-hls decision attempt %d/3 returned %d: %s", attempt + 1, dec_res.status_code, dec_res.text[:200])

                res = await client.get(manifest_url, params=params, headers=plex_headers)
            if res.status_code == 200:
                break
            last_error = f"Plex returned {res.status_code} — body: {res.text[:500]}"
            logger.warning("plex-hls attempt %d/3: %s", attempt + 1, last_error)
            res = None
        except Exception as e:
            last_error = str(e)
            logger.warning("plex-hls attempt %d/3 exception: %s", attempt + 1, last_error)

        if attempt < 2:
            await asyncio.sleep(1)
    if res is None:
        raise HTTPException(502, f"Could not fetch Plex HLS manifest: {last_error}")

    # Use the final URL after any redirects as the base for URL rewriting
    final_path = str(res.url.path)
    rewritten = _rewrite_m3u8_urls(res.text, connection_id, final_path)
    return Response(
        content=rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/hls-segment")
async def hls_segment_proxy(
    connection_id: int,
    path: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Proxy HLS sub-manifests and TS segments from Emby/Jellyfin/Plex.
    For M3U8 responses, rewrites embedded URLs before returning."""
    # Use a short-lived session just for the connection lookup, then close it
    # before streaming starts so the DB connection is not held open during the
    # potentially long-lived streaming response.
    async with AsyncSessionLocal() as db:
        conn = await _get_conn_for_user(connection_id, current_user.id, db)
        conn_url = conn.url
        conn_token = conn.token
        conn_type = conn.type

    # path is the decoded absolute path (+ query string) on the media server.
    # Emby/Jellyfin HLS sub-playlists often use relative segment paths with no auth
    # query params. Always inject api_key so the session lookup on the server succeeds.
    segment_url = f"{conn_url.rstrip('/')}{path}"
    if conn_type in ("jellyfin", "emby") and "api_key=" not in path:
        sep = "&" if "?" in path else "?"
        segment_url += f"{sep}api_key={conn_token}"
    session_id = None
    if "/session/" in path:
        parts = path.split("/session/")
        if len(parts) > 1:
            session_id = parts[1].split("/")[0]

    if conn_type == "plex":
        seg_headers = {
            "X-Plex-Token": conn_token,
            "X-Plex-Product": "Scrob",
            "X-Plex-Version": "1.0.0",
            "X-Plex-Platform": "Chrome",
            "X-Plex-Platform-Version": "120.0",
            "X-Plex-Device": "Browser",
            "X-Plex-Device-Name": "Scrob",
        }
        if session_id:
            seg_headers["X-Plex-Client-Identifier"] = session_id
    else:
        seg_headers = {"Authorization": f'MediaBrowser Token="{conn_token}"'}

    try:
        client = httpx.AsyncClient(timeout=httpx.Timeout(None))
        upstream_req = client.build_request("GET", segment_url, headers=seg_headers)
        upstream_res = await client.send(upstream_req, stream=True)
    except Exception as e:
        raise HTTPException(502, f"Could not fetch HLS segment: {e}")

    content_type = upstream_res.headers.get("content-type", "")
    is_manifest = "mpegurl" in content_type or path.split("?")[0].endswith(".m3u8")

    if is_manifest:
        # Read the full body, rewrite URLs, return as text
        body = await upstream_res.aread()
        await upstream_res.aclose()
        await client.aclose()
        # Strip query string for path resolution, but pass it as inherit_qs so that
        # bare relative segment paths (e.g. "0.ts") get DeviceId/PlaySessionId/api_key appended.
        base_path = path.split("?")[0]
        inherit_qs = path.split("?", 1)[1] if "?" in path else ""
        rewritten = _rewrite_m3u8_urls(body.decode("utf-8"), connection_id, base_path, inherit_qs)
        return Response(
            content=rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )

    # Binary segment (TS/fMP4) — stream through.
    # Note: We omit "Content-Length" to prevent Starlette/Uvicorn RuntimeError:
    # "Response content longer/shorter than Content-Length" when streaming.
    res_headers: dict[str, str] = {}
    for h in ("Content-Type",):
        v = upstream_res.headers.get(h.lower())
        if v:
            res_headers[h] = v

    async def cleanup() -> None:
        await upstream_res.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream_res.aiter_bytes(65536),
        status_code=upstream_res.status_code,
        headers=res_headers,
        background=BackgroundTask(cleanup),
    )


@router.post("/session/report/{type}/{tmdb_id}")
async def report_session(
    type: MediaType,
    tmdb_id: int,
    body: SessionReportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    media_id: int | None = Query(None),
):
    """Report playback state to the media server so the session appears in its Now Playing dashboard."""
    if type not in (MediaType.movie, MediaType.episode):
        return {"ok": False}

    if media_id:
        media_ids = [media_id]
    else:
        media_id_q = await db.execute(
            select(Media.id).where(Media.tmdb_id == tmdb_id, Media.media_type == type)
        )
        media_ids = [r[0] for r in media_id_q.all()]
    if not media_ids:
        return {"ok": False}

    session_cf_filters = [
        Collection.media_id.in_(media_ids),
        Collection.user_id == current_user.id,
        CollectionFile.connection_id == body.connection_id,
    ]
    if body.file_id is not None:
        session_cf_filters.append(CollectionFile.id == body.file_id)
    cf_q = await db.execute(
        select(CollectionFile, MediaServerConnection)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .outerjoin(MediaServerConnection, MediaServerConnection.id == CollectionFile.connection_id)
        .where(*session_cf_filters)
    )
    row = cf_q.first()
    if not row:
        return {"ok": False}

    cf, conn = row
    if not conn or not cf.source_id:
        return {"ok": False}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            if cf.source.value in ("jellyfin", "emby"):
                pos_ticks = body.position_ms * 10_000  # Jellyfin uses 100-nanosecond ticks
                # Authorization with full MediaBrowser scheme is required for
                # Jellyfin/Emby to associate the playback report with a client
                # session and show it in "Now Playing".
                device_id = f"scrob-{current_user.id}"
                headers = {
                    "Authorization": (
                        f'MediaBrowser Token="{conn.token}", Device="Scrob",'
                        f' DeviceId="{device_id}", Version="1.0.0"'
                    ),
                    "Content-Type": "application/json",
                }
                base = conn.url.rstrip("/")
                if body.state == "playing":
                    await client.post(
                        f"{base}/Sessions/Playing",
                        json={
                            "ItemId": cf.source_id,
                            "PositionTicks": pos_ticks,
                            "CanSeek": True,
                            "IsPaused": False,
                            "IsMuted": False,
                            "PlayMethod": "DirectPlay",
                        },
                        headers=headers,
                    )
                elif body.state in ("progress", "paused"):
                    await client.post(
                        f"{base}/Sessions/Playing/Progress",
                        json={
                            "ItemId": cf.source_id,
                            "PositionTicks": pos_ticks,
                            "IsPaused": body.state == "paused",
                            "IsMuted": False,
                        },
                        headers=headers,
                    )
                elif body.state == "stopped":
                    await client.post(
                        f"{base}/Sessions/Playing/Stopped",
                        json={"ItemId": cf.source_id, "PositionTicks": pos_ticks},
                        headers=headers,
                    )
            elif cf.source.value == "plex":
                plex_state = "stopped" if body.state == "stopped" else ("paused" if body.state == "paused" else "playing")
                # Ping the Universal Transcoder session to prevent it expiring while HLS.js
                # buffers ahead and stops requesting segments (typically after ~60 s idle).
                if body.plex_session_id:
                    try:
                        await client.get(
                            f"{conn.url.rstrip('/')}/video/:/transcode/universal/ping",
                            params={"session": body.plex_session_id},
                            headers={"X-Plex-Token": conn.token},
                        )
                    except Exception:
                        pass
                await client.get(
                    f"{conn.url.rstrip('/')}/:/timeline",
                    params={
                        "ratingKey": cf.source_id,
                        "key": f"/library/metadata/{cf.source_id}",
                        "state": plex_state,
                        "time": str(body.position_ms),
                        "duration": str(body.duration_ms),
                        "identifier": "tv.plex.providers.library",
                        "X-Plex-Token": conn.token,
                    },
                    headers={"Accept": "application/json"},
                )
    except Exception:
        pass  # Best-effort; non-critical

    return {"ok": True}
