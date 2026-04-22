import base64
import concurrent.futures
import hashlib
import http.client
import json
import mimetypes
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from config import STATUS_MEDIA_CACHE_DIR, get_status_source_config

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
MAX_CACHE_WORKERS = 6

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


def has_webview_status_source(
    source_mode: str = "desktop",
    selected_web_browser: str = "chrome",
    selected_web_profile: str | None = None,
) -> bool:
    source_config = get_status_source_config(
        source_mode,
        selected_web_browser,
        selected_web_profile,
    )
    return os.path.isdir(source_config["indexeddb_dir"])


def get_webview_status_files(
    file_type: str,
    page: int = 1,
    items_per_page: int | None = None,
    source_mode: str = "desktop",
    selected_web_browser: str = "chrome",
    selected_web_profile: str | None = None,
) -> list[str]:
    if file_type not in {"photos", "videos"}:
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
    if file_type not in {"photos", "videos"}:
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
        return records

    start = max(0, (page - 1) * items_per_page)
    stop = start + items_per_page
    return records[start:stop]


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
    global _STATUS_RECORD_CACHE

    source_config = get_status_source_config(
        source_mode,
        selected_web_browser,
        selected_web_profile,
    )
    source_key = source_config["key"]

    if not has_webview_status_source(
        source_mode,
        selected_web_browser,
        selected_web_profile,
    ):
        return []

    snapshot = _build_indexeddb_snapshot(source_config)
    cached_snapshot = _STATUS_RECORD_CACHE.get(source_key)
    if cached_snapshot and cached_snapshot[0] == snapshot:
        return list(cached_snapshot[1])

    cached_records = _load_cached_records(source_key, snapshot)
    if cached_records is not None:
        _STATUS_RECORD_CACHE[source_key] = (snapshot, cached_records)
        return list(cached_records)

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
    source_config = get_status_source_config(
        source_mode,
        selected_web_browser,
        selected_web_profile,
    )
    source_key = source_config["key"]
    _STATUS_RECORD_CACHE.pop(source_key, None)
    index_cache_file = _index_cache_file_for_source(source_key)
    if os.path.exists(index_cache_file):
        try:
            os.remove(index_cache_file)
        except OSError:
            pass


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
    else:
        return None

    mimetype = _as_string(message.get("mimetype"))
    filehash = _as_string(message.get("filehash"))
    direct_path = _as_string(message.get("directPath"))
    url = _normalize_extracted_url(_as_string(message.get("deprecatedMms3Url")))

    if not url and direct_path:
        url = f"https://mmg.whatsapp.net{_normalize_direct_path(direct_path)}"

    if not mimetype or not filehash or not url:
        return None

    return StatusRecord(
        status_id=_extract_status_id(message) or filehash,
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
    import re

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


def _download_plaintext_payload(record: StatusRecord) -> bytes | None:
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

    media_key = base64.b64decode(record.media_key)
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
    extension = _extension_for_mimetype(record.mimetype)
    safe_name = base64.urlsafe_b64encode(
        base64.b64decode(record.filehash)
    ).decode("ascii").rstrip("=")
    return os.path.join(STATUS_MEDIA_CACHE_DIR, record.kind, f"{safe_name}{extension}")


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
    actual_hash = base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")
    return actual_hash == expected_hash


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
