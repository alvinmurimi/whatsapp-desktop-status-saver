import asyncio
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import websockets

from config import SETTINGS_DIR

if TYPE_CHECKING:
    from webview_status_source import StatusRecord


TEXT_HYDRATION_CACHE_FILE = os.path.join(SETTINGS_DIR, "text_hydration_cache.json")
TEXT_HYDRATION_SCHEMA_VERSION = 1
TEXT_HYDRATION_RETRY_COOLDOWN_SECONDS = 15 * 60
TEXT_HYDRATION_SUCCESS_TTL_SECONDS = 7 * 24 * 60 * 60

_CACHE_LOCK = threading.Lock()
_ACTIVE_HYDRATION_LOCK = threading.Lock()
_ACTIVE_HYDRATION_KEYS: set[str] = set()
_BUNDLE_CACHE: str | None = None

_CHROMIUM_FILE_ITEMS = (
    "Preferences",
    "Secure Preferences",
)
_CHROMIUM_DIR_ITEMS = (
    "IndexedDB",
    "blob_storage",
    "Service Worker",
    "Local Storage",
    "Session Storage",
    "Sessions",
    "Network",
    "shared_proto_db",
    "Shared Dictionary",
)
_CHROMIUM_FLAGS = (
    "--headless=new",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--no-first-run",
    "--no-default-browser-check",
)
_CHROME_EXE_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_EDGE_EXE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_FIREFOX_EXE_CANDIDATES = (
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
)
_FIREFOX_ITEMS = (
    "storage",
    "cookies.sqlite",
    "cookies.sqlite-wal",
    "cookies.sqlite-shm",
    "webappsstore.sqlite",
    "prefs.js",
    "xulstore.json",
    "permissions.sqlite",
    "sessionstore.jsonlz4",
)


def merge_cached_text_hydration(records: list["StatusRecord"]) -> list["StatusRecord"]:
    if not records:
        return records

    cache = _load_cache()
    sources = cache.get("sources", {})
    now = time.time()
    merged_records: list["StatusRecord"] = []

    for record in records:
        if record.kind != "texts":
            merged_records.append(record)
            continue

        source_entry = sources.get(record.source_key) or {}
        updated_at = float(source_entry.get("updated_at") or 0)
        if updated_at and (now - updated_at) > TEXT_HYDRATION_SUCCESS_TTL_SECONDS:
            merged_records.append(record)
            continue

        item = (source_entry.get("items") or {}).get(record.status_id)
        if not isinstance(item, dict):
            merged_records.append(record)
            continue

        merged_records.append(
            replace(
                record,
                text_value=(item.get("text_value") or record.text_value),
                text_subtype=(item.get("text_subtype") or record.text_subtype),
                background_color=_coerce_int(item.get("background_color"), record.background_color),
                text_color=_coerce_int(item.get("text_color"), record.text_color),
                font_id=_coerce_int(item.get("font_id"), record.font_id),
                thumbnail_inline=(item.get("thumbnail_inline") or record.thumbnail_inline),
            )
        )

    return merged_records


def records_need_live_hydration(records: list["StatusRecord"]) -> bool:
    if not records:
        return False

    cache = _load_cache()
    sources = cache.get("sources", {})
    now = time.time()

    for record in records:
        if record.kind != "texts":
            continue
        if (record.text_value or "").strip():
            continue

        source_entry = sources.get(record.source_key) or {}
        updated_at = float(source_entry.get("updated_at") or 0)
        last_attempt_at = float(source_entry.get("last_attempt_at") or 0)
        items = source_entry.get("items") or {}
        cached_item = items.get(record.status_id)
        if cached_item and (cached_item.get("text_value") or "").strip():
            continue
        if updated_at and (now - updated_at) <= TEXT_HYDRATION_SUCCESS_TTL_SECONDS:
            return False
        if last_attempt_at and (now - last_attempt_at) <= TEXT_HYDRATION_RETRY_COOLDOWN_SECONDS:
            return False
        return True
    return False


def hydrate_live_text_records(records: list["StatusRecord"]) -> dict[str, object]:
    text_records = [record for record in records if record.kind == "texts"]
    if not text_records:
        return {"updated": 0, "sources": [], "supported": False}

    grouped: dict[str, list["StatusRecord"]] = {}
    for record in text_records:
        grouped.setdefault(record.source_key, []).append(record)

    updated_total = 0
    updated_sources: list[str] = []
    skipped_sources: list[str] = []

    for source_key, source_records in grouped.items():
        if not _is_supported_source_key(source_key):
            skipped_sources.append(source_key)
            continue

        with _ACTIVE_HYDRATION_LOCK:
            if source_key in _ACTIVE_HYDRATION_KEYS:
                continue
            _ACTIVE_HYDRATION_KEYS.add(source_key)

        try:
            updated = _hydrate_source_group(source_key, source_records)
            if updated > 0:
                updated_total += updated
                updated_sources.append(source_key)
        finally:
            with _ACTIVE_HYDRATION_LOCK:
                _ACTIVE_HYDRATION_KEYS.discard(source_key)

    return {
        "updated": updated_total,
        "sources": updated_sources,
        "skipped_sources": skipped_sources,
        "supported": bool(updated_sources) or not skipped_sources,
    }


def _hydrate_source_group(source_key: str, records: list["StatusRecord"]) -> int:
    now = time.time()
    _mark_source_attempt(source_key, now)

    runtime = _runtime_for_records(records)
    if not runtime:
        return 0

    expected_ids = {record.status_id for record in records}
    extracted_items = _probe_text_records_via_cdp(runtime, expected_ids)
    if not extracted_items:
        return 0

    changed = _store_source_items(source_key, extracted_items, now)
    return changed


def _runtime_for_records(records: list["StatusRecord"]) -> dict | None:
    first = records[0]
    indexeddb_dir = first.source_indexeddb_dir
    if not indexeddb_dir:
        return None

    indexeddb_path = Path(indexeddb_dir)
    profile_dir = indexeddb_path.parent.parent
    user_data_dir = profile_dir.parent

    if first.source_key == "desktop":
        browser = "desktop"
        executable_path = _pick_existing_path(_EDGE_EXE_CANDIDATES) or _pick_existing_path(_CHROME_EXE_CANDIDATES)
        profile_name = profile_dir.name
    elif first.source_key.startswith("chrome-"):
        browser = "chrome"
        executable_path = _pick_existing_path(_CHROME_EXE_CANDIDATES)
        profile_name = profile_dir.name
    elif first.source_key.startswith("edge-"):
        browser = "edge"
        executable_path = _pick_existing_path(_EDGE_EXE_CANDIDATES)
        profile_name = profile_dir.name
    elif first.source_key.startswith("firefox-"):
        try:
            profile_dir = indexeddb_path.parents[3]
        except IndexError:
            return None
        browser = "firefox"
        executable_path = _pick_existing_path(_FIREFOX_EXE_CANDIDATES)
        profile_name = profile_dir.name
    else:
        return None

    if not executable_path:
        return None

    return {
        "browser": browser,
        "executable_path": executable_path,
        "user_data_dir": str(user_data_dir),
        "profile_dir": str(profile_dir),
        "profile_name": profile_name,
    }


def _probe_text_records_via_cdp(runtime: dict, expected_ids: set[str]) -> dict[str, dict]:
    bundle_source = _load_wpp_bundle_source()
    if not bundle_source:
        return {}

    with tempfile.TemporaryDirectory(prefix="wa-text-hydration-") as temp_root:
        clone_root = os.path.join(temp_root, "profile")
        if runtime["browser"] == "firefox":
            _clone_firefox_profile(runtime["profile_dir"], clone_root)
            return asyncio.run(
                _probe_text_records_via_bidi_async(
                    runtime["executable_path"],
                    clone_root,
                    bundle_source,
                    expected_ids,
                )
            )

        _clone_chromium_profile(
            runtime["user_data_dir"],
            runtime["profile_name"],
            clone_root,
        )
        return asyncio.run(
            _probe_text_records_async(
                runtime["executable_path"],
                clone_root,
                runtime["profile_name"],
                bundle_source,
                expected_ids,
            )
        )


async def _probe_text_records_async(
    executable_path: str,
    clone_root: str,
    profile_name: str,
    bundle_source: str,
    expected_ids: set[str],
) -> dict[str, dict]:
    process = None
    port = None
    try:
        process = subprocess.Popen(
            [
                executable_path,
                *(_CHROMIUM_FLAGS),
                f"--user-data-dir={clone_root}",
                f"--profile-directory={profile_name}",
                "about:blank",
                "--remote-debugging-port=0",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        port = await _wait_for_devtools_port(clone_root)
        ws_url = await _resolve_page_websocket_url(port)
        if not ws_url:
            return {}

        async with websockets.connect(ws_url, max_size=20_000_000) as websocket:
            message_id = 0

            async def cdp(method: str, params: dict | None = None):
                nonlocal message_id
                message_id += 1
                payload = {
                    "id": message_id,
                    "method": method,
                    "params": params or {},
                }
                await websocket.send(json.dumps(payload))
                while True:
                    raw = await websocket.recv()
                    data = json.loads(raw)
                    if data.get("id") == message_id:
                        if "error" in data:
                            raise RuntimeError(f"{method} failed: {data['error']}")
                        return data.get("result", {})

            await cdp("Page.enable")
            await cdp("Runtime.enable")
            await cdp(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": bundle_source},
            )
            await cdp(
                "Page.navigate",
                {"url": "https://web.whatsapp.com/"},
            )

            extracted: dict[str, dict] = {}
            deadline = time.time() + 55
            while time.time() < deadline:
                await asyncio.sleep(4)
                result = await cdp(
                    "Runtime.evaluate",
                    {
                        "expression": _TEXT_EXTRACT_EXPRESSION,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                )
                value = ((result.get("result") or {}).get("value") or {})
                if not isinstance(value, dict):
                    continue
                items = value.get("items") or []
                for item in items:
                    message_id = item.get("messageId")
                    status_id = _normalize_live_message_id(message_id or item.get("statusId"))
                    if not status_id:
                        continue
                    extracted[status_id] = {
                        "text_value": item.get("body"),
                        "text_subtype": item.get("subtype"),
                        "background_color": _coerce_int(item.get("backgroundColor"), None),
                        "text_color": _coerce_int(item.get("textColor"), None),
                        "font_id": _coerce_int(item.get("font"), None),
                        "thumbnail_inline": item.get("thumbnailInline"),
                    }

                matched_ids = [
                    status_id
                    for status_id, item in extracted.items()
                    if status_id in expected_ids and (item.get("text_value") or "").strip()
                ]
                if matched_ids:
                    break

            return extracted
    except Exception:
        return {}
    finally:
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


async def _probe_text_records_via_bidi_async(
    executable_path: str,
    clone_root: str,
    bundle_source: str,
    expected_ids: set[str],
) -> dict[str, dict]:
    process = None
    try:
        process = subprocess.Popen(
            [
                executable_path,
                "--headless",
                "--new-instance",
                "--remote-debugging-port",
                "0",
                "-profile",
                clone_root,
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        bidi_base_url = await _wait_for_firefox_bidi_url(process)
        if not bidi_base_url:
            return {}

        ws_url = bidi_base_url.rstrip("/")
        if not ws_url.endswith("/session"):
            ws_url = f"{ws_url}/session"

        async with websockets.connect(ws_url, max_size=20_000_000) as websocket:
            message_id = 0

            async def bidi(method: str, params: dict | None = None):
                nonlocal message_id
                message_id += 1
                payload = {
                    "id": message_id,
                    "method": method,
                    "params": params or {},
                }
                await websocket.send(json.dumps(payload))
                while True:
                    raw = await websocket.recv()
                    data = json.loads(raw)
                    if data.get("id") == message_id:
                        if "error" in data:
                            raise RuntimeError(f"{method} failed: {data['error']}")
                        return data.get("result", {})

            await bidi("session.new", {"capabilities": {"alwaysMatch": {}}})

            context_id = None
            context_deadline = time.time() + 20
            while time.time() < context_deadline and not context_id:
                tree = await bidi("browsingContext.getTree", {})
                contexts = tree.get("contexts") or []
                if contexts:
                    context_id = contexts[0].get("context")
                    break
                await asyncio.sleep(0.25)

            if not context_id:
                return {}

            await bidi(
                "browsingContext.navigate",
                {
                    "context": context_id,
                    "url": "https://web.whatsapp.com/",
                    "wait": "interactive",
                },
            )

            extracted: dict[str, dict] = {}
            bundle_injected = False
            deadline = time.time() + 60
            while time.time() < deadline:
                await asyncio.sleep(4)

                if not bundle_injected:
                    try:
                        await bidi(
                            "script.evaluate",
                            {
                                "target": {"context": context_id},
                                "expression": f"{bundle_source}\n;true",
                                "awaitPromise": True,
                                "resultOwnership": "none",
                            },
                        )
                        bundle_injected = True
                    except Exception:
                        continue

                try:
                    result = await bidi(
                        "script.evaluate",
                        {
                            "target": {"context": context_id},
                            "expression": _TEXT_EXTRACT_EXPRESSION,
                            "awaitPromise": True,
                            "resultOwnership": "none",
                        },
                    )
                except Exception:
                    bundle_injected = False
                    continue

                remote_value = result
                if isinstance(result, dict) and result.get("type") == "success":
                    remote_value = result.get("result")
                elif isinstance(result, dict) and isinstance(result.get("result"), dict):
                    nested = result.get("result")
                    if nested.get("type") == "success":
                        remote_value = nested.get("result")

                value = _decode_bidi_remote_value(remote_value)
                if not isinstance(value, dict):
                    continue

                items = value.get("items") or []
                if not isinstance(items, list):
                    continue

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    message_id_value = item.get("messageId")
                    status_id = _normalize_live_message_id(message_id_value or item.get("statusId"))
                    if not status_id:
                        continue
                    extracted[status_id] = {
                        "text_value": item.get("body"),
                        "text_subtype": item.get("subtype"),
                        "background_color": _coerce_int(item.get("backgroundColor"), None),
                        "text_color": _coerce_int(item.get("textColor"), None),
                        "font_id": _coerce_int(item.get("font"), None),
                        "thumbnail_inline": item.get("thumbnailInline"),
                    }

                matched_ids = [
                    status_id
                    for status_id, item in extracted.items()
                    if status_id in expected_ids and (item.get("text_value") or "").strip()
                ]
                if matched_ids:
                    break

            return extracted
    except Exception:
        return {}
    finally:
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


async def _wait_for_firefox_bidi_url(process: subprocess.Popen) -> str | None:
    if process.stderr is None:
        return None

    bidi_url_pattern = re.compile(r"WebDriver BiDi listening on (ws://\S+)")
    line_queue: queue.Queue[bytes] = queue.Queue()

    def _reader():
        try:
            while True:
                raw_line = process.stderr.readline()
                if not raw_line:
                    break
                line_queue.put(raw_line)
        except Exception:
            return

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    deadline = time.time() + 25
    while time.time() < deadline:
        if process.poll() is not None and line_queue.empty():
            break
        try:
            raw_line = line_queue.get(timeout=0.25)
        except queue.Empty:
            continue
        line = raw_line.decode("utf-8", "ignore").strip()
        match = bidi_url_pattern.search(line)
        if match:
            return match.group(1)

    return None


async def _wait_for_devtools_port(clone_root: str) -> int:
    port_file = os.path.join(clone_root, "DevToolsActivePort")
    deadline = time.time() + 20
    while time.time() < deadline:
        if os.path.isfile(port_file):
            try:
                with open(port_file, "r", encoding="utf-8") as handle:
                    first_line = handle.readline().strip()
                return int(first_line)
            except (OSError, ValueError):
                pass
        await asyncio.sleep(0.25)
    raise TimeoutError("Timed out waiting for DevToolsActivePort")


async def _resolve_page_websocket_url(port: int) -> str | None:
    deadline = time.time() + 20
    endpoint = f"http://127.0.0.1:{port}/json"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=5) as response:
                targets = json.loads(response.read().decode("utf-8"))
            for target in targets:
                if target.get("type") == "page":
                    return target.get("webSocketDebuggerUrl")
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.25)
    return None


def _clone_chromium_profile(source_user_data_dir: str, profile_name: str, clone_root: str) -> None:
    os.makedirs(clone_root, exist_ok=True)
    source_profile_dir = os.path.join(source_user_data_dir, profile_name)
    clone_profile_dir = os.path.join(clone_root, profile_name)
    os.makedirs(clone_profile_dir, exist_ok=True)

    local_state_path = os.path.join(source_user_data_dir, "Local State")
    if os.path.isfile(local_state_path):
        shutil.copy2(local_state_path, os.path.join(clone_root, "Local State"))

    for item_name in _CHROMIUM_FILE_ITEMS:
        source_path = os.path.join(source_profile_dir, item_name)
        if os.path.exists(source_path):
            _copy_path(source_path, os.path.join(clone_profile_dir, item_name))

    for dir_name in _CHROMIUM_DIR_ITEMS:
        source_path = os.path.join(source_profile_dir, dir_name)
        if os.path.exists(source_path):
            _copy_path(source_path, os.path.join(clone_profile_dir, dir_name))


def _copy_path(source_path: str, target_path: str) -> None:
    try:
        if os.path.isdir(source_path):
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(source_path, target_path)
    except Exception:
        # Browser-owned files can be locked. Best-effort cloning is good enough for
        # the live text probe because IndexedDB and local session data usually survive.
        pass


def _clone_firefox_profile(source_profile_dir: str, clone_root: str) -> None:
    os.makedirs(clone_root, exist_ok=True)
    for item_name in _FIREFOX_ITEMS:
        source_path = os.path.join(source_profile_dir, item_name)
        if os.path.exists(source_path):
            _copy_path(source_path, os.path.join(clone_root, item_name))


def _decode_bidi_remote_value(value):
    if not isinstance(value, dict):
        return value

    value_type = value.get("type")
    if value_type in {"undefined", "null"}:
        return None
    if value_type in {"string", "number", "boolean"}:
        return value.get("value")
    if value_type == "bigint":
        return value.get("value")
    if value_type == "array":
        return [
            _decode_bidi_remote_value(item)
            for item in (value.get("value") or [])
        ]
    if value_type in {"object", "map"}:
        raw_value = value.get("value")
        if isinstance(raw_value, dict):
            return {
                key: _decode_bidi_remote_value(item)
                for key, item in raw_value.items()
            }
        if isinstance(raw_value, list):
            decoded: dict = {}
            for entry in raw_value:
                if not isinstance(entry, list) or len(entry) != 2:
                    continue
                raw_key, raw_item = entry
                key = _decode_bidi_remote_value(raw_key) if isinstance(raw_key, dict) else raw_key
                decoded[str(key)] = _decode_bidi_remote_value(raw_item)
            return decoded
        return raw_value
    if value_type == "regexp":
        pattern = value.get("pattern")
        flags = value.get("flags")
        return f"/{pattern}/{flags}" if pattern is not None else None

    if "value" in value:
        return _decode_bidi_remote_value(value.get("value"))
    return value


def _load_wpp_bundle_source() -> str:
    global _BUNDLE_CACHE
    if _BUNDLE_CACHE is not None:
        return _BUNDLE_CACHE

    candidate_paths = [
        os.path.join(os.path.dirname(__file__), "vendor", "wppconnect-wa.js"),
        os.path.join(
            os.path.dirname(__file__),
            "tmp-live-probe",
            "node_modules",
            "@wppconnect",
            "wa-js",
            "dist",
            "wppconnect-wa.js",
        ),
    ]
    for candidate_path in candidate_paths:
        if not os.path.isfile(candidate_path):
            continue
        with open(candidate_path, "r", encoding="utf-8") as handle:
            _BUNDLE_CACHE = handle.read()
        return _BUNDLE_CACHE
    return ""


def _load_cache() -> dict:
    with _CACHE_LOCK:
        if not os.path.isfile(TEXT_HYDRATION_CACHE_FILE):
            return {"schema_version": TEXT_HYDRATION_SCHEMA_VERSION, "sources": {}}
        try:
            with open(TEXT_HYDRATION_CACHE_FILE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, TypeError):
            return {"schema_version": TEXT_HYDRATION_SCHEMA_VERSION, "sources": {}}

        if payload.get("schema_version") != TEXT_HYDRATION_SCHEMA_VERSION:
            return {"schema_version": TEXT_HYDRATION_SCHEMA_VERSION, "sources": {}}
        payload.setdefault("sources", {})
        return payload


def _write_cache(cache: dict) -> None:
    with _CACHE_LOCK:
        with open(TEXT_HYDRATION_CACHE_FILE, "w", encoding="utf-8") as handle:
            json.dump(cache, handle)


def _mark_source_attempt(source_key: str, attempt_at: float) -> None:
    cache = _load_cache()
    sources = cache.setdefault("sources", {})
    source_entry = sources.setdefault(source_key, {})
    source_entry["last_attempt_at"] = attempt_at
    _write_cache(cache)


def _store_source_items(source_key: str, items: dict[str, dict], updated_at: float) -> int:
    cache = _load_cache()
    sources = cache.setdefault("sources", {})
    source_entry = sources.setdefault(source_key, {})
    current_items = source_entry.setdefault("items", {})
    changed = 0

    for status_id, item in items.items():
        if status_id not in current_items or current_items[status_id] != item:
            current_items[status_id] = item
            changed += 1

    source_entry["updated_at"] = updated_at
    source_entry["last_attempt_at"] = updated_at
    _write_cache(cache)
    return changed


def _pick_existing_path(paths: tuple[str, ...]) -> str | None:
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


def _is_supported_source_key(source_key: str) -> bool:
    return (
        source_key == "desktop"
        or source_key.startswith("chrome-")
        or source_key.startswith("edge-")
        or source_key.startswith("firefox-")
    )


def _coerce_int(value, fallback):
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_live_message_id(message_id: str | None) -> str | None:
    if not message_id:
        return None
    if message_id.startswith("false_"):
        return message_id[len("false_") :]
    return message_id


_TEXT_EXTRACT_EXPRESSION = r"""
(() => {
  const statusStore = window.WPP?.whatsapp?.StatusV3Store;
  const statusModels = statusStore?.getModelsArray?.() || [];
  const items = [];
  for (const statusModel of statusModels) {
    const msgs = statusModel.getAllMsgs?.() || [];
    for (const msg of msgs) {
      if (msg?.type !== "chat") continue;
      const body =
        msg?.body ||
        msg?.text ||
        msg?.caption ||
        msg?.__x_body ||
        msg?.__x_text ||
        null;
      const thumbnailInline =
        typeof msg?.thumbnail === "string"
          ? msg.thumbnail
          : typeof msg?.__x_thumbnail === "string"
            ? msg.__x_thumbnail
            : null;
      items.push({
        statusId:
          statusModel?.id?._serialized ||
          statusModel?.id?.toString?.() ||
          null,
        messageId:
          msg?.id?._serialized ||
          msg?.id?.toString?.() ||
          null,
        body,
        subtype: msg?.subtype || msg?.__x_subtype || null,
        backgroundColor:
          msg?.backgroundColor || msg?.__x_backgroundColor || null,
        textColor: msg?.textColor || msg?.__x_textColor || null,
        font: msg?.font || msg?.__x_font || null,
        thumbnailInline,
      });
    }
  }
  return {
    statusModelCount: statusModels.length,
    items,
  };
})()
""".strip()
