import os
import json
import platform

# Determine the operating system
SYSTEM = platform.system()

WINDOWS_PACKAGE_ROOT = os.path.expandvars(
    r"%userprofile%\AppData\Local\Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm"
)
WINDOWS_STATUS_CANDIDATES = [
    os.path.join(WINDOWS_PACKAGE_ROOT, "LocalState", "shared", "transfers"),
    os.path.join(WINDOWS_PACKAGE_ROOT, "LocalState", "shared"),
]
MACOS_STATUS_CANDIDATES = [
    os.path.expanduser(
        "~/Library/Containers/net.whatsapp.WhatsApp/Data/Library/Application Support/WhatsApp/shared/transfers"
    )
]
WINDOWS_WEBVIEW_INDEXEDDB_DIR = os.path.join(
    WINDOWS_PACKAGE_ROOT,
    "LocalCache",
    "EBWebView",
    "Default",
    "IndexedDB",
    "https_web.whatsapp.com_0.indexeddb.leveldb",
)
WINDOWS_WEBVIEW_BLOB_DIR = os.path.join(
    WINDOWS_PACKAGE_ROOT,
    "LocalCache",
    "EBWebView",
    "Default",
    "IndexedDB",
    "https_web.whatsapp.com_0.indexeddb.blob",
)


def _first_existing_path(paths):
    for path in paths:
        if os.path.exists(path):
            return path
    return paths[0]


def get_whatsapp_status_path():
    if SYSTEM == "Windows":
        return _first_existing_path(WINDOWS_STATUS_CANDIDATES)
    if SYSTEM == "Darwin":
        return _first_existing_path(MACOS_STATUS_CANDIDATES)
    raise NotImplementedError(f"Unsupported operating system: {SYSTEM}")


def get_whatsapp_storage_diagnostics():
    if SYSTEM == "Windows":
        return {
            "package_root": WINDOWS_PACKAGE_ROOT,
            "selected_status_path": get_whatsapp_status_path(),
            "known_candidates": WINDOWS_STATUS_CANDIDATES,
        }
    if SYSTEM == "Darwin":
        return {
            "package_root": os.path.expanduser(
                "~/Library/Containers/net.whatsapp.WhatsApp"
            ),
            "selected_status_path": get_whatsapp_status_path(),
            "known_candidates": MACOS_STATUS_CANDIDATES,
        }
    raise NotImplementedError(f"Unsupported operating system: {SYSTEM}")


# Default paths and settings
if SYSTEM == "Windows":
    WHATSAPP_STATUS_PATH = get_whatsapp_status_path()
    WHATSAPP_WEBVIEW_INDEXEDDB_DIR = WINDOWS_WEBVIEW_INDEXEDDB_DIR
    WHATSAPP_WEBVIEW_BLOB_DIR = WINDOWS_WEBVIEW_BLOB_DIR
    SETTINGS_DIR = os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'WhatsAppStatusSaver')
elif SYSTEM == "Darwin":  # macOS
    WHATSAPP_STATUS_PATH = get_whatsapp_status_path()
    WHATSAPP_WEBVIEW_INDEXEDDB_DIR = ""
    WHATSAPP_WEBVIEW_BLOB_DIR = ""
    SETTINGS_DIR = os.path.expanduser('~/Library/Application Support/WhatsAppStatusSaver')
else:
    raise NotImplementedError(f"Unsupported operating system: {SYSTEM}")
    

DEFAULT_SAVE_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', 'WhatsappStatuses')
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
THUMBNAIL_CACHE_DIR = os.path.join(SETTINGS_DIR, "thumbnail_cache")
STATUS_MEDIA_CACHE_DIR = os.path.join(SETTINGS_DIR, "status_media_cache")

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return {"save_dir": DEFAULT_SAVE_DIR, "theme_mode": "light"}

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f)

# Create necessary directories
if not os.path.exists(SETTINGS_DIR):
    os.makedirs(SETTINGS_DIR)

if not os.path.exists(THUMBNAIL_CACHE_DIR):
    os.makedirs(THUMBNAIL_CACHE_DIR)

if not os.path.exists(STATUS_MEDIA_CACHE_DIR):
    os.makedirs(STATUS_MEDIA_CACHE_DIR)
