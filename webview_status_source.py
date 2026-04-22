import base64
import concurrent.futures
import hashlib
import http.client
import json
import mimetypes
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass
from io import BytesIO
from datetime import datetime
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from PIL import Image, ImageDraw, ImageFont

from config import (
    STATUS_MEDIA_CACHE_DIR,
    get_status_source_config,
    get_supported_web_browsers,
    get_web_profiles,
)
from live_text_hydration import merge_cached_text_hydration

try:
    from ccl_chromium_reader.ccl_chromium_indexeddb import (
        DatabaseMetadataType,
        IdbKey,
        IndexedDb,
        ObjectStoreMetadataType,
        _le_varint_from_bytes,
    )
    from ccl_chromium_reader.serialization_formats import (
        ccl_blink_value_deserializer,
        ccl_v8_value_deserializer,
    )

    HAS_INDEXEDDB_MESSAGE_PARSER = True
except ImportError:
    HAS_INDEXEDDB_MESSAGE_PARSER = False


STATUS_URL_PATTERN = rb'deprecatedMms3Url"[\x00-\xff]{0,4}(https://mmg\.whatsapp\.net[^"]+)"'
DIRECT_PATH_PATTERN = rb'directPath"[\x00-\xff]{0,4}((?:\.\.)?/[^"]+)"'
MIME_PATTERN = rb'mimetype"[\x00-\xff]{0,4}((?:image|video)/[^"]+)"'
FILEHASH_PATTERN = rb'filehash",([A-Za-z0-9+/=]+)"'
ENC_FILEHASH_PATTERN = rb'encFilehash",([A-Za-z0-9+/=]+)"'
MEDIA_KEY_PATTERN = rb'mediaKey",([A-Za-z0-9+/=]+)"'
STATUS_MARKER = b"status@broadcast"
WINDOW_BYTES_BEFORE = 1800
WINDOW_BYTES_AFTER = 2200

STATUS_KEY_NEEDLE_UTF16 = STATUS_MARKER.decode("ascii").encode("utf-16-be")
MESSAGE_DATABASE_NAME = "model-storage"
MESSAGE_OBJECT_STORE_NAME = "message"
INDEX_CACHE_MAX_AGE_SECONDS = 15 * 60
INDEX_CACHE_SCHEMA_VERSION = 3
TEXT_ASSET_SCHEMA_VERSION = 4
MAX_CACHE_WORKERS = 6
FIREFOX_REQUIRED_TABLES = {"database", "object_store", "object_data"}
FIREFOX_STATUS_ID_PATTERN = re.compile(
    rb"(status@broadcast_[A-Za-z0-9:_-]+@[A-Za-z]+)"
)
FIREFOX_TYPE_PATTERN = re.compile(rb"type[\x00-\xff]{0,16}(imag|video|chat)")
FIREFOX_FILEHASH_PATTERN = re.compile(
    rb"filehash[\x00-\xff]{0,16}([A-Za-z0-9+/=]{20,})"
)
BASE64_TOKEN_PATTERN = re.compile(rb"[A-Za-z0-9+/=]{20,}")
URL_SAFE_BYTES = (
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    b"abcdefghijklmnopqrstuvwxyz"
    b"0123456789"
    b"-._~:/?#[]@!$&'()*+,;=%"
)

IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
VIDEO_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}
MEDIA_INFO_BY_KIND = {
    "photos": b"WhatsApp Image Keys",
    "videos": b"WhatsApp Video Keys",
    "texts": b"WhatsApp Image Keys",
}
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://web.whatsapp.com/",
}

_STATUS_RECORD_CACHE: dict[str, tuple[str, list["StatusRecord"]]] = {}


@dataclass(frozen=True)
class StatusRecord:
    status_id: str
    kind: str
    mimetype: str
    url: str
    direct_path: str | None
    filehash: str
    enc_filehash: str | None
    media_key: str | None
    source_file: str
    source_offset: int
    timestamp: float
    author_jid: str | None
    source_key: str
    source_label: str
    source_indexeddb_dir: str
    source_blob_dir: str | None
    text_value: str | None = None
    text_subtype: str | None = None
    background_color: int | None = None
    text_color: int | None = None
    font_id: int | None = None
    thumbnail_direct_path: str | None = None
    thumbnail_filehash: str | None = None
    thumbnail_enc_filehash: str | None = None
    thumbnail_inline: str | None = None
    music_title: str | None = None
    music_artist: str | None = None
    music_artwork_direct_path: str | None = None
    music_artwork_filehash: str | None = None
    music_artwork_enc_filehash: str | None = None
    music_artwork_media_key: str | None = None
    music_track_duration_ms: int | None = None


def has_webview_status_source(
    source_mode: str = "desktop",
    selected_web_browser: str = "chrome",
    selected_web_profile: str | None = None,
) -> bool:
    return any(
        os.path.isdir(source_config["indexeddb_dir"])
        for source_config in _iter_source_configs(
            source_mode,
            selected_web_browser,
            selected_web_profile,
        )
    )


def get_webview_status_files(
    file_type: str,
    page: int = 1,
    items_per_page: int | None = None,
    source_mode: str = "desktop",
    selected_web_browser: str = "chrome",
    selected_web_profile: str | None = None,
) -> list[str]:
    if file_type not in {"photos", "videos", "texts"}:
        return []

    records = get_webview_status_records(
        file_type,
        page=page,
        items_per_page=items_per_page,
        source_mode=source_mode,
        selected_web_browser=selected_web_browser,
        selected_web_profile=selected_web_profile,
    )
    if not records:
        return []

    unique_records_by_cache_path: dict[str, StatusRecord] = {}
    for record in records:
        unique_records_by_cache_path.setdefault(_cache_path_for_record(record), record)

    resolved_paths: dict[str, str | None] = {}
    workers = min(MAX_CACHE_WORKERS, len(unique_records_by_cache_path))
    if workers <= 1:
        for cache_path, record in unique_records_by_cache_path.items():
            resolved_paths[cache_path] = ensure_record_cached(record)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(ensure_record_cached, record): cache_path
                for cache_path, record in unique_records_by_cache_path.items()
            }
            for future in concurrent.futures.as_completed(future_map):
                cache_path = future_map[future]
                try:
                    resolved_paths[cache_path] = future.result()
                except Exception:
                    resolved_paths[cache_path] = None

    accessible_files: list[str] = []
    for record in records:
        cache_path = resolved_paths.get(_cache_path_for_record(record))
        if cache_path:
            accessible_files.append(cache_path)
    return accessible_files


def get_webview_status_records(
    file_type: str,
    page: int = 1,
    items_per_page: int | None = None,
    source_mode: str = "desktop",
    selected_web_browser: str = "chrome",
    selected_web_profile: str | None = None,
) -> list[StatusRecord]:
    if file_type not in {"photos", "videos", "texts"}:
        return []

    records = [
        record
        for record in _load_all_status_records(
            source_mode,
            selected_web_browser,
            selected_web_profile,
        )
        if record.kind == file_type
    ]
    if items_per_page is None or items_per_page <= 0:
        return merge_cached_text_hydration(records) if file_type == "texts" else records

    start = max(0, (page - 1) * items_per_page)
    stop = start + items_per_page
    paged_records = records[start:stop]
    return merge_cached_text_hydration(paged_records) if file_type == "texts" else paged_records


def iter_status_records(
    file_type: str,
    source_mode: str = "desktop",
    selected_web_browser: str = "chrome",
    selected_web_profile: str | None = None,
) -> Iterable[StatusRecord]:
    for record in _load_all_status_records(
        source_mode,
        selected_web_browser,
        selected_web_profile,
    ):
        if record.kind == file_type:
            yield record


def ensure_record_cached(record: StatusRecord) -> str | None:
    cache_path = _cache_path_for_record(record)
    if os.path.exists(cache_path):
        return cache_path

    if record.kind == "texts":
        return _generate_text_status_asset(record, cache_path)

    payload = _download_plaintext_payload(record)
    if not payload:
        return None

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=os.path.dirname(cache_path),
            suffix=".tmp",
        ) as temp_handle:
            temp_handle.write(payload)
            temp_file = temp_handle.name
        os.replace(temp_file, cache_path)
        return cache_path
    finally:
        if temp_file and os.path.exists(temp_file):
            os.unlink(temp_file)


def get_cached_record_path(record: StatusRecord) -> str | None:
    cache_path = _cache_path_for_record(record)
    return cache_path if os.path.exists(cache_path) else None


def _load_all_status_records(
    source_mode: str = "desktop",
    selected_web_browser: str = "chrome",
    selected_web_profile: str | None = None,
) -> list[StatusRecord]:
    source_configs = _iter_source_configs(
        source_mode,
        selected_web_browser,
        selected_web_profile,
    )

    if source_mode == "all":
        combined_records: list[StatusRecord] = []
        for source_config in source_configs:
            if not os.path.isdir(source_config["indexeddb_dir"]):
                continue
            combined_records.extend(_load_records_for_source_config(source_config))
        return _merge_status_records(combined_records)

    if not source_configs:
        return []

    source_config = source_configs[0]
    if not os.path.isdir(source_config["indexeddb_dir"]):
        return []

    return _load_records_for_source_config(source_config)


def _iter_source_configs(
    source_mode: str,
    selected_web_browser: str,
    selected_web_profile: str | None,
) -> list[dict]:
    if source_mode != "all":
        return [
            get_status_source_config(
                source_mode,
                selected_web_browser,
                selected_web_profile,
            )
        ]

    source_configs = [
        get_status_source_config("desktop", selected_web_browser, selected_web_profile)
    ]
    for browser in get_supported_web_browsers():
        for profile in get_web_profiles(browser):
            if not profile.get("available"):
                continue
            source_configs.append(
                get_status_source_config("web", browser, profile["profile_name"])
            )

    unique_configs: list[dict] = []
    seen_keys: set[str] = set()
    for source_config in source_configs:
        source_key = source_config["key"]
        if source_key in seen_keys:
            continue
        seen_keys.add(source_key)
        unique_configs.append(source_config)
    return unique_configs


def _load_records_for_source_config(source_config: dict) -> list[StatusRecord]:
    global _STATUS_RECORD_CACHE

    source_key = source_config["key"]

    snapshot = _build_indexeddb_snapshot(source_config)
    cached_snapshot = _STATUS_RECORD_CACHE.get(source_key)
    if cached_snapshot and cached_snapshot[0] == snapshot:
        return list(cached_snapshot[1])

    cached_records = _load_cached_records(source_key, snapshot)
    if cached_records is not None:
        _STATUS_RECORD_CACHE[source_key] = (snapshot, cached_records)
        return list(cached_records)

    if _is_firefox_source(source_config):
        records = _load_records_from_firefox_message_store(source_config)
        if not records:
            records = _load_records_from_firefox_blob_fallback(source_config)
        _STATUS_RECORD_CACHE[source_key] = (snapshot, records)
        _write_cached_records(source_key, snapshot, records)
        return list(records)

    if HAS_INDEXEDDB_MESSAGE_PARSER:
        records = _load_records_from_message_store(source_config)
        if records:
            _STATUS_RECORD_CACHE[source_key] = (snapshot, records)
            _write_cached_records(source_key, snapshot, records)
            return list(records)

    records = _load_records_from_regex_fallback(source_config)
    _STATUS_RECORD_CACHE[source_key] = (snapshot, records)
    _write_cached_records(source_key, snapshot, records)
    return list(records)


def invalidate_status_source_cache(
    source_mode: str = "desktop",
    selected_web_browser: str = "chrome",
    selected_web_profile: str | None = None,
) -> None:
    source_configs = _iter_source_configs(
        source_mode,
        selected_web_browser,
        selected_web_profile,
    )
    for source_config in source_configs:
        source_key = source_config["key"]
        _STATUS_RECORD_CACHE.pop(source_key, None)
        index_cache_file = _index_cache_file_for_source(source_key)
        if os.path.exists(index_cache_file):
            try:
                os.remove(index_cache_file)
            except OSError:
                pass


def _merge_status_records(records: list[StatusRecord]) -> list[StatusRecord]:
    merged: dict[tuple[str, str], StatusRecord] = {}
    for record in records:
        status_identity = (record.status_id or "").strip()
        dedupe_identity = status_identity or record.filehash
        dedupe_key = (record.kind, dedupe_identity)
        previous = merged.get(dedupe_key)
        if previous is None or _record_sort_key(record) > _record_sort_key(previous):
            merged[dedupe_key] = record

    return sorted(
        merged.values(),
        key=lambda record: (record.timestamp, record.status_id),
        reverse=True,
    )


def _record_sort_key(record: StatusRecord) -> tuple[float, int, int, int]:
    return (
        float(record.timestamp or 0.0),
        1 if record.author_jid else 0,
        1 if record.media_key else 0,
        _source_priority(record.source_key),
    )


def _source_priority(source_key: str) -> int:
    if source_key == "desktop":
        return 4
    if source_key.startswith("firefox-"):
        return 3
    if source_key.startswith("edge-"):
        return 2
    if source_key.startswith("chrome-"):
        return 1
    return 0


def _is_firefox_source(source_config: dict) -> bool:
    return source_config.get("browser") == "firefox"


def _load_records_from_firefox_message_store(source_config: dict) -> list[StatusRecord]:
    indexeddb_dir = source_config["indexeddb_dir"]
    if not os.path.isdir(indexeddb_dir):
        return []

    latest_records: dict[str, StatusRecord] = {}
    for file_name in sorted(os.listdir(indexeddb_dir), reverse=True):
        if not file_name.endswith(".sqlite"):
            continue

        source_path = os.path.join(indexeddb_dir, file_name)
        for source_offset, message_blob in _iter_firefox_message_blobs(source_path):
            record = _build_status_record_from_firefox_message(
                message_blob,
                source_path,
                source_offset,
                source_config,
            )
            if not record:
                continue

            previous = latest_records.get(record.status_id)
            if previous is None or (
                record.timestamp,
                record.source_offset,
                record.source_file,
            ) > (
                previous.timestamp,
                previous.source_offset,
                previous.source_file,
            ):
                latest_records[record.status_id] = record

    return sorted(
        latest_records.values(),
        key=lambda record: (record.timestamp, record.status_id),
        reverse=True,
    )


def _load_records_from_firefox_blob_fallback(source_config: dict) -> list[StatusRecord]:
    indexeddb_dir = source_config["indexeddb_dir"]
    if not os.path.isdir(indexeddb_dir):
        return []

    latest_records: dict[str, StatusRecord] = {}
    for file_name in sorted(os.listdir(indexeddb_dir), reverse=True):
        if not file_name.endswith(".sqlite"):
            continue

        source_path = os.path.join(indexeddb_dir, file_name)
        for source_offset, message_blob in _iter_firefox_raw_blobs(source_path):
            record = _build_status_record_from_firefox_message(
                message_blob,
                source_path,
                source_offset,
                source_config,
            )
            if not record:
                continue

            previous = latest_records.get(record.status_id)
            if previous is None or (
                record.timestamp,
                record.source_offset,
                record.source_file,
            ) > (
                previous.timestamp,
                previous.source_offset,
                previous.source_file,
            ):
                latest_records[record.status_id] = record

    return sorted(
        latest_records.values(),
        key=lambda record: (record.timestamp, record.status_id),
        reverse=True,
    )


def _iter_firefox_message_blobs(source_path: str) -> Iterable[tuple[int, bytes]]:
    try:
        connection = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return []

    try:
        cursor = connection.cursor()
        table_names = {
            row[0]
            for row in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not FIREFOX_REQUIRED_TABLES.issubset(table_names):
            return []

        database_names = {row[0] for row in cursor.execute("SELECT name FROM database")}
        if MESSAGE_DATABASE_NAME not in database_names:
            return []

        object_store_row = cursor.execute(
            "SELECT id FROM object_store WHERE name = ?",
            (MESSAGE_OBJECT_STORE_NAME,),
        ).fetchone()
        if not object_store_row:
            return []

        object_store_id = int(object_store_row[0])
        blobs: list[tuple[int, bytes]] = []
        for source_offset, (_, data) in enumerate(
            cursor.execute(
                "SELECT key, data FROM object_data WHERE object_store_id = ?",
                (object_store_id,),
            )
        ):
            if not isinstance(data, bytes) or STATUS_MARKER not in data:
                continue
            blobs.append((source_offset, data))
        return blobs
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _iter_firefox_raw_blobs(source_path: str) -> Iterable[tuple[int, bytes]]:
    try:
        connection = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return []

    try:
        cursor = connection.cursor()
        table_names = {
            row[0]
            for row in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "object_data" not in table_names:
            return []

        blobs: list[tuple[int, bytes]] = []
        for source_offset, row in enumerate(
            cursor.execute("SELECT data FROM object_data")
        ):
            data = row[0]
            if not isinstance(data, bytes) or STATUS_MARKER not in data:
                continue
            blobs.append((source_offset, data))
        return blobs
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _build_status_record_from_firefox_message(
    message_blob: bytes,
    source_file: str,
    source_offset: int,
    source_config: dict,
) -> StatusRecord | None:
    message_type = _extract_firefox_message_type(message_blob)
    if message_type == "imag":
        kind = "photos"
        mimetype = "image/jpeg"
    elif message_type == "video":
        kind = "videos"
        mimetype = "video/mp4"
    elif message_type == "chat":
        kind = "texts"
        mimetype = "image/png"
    else:
        return None

    direct_path = _extract_firefox_direct_path(message_blob)
    filehash = _extract_firefox_filehash(message_blob)
    if kind != "texts" and (not direct_path or not filehash):
        return None
    if kind == "texts" and not filehash:
        filehash = _fallback_text_filehash(
            _normalize_status_id(_extract_firefox_status_id(message_blob) or None),
            source_config["key"],
            source_offset,
        )

    status_id = _normalize_status_id(_extract_firefox_status_id(message_blob) or filehash)
    normalized_direct_path = _normalize_direct_path(direct_path) if direct_path else None
    url = f"https://mmg.whatsapp.net{normalized_direct_path}" if normalized_direct_path else ""

    music = _extract_embedded_music_from_blob(message_blob)

    return StatusRecord(
        status_id=status_id,
        kind=kind,
        mimetype=mimetype,
        url=url,
        direct_path=normalized_direct_path,
        filehash=filehash,
        enc_filehash=_extract_firefox_enc_filehash(message_blob),
        media_key=_extract_firefox_media_key(message_blob),
        source_file=source_file,
        source_offset=source_offset,
        timestamp=_extract_firefox_timestamp(message_blob),
        author_jid=None,
        source_key=source_config["key"],
        source_label=source_config["label"],
        source_indexeddb_dir=source_config["indexeddb_dir"],
        source_blob_dir=source_config.get("blob_dir"),
        text_value=None,
        text_subtype=_extract_firefox_subtype(message_blob),
        background_color=_extract_firefox_background_color(message_blob),
        text_color=_extract_firefox_text_color(message_blob),
        font_id=_extract_firefox_font_id(message_blob),
        thumbnail_direct_path=None,
        thumbnail_filehash=None,
        thumbnail_enc_filehash=None,
        thumbnail_inline=None,
        music_title=music.get("title"),
        music_artist=music.get("artist"),
        music_artwork_direct_path=music.get("artwork_direct_path"),
        music_artwork_filehash=music.get("artwork_filehash"),
        music_artwork_enc_filehash=music.get("artwork_enc_filehash"),
        music_artwork_media_key=music.get("artwork_media_key"),
        music_track_duration_ms=music.get("duration_ms"),
    )


def _load_records_from_message_store(source_config: dict) -> list[StatusRecord]:
    indexeddb_dir = source_config["indexeddb_dir"]
    source_blob_dir = source_config.get("blob_dir")
    blob_dir = source_blob_dir if source_blob_dir and os.path.isdir(source_blob_dir) else None
    indexed_db = IndexedDb(indexeddb_dir, blob_dir)
    try:
        database_id = _get_database_id(indexed_db, MESSAGE_DATABASE_NAME)
        object_store_id = _get_object_store_id(indexed_db, database_id, MESSAGE_OBJECT_STORE_NAME)
        prefix = IndexedDb.make_prefix(database_id, object_store_id, 1)
        blink_deserializer = ccl_blink_value_deserializer.BlinkV8Deserializer()
        latest_messages: dict[str, tuple[int, str, dict]] = {}

        for raw_record in indexed_db._fetched_records:
            if not raw_record.key.startswith(prefix):
                continue
            if STATUS_KEY_NEEDLE_UTF16 not in raw_record.key:
                continue

            message = _deserialize_message_record(
                indexed_db,
                raw_record,
                prefix,
                database_id,
                object_store_id,
                blink_deserializer,
            )
            if not isinstance(message, dict):
                continue

            status_id = _extract_status_id(message)
            if not status_id:
                continue

            previous = latest_messages.get(status_id)
            if previous is None or raw_record.seq > previous[0]:
                latest_messages[status_id] = (
                    raw_record.seq,
                    str(raw_record.origin_file),
                    message,
                )

        records: list[StatusRecord] = []
        for sequence_number, origin_file, message in latest_messages.values():
            record = _build_status_record_from_message(
                message,
                origin_file,
                sequence_number,
                source_config,
            )
            if record:
                records.append(record)

        records.sort(key=lambda record: (record.timestamp, record.status_id), reverse=True)
        return records
    finally:
        indexed_db.close()


def _deserialize_message_record(
    indexed_db: IndexedDb,
    raw_record,
    prefix: bytes,
    database_id: int,
    object_store_id: int,
    blink_deserializer,
) -> dict | None:
    if not raw_record.value:
        return None

    try:
        key = IdbKey(raw_record.key[len(prefix):])
        version_info = _le_varint_from_bytes(raw_record.value)
        if version_info is None:
            return None
        _, version_bytes = version_info
        precursor = indexed_db.read_record_precursor(
            key,
            database_id,
            object_store_id,
            raw_record.value[len(version_bytes):],
            None,
        )
        if precursor is None:
            return None

        _, object_stream, _, _ = precursor
        deserializer = ccl_v8_value_deserializer.Deserializer(
            object_stream,
            host_object_delegate=blink_deserializer.read,
        )
        value = deserializer.read()
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _build_status_record_from_message(
    message: dict,
    source_file: str,
    source_offset: int,
    source_config: dict,
) -> StatusRecord | None:
    message_type = _as_string(message.get("type"))
    if message_type == "image":
        kind = "photos"
    elif message_type == "video":
        kind = "videos"
    elif message_type == "chat":
        kind = "texts"
    else:
        return None

    mimetype = _as_string(message.get("mimetype"))
    filehash = _as_string(message.get("filehash"))
    direct_path = _as_string(message.get("directPath"))
    url = _normalize_extracted_url(_as_string(message.get("deprecatedMms3Url")))

    if not url and direct_path:
        url = f"https://mmg.whatsapp.net{_normalize_direct_path(direct_path)}"

    if kind == "texts":
        mimetype = mimetype or "image/png"
        filehash = filehash or _fallback_text_filehash(
            _normalize_status_id(_extract_status_id(message) or None),
            source_config["key"],
            source_offset,
        )
    elif not mimetype or not filehash or not url:
        return None

    music = _extract_embedded_music(message)

    return StatusRecord(
        status_id=_normalize_status_id(_extract_status_id(message) or filehash),
        kind=kind,
        mimetype=mimetype,
        url=url,
        direct_path=direct_path,
        filehash=filehash,
        enc_filehash=_as_string(message.get("encFilehash")),
        media_key=_as_string(message.get("mediaKey")),
        source_file=source_file,
        source_offset=source_offset,
        timestamp=float(message.get("t") or 0.0),
        author_jid=_serialized_jid(message.get("author")),
        source_key=source_config["key"],
        source_label=source_config["label"],
        source_indexeddb_dir=source_config["indexeddb_dir"],
        source_blob_dir=source_config.get("blob_dir"),
        text_value=_extract_text_value(message),
        text_subtype=_as_string(message.get("subtype")),
        background_color=_coerce_int(message.get("backgroundColor")),
        text_color=_coerce_int(message.get("textColor")),
        font_id=_coerce_int(message.get("font")),
        thumbnail_direct_path=_as_string(message.get("thumbnailDirectPath")),
        thumbnail_filehash=_bytes_to_base64(message.get("thumbnailSha256")),
        thumbnail_enc_filehash=_bytes_to_base64(message.get("thumbnailEncSha256")),
        thumbnail_inline=_as_string(message.get("thumbnail")),
        music_title=music.get("title"),
        music_artist=music.get("artist"),
        music_artwork_direct_path=music.get("artwork_direct_path"),
        music_artwork_filehash=music.get("artwork_filehash"),
        music_artwork_enc_filehash=music.get("artwork_enc_filehash"),
        music_artwork_media_key=music.get("artwork_media_key"),
        music_track_duration_ms=music.get("duration_ms"),
    )


def _extract_status_id(message: dict) -> str | None:
    status_id = _as_string(message.get("id"))
    internal_id = _as_string(message.get("internalId"))
    from_value = _serialized_jid(message.get("from"))

    if status_id and STATUS_MARKER.decode("ascii") in status_id:
        return status_id
    if internal_id and STATUS_MARKER.decode("ascii") in internal_id:
        return status_id or internal_id
    if from_value == STATUS_MARKER.decode("ascii"):
        return status_id or internal_id
    return None


def _normalize_status_id(status_id: str | None) -> str | None:
    if not status_id:
        return status_id

    normalized = status_id
    if normalized.startswith("false_"):
        normalized = normalized[len("false_"):]
    elif normalized.startswith("true_"):
        normalized = normalized[len("true_"):]

    if normalized.endswith("@li"):
        normalized = f"{normalized}d"
    return normalized


def _get_database_id(indexed_db: IndexedDb, database_name: str) -> int:
    for database_id in indexed_db.global_metadata.db_ids:
        if database_id.name == database_name:
            return database_id.dbid_no
    raise KeyError(f"Could not find IndexedDB database: {database_name}")


def _get_object_store_id(indexed_db: IndexedDb, database_id: int, object_store_name: str) -> int:
    maximum_store_id = indexed_db.get_database_metadata(
        database_id,
        DatabaseMetadataType.MaximumObjectStoreId,
    ) or 0
    for object_store_id in range(1, maximum_store_id + 1):
        name = indexed_db.get_object_store_metadata(
            database_id,
            object_store_id,
            ObjectStoreMetadataType.StoreName,
        )
        if name == object_store_name:
            return object_store_id
    raise KeyError(
        f"Could not find object store '{object_store_name}' in database id {database_id}"
    )


def _build_indexeddb_snapshot(source_config: dict) -> str:
    snapshot_parts: list[str] = []
    for root_dir in [source_config["indexeddb_dir"], source_config.get("blob_dir")]:
        if not root_dir or not os.path.exists(root_dir):
            continue

        for current_root, _, files in os.walk(root_dir):
            for file_name in sorted(files):
                file_path = os.path.join(current_root, file_name)
                try:
                    stat_result = os.stat(file_path)
                except OSError:
                    continue
                rel_path = os.path.relpath(file_path, root_dir)
                snapshot_parts.append(
                    f"{root_dir}|{rel_path}|{stat_result.st_size}|{stat_result.st_mtime_ns}"
                )

    digest = hashlib.sha256()
    for part in snapshot_parts:
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def _index_cache_file_for_source(source_key: str) -> str:
    safe_source_key = "".join(
        char if char.isalnum() or char in {"-", "_"} else "-"
        for char in source_key
    )
    return os.path.join(
        STATUS_MEDIA_CACHE_DIR,
        f"_status_index_cache_{safe_source_key}.json",
    )


def _load_cached_records(source_key: str, snapshot: str) -> list[StatusRecord] | None:
    index_cache_file = _index_cache_file_for_source(source_key)
    if not os.path.exists(index_cache_file):
        return None

    try:
        with open(index_cache_file, "r", encoding="utf-8") as cache_file:
            payload = json.load(cache_file)
    except (OSError, ValueError, TypeError):
        return None

    generated_at = payload.get("generated_at")
    is_recent = isinstance(generated_at, (int, float)) and (
        time.time() - float(generated_at) <= INDEX_CACHE_MAX_AGE_SECONDS
    )

    if payload.get("snapshot") != snapshot and not is_recent:
        return None
    if payload.get("schema_version") != INDEX_CACHE_SCHEMA_VERSION:
        return None

    records = payload.get("records")
    if not isinstance(records, list):
        return None

    try:
        return [StatusRecord(**record) for record in records]
    except (TypeError, ValueError):
        return None


def _write_cached_records(source_key: str, snapshot: str, records: list[StatusRecord]) -> None:
    index_cache_file = _index_cache_file_for_source(source_key)
    payload = {
        "schema_version": INDEX_CACHE_SCHEMA_VERSION,
        "generated_at": time.time(),
        "snapshot": snapshot,
        "records": [asdict(record) for record in records],
    }

    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=os.path.dirname(index_cache_file),
            suffix=".tmp",
            encoding="utf-8",
        ) as temp_handle:
            json.dump(payload, temp_handle)
            temp_file = temp_handle.name
        os.replace(temp_file, index_cache_file)
    except OSError:
        if temp_file and os.path.exists(temp_file):
            os.unlink(temp_file)


def _load_records_from_regex_fallback(source_config: dict) -> list[StatusRecord]:
    status_url_pattern = re.compile(STATUS_URL_PATTERN)
    direct_path_pattern = re.compile(DIRECT_PATH_PATTERN)
    mime_pattern = re.compile(MIME_PATTERN)
    filehash_pattern = re.compile(FILEHASH_PATTERN)
    enc_filehash_pattern = re.compile(ENC_FILEHASH_PATTERN)
    media_key_pattern = re.compile(MEDIA_KEY_PATTERN)

    records: list[StatusRecord] = []
    dedupe_keys: set[str] = set()

    indexeddb_dir = source_config["indexeddb_dir"]
    if not os.path.isdir(indexeddb_dir):
        return records

    for file_name in sorted(os.listdir(indexeddb_dir), reverse=True):
        if not file_name.endswith((".ldb", ".log")):
            continue

        source_path = os.path.join(indexeddb_dir, file_name)
        try:
            with open(source_path, "rb") as file_handle:
                blob = file_handle.read()
        except OSError:
            continue

        matches = list(status_url_pattern.finditer(blob))
        for match in reversed(matches):
            start = max(0, match.start() - WINDOW_BYTES_BEFORE)
            end = min(len(blob), match.end() + WINDOW_BYTES_AFTER)
            window = blob[start:end]
            if STATUS_MARKER not in window:
                continue

            mimetype = _extract_group(mime_pattern, window)
            filehash = _extract_group(filehash_pattern, window)
            if not mimetype or not filehash:
                continue

            kind = _kind_from_mimetype(mimetype)
            if kind not in {"photos", "videos"}:
                continue

            direct_path = _extract_group(direct_path_pattern, window)
            url = _normalize_extracted_url(match.group(1).decode("utf-8", "ignore"))
            if not url and direct_path:
                url = f"https://mmg.whatsapp.net{_normalize_direct_path(direct_path)}"
            if not url:
                continue

            dedupe_key = filehash or url
            if dedupe_key in dedupe_keys:
                continue
            dedupe_keys.add(dedupe_key)

            records.append(
                StatusRecord(
                    status_id=filehash,
                    kind=kind,
                    mimetype=mimetype,
                    url=url,
                    direct_path=direct_path,
                    filehash=filehash,
                    enc_filehash=_extract_group(enc_filehash_pattern, window),
                    media_key=_extract_group(media_key_pattern, window),
                    source_file=source_path,
                    source_offset=match.start(),
                    timestamp=0.0,
                    author_jid=None,
                    source_key=source_config["key"],
                    source_label=source_config["label"],
                    source_indexeddb_dir=source_config["indexeddb_dir"],
                    source_blob_dir=source_config.get("blob_dir"),
                )
            )

    return records


def _extract_firefox_message_type(message_blob: bytes) -> str | None:
    match = FIREFOX_TYPE_PATTERN.search(message_blob)
    if not match:
        return None
    return match.group(1).decode("utf-8", "ignore")


def _extract_firefox_status_id(message_blob: bytes) -> str | None:
    match = FIREFOX_STATUS_ID_PATTERN.search(message_blob)
    if not match:
        return None
    return match.group(1).decode("utf-8", "ignore")


def _extract_firefox_direct_path(message_blob: bytes) -> str | None:
    marker_index = message_blob.find(b"directPath")
    if marker_index < 0:
        return None

    path_index = message_blob.find(b"/", marker_index)
    if path_index < 0 or path_index - marker_index > 48:
        return None

    extracted = _extract_ascii_run(message_blob, path_index, URL_SAFE_BYTES)
    return extracted if extracted and extracted.startswith("/") else None


def _extract_firefox_filehash(message_blob: bytes) -> str | None:
    return _extract_base64_after_marker(message_blob, b"filehash")


def _extract_firefox_media_key(message_blob: bytes) -> str | None:
    media_key = _extract_base64_after_marker(message_blob, b"mediaKey")
    if media_key:
        return media_key

    filehash_match = FIREFOX_FILEHASH_PATTERN.search(message_blob)
    if not filehash_match:
        return None

    search_start = filehash_match.start()
    search_end = min(len(message_blob), search_start + 900)
    key_index = message_blob.find(b"Key", search_start, search_end)
    if key_index < 0:
        return None
    return _extract_base64_after_marker(message_blob[key_index:], b"Key")


def _extract_firefox_enc_filehash(message_blob: bytes) -> str | None:
    return _extract_base64_after_marker(message_blob, b"encF")


def _extract_firefox_timestamp(message_blob: bytes) -> float:
    marker_index = message_blob.find(b".8\x00\x08fro")
    if marker_index < 4:
        return 0.0

    timestamp = int.from_bytes(
        message_blob[marker_index - 4:marker_index],
        byteorder="little",
        signed=False,
    )
    if 1_500_000_000 <= timestamp <= 2_200_000_000:
        return float(timestamp)
    return 0.0


def _extract_base64_after_marker(
    message_blob: bytes,
    marker: bytes,
    lookahead: int = 160,
) -> str | None:
    marker_index = message_blob.find(marker)
    if marker_index < 0:
        return None

    region = message_blob[marker_index + len(marker): marker_index + len(marker) + lookahead]
    match = BASE64_TOKEN_PATTERN.search(region)
    if not match:
        return None
    return match.group(0).decode("utf-8", "ignore")


def _extract_ascii_run(message_blob: bytes, start_index: int, allowed_bytes: bytes) -> str:
    end_index = start_index
    while end_index < len(message_blob) and message_blob[end_index] in allowed_bytes:
        end_index += 1
    return message_blob[start_index:end_index].decode("utf-8", "ignore")


def _fallback_text_filehash(
    status_id: str | None,
    source_key: str,
    source_offset: int,
) -> str:
    digest = hashlib.sha256(f"{source_key}:{status_id or 'text'}:{source_offset}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


def _coerce_int(value) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _bytes_to_base64(value) -> str | None:
    if isinstance(value, (bytes, bytearray)) and value:
        return base64.b64encode(bytes(value)).decode("ascii").rstrip("=")
    if isinstance(value, str) and value:
        return value
    return None


def _walk_values(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, child
            yield from _walk_values(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            yield from _walk_values(child, path)


def _extract_text_value(message: dict) -> str | None:
    for candidate in ("caption", "text", "body", "content", "pollName"):
        value = _as_string(message.get(candidate))
        if value:
            return value

    for path, value in _walk_values(message):
        path_lower = path.lower()
        if not isinstance(value, str) or not value:
            continue
        if path_lower.endswith(".text") or path_lower.endswith(".caption") or path_lower.endswith(".body"):
            if STATUS_MARKER.decode("ascii") not in value:
                return value
    return None


def _extract_embedded_music(message: dict) -> dict[str, str | int | None]:
    result = {
        "title": None,
        "artist": None,
        "artwork_direct_path": None,
        "artwork_filehash": None,
        "artwork_enc_filehash": None,
        "artwork_media_key": None,
        "duration_ms": None,
    }
    for path, value in _walk_values(message):
        if not path.endswith("embeddedMusic"):
            continue
        if not isinstance(value, dict):
            continue
        result["title"] = _as_string(value.get("title"))
        result["artist"] = _as_string(value.get("author")) or _as_string(value.get("artistAttribution"))
        result["artwork_direct_path"] = _as_string(value.get("artworkDirectPath"))
        result["artwork_filehash"] = _bytes_to_base64(value.get("artworkSha256"))
        result["artwork_enc_filehash"] = _bytes_to_base64(value.get("artworkEncSha256"))
        result["artwork_media_key"] = _bytes_to_base64(value.get("artworkMediaKey"))
        result["duration_ms"] = _coerce_int(value.get("overlapDurationInMs"))
        break
    return result


def _extract_embedded_music_from_blob(message_blob: bytes) -> dict[str, str | int | None]:
    return {
        "title": _extract_ascii_after_marker(message_blob, b"title"),
        "artist": _extract_ascii_after_marker(message_blob, b"author"),
        "artwork_direct_path": _extract_path_after_marker(message_blob, b"artworkDirectPath"),
        "artwork_filehash": _extract_base64_after_marker(message_blob, b"artworkSha256"),
        "artwork_enc_filehash": _extract_base64_after_marker(message_blob, b"artworkEncSha256"),
        "artwork_media_key": _extract_base64_after_marker(message_blob, b"artworkMediaKey"),
        "duration_ms": _extract_int_after_marker(message_blob, b"overlapDurationInMs"),
    }


def _extract_ascii_after_marker(message_blob: bytes, marker: bytes, lookahead: int = 160) -> str | None:
    marker_index = message_blob.find(marker)
    if marker_index < 0:
        return None
    region = message_blob[marker_index + len(marker): marker_index + len(marker) + lookahead]
    matches = re.findall(rb"[A-Za-z0-9 _.,'()!?:;&/-]{3,120}", region)
    for match in matches:
        decoded = match.decode("utf-8", "ignore").strip()
        if decoded and decoded.lower() != marker.decode("utf-8", "ignore").lower():
            return decoded
    return None


def _extract_path_after_marker(message_blob: bytes, marker: bytes) -> str | None:
    marker_index = message_blob.find(marker)
    if marker_index < 0:
        return None
    path_index = message_blob.find(b"/", marker_index)
    if path_index < 0:
        return None
    extracted = _extract_ascii_run(message_blob, path_index, URL_SAFE_BYTES)
    return extracted if extracted.startswith("/") else None


def _extract_int_after_marker(message_blob: bytes, marker: bytes) -> int | None:
    marker_index = message_blob.find(marker)
    if marker_index < 0:
        return None
    region = message_blob[marker_index + len(marker): marker_index + len(marker) + 24]
    match = re.search(rb"(\d{1,8})", region)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _extract_firefox_subtype(message_blob: bytes) -> str | None:
    return _extract_ascii_after_marker(message_blob, b"subtype")


def _extract_firefox_background_color(message_blob: bytes) -> int | None:
    return _extract_int_after_marker(message_blob, b"backgroundColor")


def _extract_firefox_text_color(message_blob: bytes) -> int | None:
    return _extract_int_after_marker(message_blob, b"textColor")


def _extract_firefox_font_id(message_blob: bytes) -> int | None:
    return _extract_int_after_marker(message_blob, b"font")


def _download_plaintext_payload(record: StatusRecord) -> bytes | None:
    if record.kind == "texts":
        thumbnail_payload = _download_text_thumbnail_payload(record)
        if thumbnail_payload:
            return thumbnail_payload
        return None

    for url in _candidate_urls(record):
        try:
            encrypted_or_plain = _download_url(url)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            ValueError,
            OSError,
            http.client.InvalidURL,
        ):
            continue

        if _matches_sha256(encrypted_or_plain, record.filehash):
            return encrypted_or_plain

        if record.media_key:
            decrypted = _decrypt_media(record, encrypted_or_plain)
            if decrypted and _matches_sha256(decrypted, record.filehash):
                return decrypted

    return None


def _download_text_thumbnail_payload(record: StatusRecord) -> bytes | None:
    if not record.thumbnail_direct_path or not record.thumbnail_filehash:
        return None

    thumbnail_record = StatusRecord(
        status_id=f"{record.status_id}:thumbnail",
        kind="photos",
        mimetype="image/jpeg",
        url=f"https://mmg.whatsapp.net{_normalize_direct_path(record.thumbnail_direct_path)}",
        direct_path=record.thumbnail_direct_path,
        filehash=record.thumbnail_filehash,
        enc_filehash=record.thumbnail_enc_filehash,
        media_key=record.media_key,
        source_file=record.source_file,
        source_offset=record.source_offset,
        timestamp=record.timestamp,
        author_jid=record.author_jid,
        source_key=record.source_key,
        source_label=record.source_label,
        source_indexeddb_dir=record.source_indexeddb_dir,
        source_blob_dir=record.source_blob_dir,
    )
    return _download_plaintext_payload(thumbnail_record)


def _download_url(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "mmg.whatsapp.net":
        raise ValueError(f"Refusing to download from unexpected host: {url}")

    request = Request(url, headers=HTTP_HEADERS)
    with urlopen(request, timeout=30) as response:
        return response.read()


def _decrypt_media(record: StatusRecord, payload: bytes) -> bytes | None:
    if not record.media_key or record.kind not in MEDIA_INFO_BY_KIND or len(payload) <= 10:
        return None

    media_key = _decode_base64_value(record.media_key)
    expanded_key = HKDF(
        algorithm=hashes.SHA256(),
        length=112,
        salt=b"\x00" * 32,
        info=MEDIA_INFO_BY_KIND[record.kind],
    ).derive(media_key)

    iv = expanded_key[:16]
    cipher_key = expanded_key[16:48]
    ciphertext = payload[:-10]
    decryptor = Cipher(algorithms.AES(cipher_key), modes.CBC(iv)).decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

    try:
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded_plaintext) + unpadder.finalize()
    except ValueError:
        return None


def _candidate_urls(record: StatusRecord) -> list[str]:
    urls = [record.url]
    if record.direct_path:
        urls.append(f"https://mmg.whatsapp.net{_normalize_direct_path(record.direct_path)}")

    unique_urls: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            unique_urls.append(url)
    return unique_urls


def _cache_path_for_record(record: StatusRecord) -> str:
    if record.kind == "texts":
        extension = ".png"
    else:
        extension = _extension_for_mimetype(record.mimetype)

    cache_seed = _decode_base64_value(record.filehash)
    if record.kind == "texts":
        text_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "status_id": record.status_id,
                    "text_value": record.text_value,
                    "text_subtype": record.text_subtype,
                    "background_color": record.background_color,
                    "text_color": record.text_color,
                    "font_id": record.font_id,
                    "thumbnail_inline": bool(record.thumbnail_inline),
                    "thumbnail_direct_path": record.thumbnail_direct_path,
                },
                sort_keys=True,
                ensure_ascii=True,
            ).encode("utf-8")
        ).digest()[:12]
        cache_seed += text_fingerprint
    safe_name = base64.urlsafe_b64encode(cache_seed).decode("ascii").rstrip("=")
    if record.kind == "texts":
        safe_name = f"v{TEXT_ASSET_SCHEMA_VERSION}-{safe_name}"
    return os.path.join(STATUS_MEDIA_CACHE_DIR, record.kind, f"{safe_name}{extension}")


def _generate_text_status_asset(record: StatusRecord, cache_path: str) -> str | None:
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    preview_image = _load_text_preview_image(record)
    image = Image.new("RGB", (1080, 1920), _argb_to_rgb(record.background_color, default=(18, 28, 33)))
    draw = ImageDraw.Draw(image)

    if preview_image is not None:
        preview = preview_image.convert("RGB")
        preview.thumbnail((720, 720))
        preview_x = (image.width - preview.width) // 2
        preview_y = 300
        image.paste(preview, (preview_x, preview_y))

    title_text, subtitle_text = _text_status_headline(record)
    title_font_size = 76 if record.text_value else 62
    if record.text_subtype == "url":
        title_font_size = 52 if record.text_value else 46
    title_font = _load_text_font(record.font_id, title_font_size)
    subtitle_font = _load_text_font(record.font_id, 34)
    meta_font = _load_text_font(record.font_id, 36)
    text_color = _argb_to_rgb(record.text_color, default=(255, 255, 255))

    text_box_width = 900 if record.text_subtype == "url" else 820
    lines = _wrap_text(draw, title_text, title_font, text_box_width)
    line_height = int(title_font.size * 1.25)
    text_block_height = max(line_height, len(lines) * line_height)
    subtitle_height = 0
    if subtitle_text:
        subtitle_height = int(subtitle_font.size * 1.6)
    if preview_image is not None:
        text_top = 980
    elif record.text_subtype == "url":
        text_top = max(220, (image.height - (text_block_height + subtitle_height)) // 2)
    else:
        text_top = (image.height - (text_block_height + subtitle_height)) // 2
    current_y = text_top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_width = bbox[2] - bbox[0]
        draw.text(((image.width - line_width) / 2, current_y), line, fill=text_color, font=title_font)
        current_y += line_height

    if subtitle_text:
        bbox = draw.textbbox((0, 0), subtitle_text, font=subtitle_font)
        subtitle_width = bbox[2] - bbox[0]
        draw.text(
            ((image.width - subtitle_width) / 2, current_y + 12),
            subtitle_text,
            fill=text_color,
            font=subtitle_font,
        )
        current_y += subtitle_height

    footer = _text_status_footer(record)
    if footer:
        bbox = draw.textbbox((0, 0), footer, font=meta_font)
        footer_width = bbox[2] - bbox[0]
        draw.text(((image.width - footer_width) / 2, min(image.height - 180, current_y + 70)), footer, fill=text_color, font=meta_font)

    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, dir=os.path.dirname(cache_path), suffix=".tmp") as temp_handle:
            temp_file = temp_handle.name
        image.save(temp_file, format="PNG")
        os.replace(temp_file, cache_path)
        return cache_path
    finally:
        if temp_file and os.path.exists(temp_file):
            os.unlink(temp_file)


def _load_text_preview_image(record: StatusRecord) -> Image.Image | None:
    if record.thumbnail_inline:
        try:
            payload = base64.b64decode(record.thumbnail_inline)
            with Image.open(BytesIO(payload)) as image:
                return image.copy()
        except Exception:
            pass
    payload = _download_text_thumbnail_payload(record)
    if not payload:
        return None
    try:
        with Image.open(BytesIO(payload)) as image:
            return image.copy()
    except Exception:
        return None


def _default_text_status_label(record: StatusRecord) -> str:
    if record.text_subtype == "url":
        return "Link status"
    return "Text status"


def _text_status_headline(record: StatusRecord) -> tuple[str, str | None]:
    text_value = (record.text_value or "").strip()
    if text_value:
        if record.text_subtype == "url":
            return text_value, "Link status"
        return text_value, None

    if record.text_subtype == "url":
        return "Link preview", _format_status_timestamp(record.timestamp)

    return _format_status_timestamp(record.timestamp), "Text status"


def _text_status_footer(record: StatusRecord) -> str | None:
    parts = []
    if record.music_title and record.music_artist:
        parts.append(f"{record.music_title} - {record.music_artist}")
    elif record.music_title:
        parts.append(record.music_title)
    return "  ".join(parts) if parts else None


def _format_status_timestamp(timestamp: float | None) -> str:
    if not timestamp:
        return "Recent status"
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime("%I:%M %p").lstrip("0")
    except (OSError, OverflowError, ValueError):
        return "Recent status"


def _argb_to_rgb(value: int | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if value is None:
        return default
    normalized = int(value) & 0xFFFFFFFF
    return ((normalized >> 16) & 0xFF, (normalized >> 8) & 0xFF, normalized & 0xFF)


def _load_text_font(font_id: int | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_paths = {
        0: r"C:\Windows\Fonts\segoeui.ttf",
        1: r"C:\Windows\Fonts\georgia.ttf",
        2: r"C:\Windows\Fonts\SCRIPTBL.TTF",
        3: r"C:\Windows\Fonts\comic.ttf",
        4: r"C:\Windows\Fonts\arialbd.ttf",
    }
    font_path = font_paths.get(font_id or 0, font_paths[0])
    try:
        return ImageFont.truetype(font_path, size=size)
    except OSError:
        return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return ["Text status"]

    lines: list[str] = []
    current = ""
    for raw_word in words:
        word_segments = _split_long_token(draw, raw_word, font, max_width)
        for index, word in enumerate(word_segments):
            spacer = "" if not current else " "
            trial = f"{current}{spacer}{word}" if current else word
            bbox = draw.textbbox((0, 0), trial, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                current = trial
                continue
            if current:
                lines.append(current)
                current = word
            else:
                lines.append(word)
                current = ""
        if not word_segments and raw_word:
            current = raw_word if not current else f"{current} {raw_word}"
    if current:
        lines.append(current)
    return lines[:10]


def _split_long_token(draw: ImageDraw.ImageDraw, token: str, font, max_width: int) -> list[str]:
    if not token:
        return []
    bbox = draw.textbbox((0, 0), token, font=font)
    if (bbox[2] - bbox[0]) <= max_width:
        return [token]

    break_markers = {"/", "?", "&", "=", "-", "_", ".", "#"}
    parts: list[str] = []
    current = ""
    for character in token:
        trial = f"{current}{character}"
        trial_bbox = draw.textbbox((0, 0), trial, font=font)
        should_break = (
            current
            and (trial_bbox[2] - trial_bbox[0]) > max_width
        )
        if should_break:
            parts.append(current)
            current = character
            continue
        current = trial
        if character in break_markers:
            parts.append(current)
            current = ""
    if current:
        parts.append(current)

    flattened: list[str] = []
    for part in parts:
        if not part:
            continue
        bbox = draw.textbbox((0, 0), part, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            flattened.append(part)
            continue
        chunk = ""
        for character in part:
            trial = f"{chunk}{character}"
            trial_bbox = draw.textbbox((0, 0), trial, font=font)
            if chunk and (trial_bbox[2] - trial_bbox[0]) > max_width:
                flattened.append(chunk)
                chunk = character
            else:
                chunk = trial
        if chunk:
            flattened.append(chunk)
    return flattened


def _extension_for_mimetype(mimetype: str) -> str:
    if mimetype in IMAGE_EXTENSIONS:
        return IMAGE_EXTENSIONS[mimetype]
    if mimetype in VIDEO_EXTENSIONS:
        return VIDEO_EXTENSIONS[mimetype]
    guessed_extension = mimetypes.guess_extension(mimetype)
    return guessed_extension or ".bin"


def _kind_from_mimetype(mimetype: str) -> str | None:
    if mimetype.startswith("image/"):
        return "photos"
    if mimetype.startswith("video/"):
        return "videos"
    return None


def _extract_group(pattern, window: bytes) -> str | None:
    match = pattern.search(window)
    if not match:
        return None
    return match.group(1).decode("utf-8", "ignore")


def _matches_sha256(payload: bytes, expected_hash: str | None) -> bool:
    if not expected_hash:
        return False
    actual_hash = base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii").rstrip("=")
    normalized_expected = expected_hash.rstrip("=")
    if actual_hash == normalized_expected:
        return True

    # Firefox occasionally exposes a one-character-truncated base64 hash in IndexedDB.
    if len(actual_hash) == len(normalized_expected) + 1:
        return actual_hash.endswith(normalized_expected) or actual_hash.startswith(normalized_expected)
    return False


def _decode_base64_value(value: str) -> bytes:
    normalized = value.strip()
    padding = "=" * (-len(normalized) % 4)
    return base64.b64decode(f"{normalized}{padding}")


def _normalize_direct_path(path: str) -> str:
    normalized = "".join(char for char in path if char.isprintable())
    if normalized.startswith("../"):
        normalized = normalized[2:]
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _normalize_extracted_url(url: str | None) -> str | None:
    if not url:
        return None
    sanitized = "".join(char for char in url if char.isprintable())
    parsed = urlparse(sanitized)
    if parsed.scheme != "https" or parsed.netloc != "mmg.whatsapp.net":
        return None
    return sanitized


def _as_string(value) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _serialized_jid(value) -> str | None:
    if isinstance(value, dict):
        serialized = value.get("_serialized")
        if isinstance(serialized, str) and serialized:
            return serialized
    if isinstance(value, str) and value:
        return value
    return None
