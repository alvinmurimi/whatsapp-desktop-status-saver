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
CHROME_USER_DATA_ROOT = os.path.expandvars(
    r"%LOCALAPPDATA%\Google\Chrome\User Data"
)
EDGE_USER_DATA_ROOT = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\Edge\User Data"
)
FIREFOX_PROFILE_ROOT = os.path.expandvars(
    r"%APPDATA%\Mozilla\Firefox\Profiles"
)
WINDOWS_WEB_BROWSER_ROOTS = {
    "chrome": CHROME_USER_DATA_ROOT,
    "edge": EDGE_USER_DATA_ROOT,
    "firefox": FIREFOX_PROFILE_ROOT,
}
WINDOWS_WEB_BROWSER_LABELS = {
    "chrome": "Chrome",
    "edge": "Edge",
    "firefox": "Firefox",
}


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


def get_supported_web_browsers():
    if SYSTEM != "Windows":
        return []
    return list(WINDOWS_WEB_BROWSER_ROOTS.keys())


def get_web_browser_label(browser):
    return WINDOWS_WEB_BROWSER_LABELS.get(browser, browser.title())


def _get_web_browser_root(browser):
    return WINDOWS_WEB_BROWSER_ROOTS.get(browser, "")


def _get_browser_profile_dirs(browser):
    browser_root = _get_web_browser_root(browser)
    if SYSTEM != "Windows" or not os.path.isdir(browser_root):
        return []

    profile_dirs = []
    for entry in os.scandir(browser_root):
        if not entry.is_dir():
            continue
        if browser == "firefox":
            profile_dirs.append(entry.path)
            continue
        if entry.name == "Default" or entry.name.startswith("Profile "):
            profile_dirs.append(entry.path)
    return sorted(profile_dirs)


def _browser_whatsapp_paths(browser, profile_dir):
    profile_name = os.path.basename(profile_dir)
    if browser == "firefox":
        origin_dir = os.path.join(
            profile_dir,
            "storage",
            "default",
            "https+++web.whatsapp.com",
        )
        indexeddb_dir = os.path.join(origin_dir, "idb")
        blob_dir = os.path.join(origin_dir, "cache")
    else:
        indexeddb_dir = os.path.join(
            profile_dir,
            "IndexedDB",
            "https_web.whatsapp.com_0.indexeddb.leveldb",
        )
        blob_dir = os.path.join(
            profile_dir,
            "IndexedDB",
            "https_web.whatsapp.com_0.indexeddb.blob",
        )
    return {
        "browser": browser,
        "browser_label": get_web_browser_label(browser),
        "profile_name": profile_name,
        "profile_dir": profile_dir,
        "indexeddb_dir": indexeddb_dir,
        "blob_dir": blob_dir,
        "available": os.path.isdir(indexeddb_dir),
    }


def get_web_profiles(browser):
    profiles = [
        _browser_whatsapp_paths(browser, profile_dir)
        for profile_dir in _get_browser_profile_dirs(browser)
    ]
    profiles.sort(
        key=lambda source: os.path.getmtime(source["indexeddb_dir"])
        if os.path.isdir(source["indexeddb_dir"])
        else 0,
        reverse=True,
    )
    return profiles


def get_browser_whatsapp_sources(browser):
    return [profile for profile in get_web_profiles(browser) if profile["available"]]


def get_preferred_web_profile(browser, selected_profile_name=None):
    profiles = get_web_profiles(browser)
    if selected_profile_name:
        for profile in profiles:
            if profile["profile_name"] == selected_profile_name:
                return profile

    available_profiles = [profile for profile in profiles if profile["available"]]
    if available_profiles:
        return available_profiles[0]

    if profiles:
        return profiles[0]

    default_profile_dir = os.path.join(_get_web_browser_root(browser), "Default")
    return _browser_whatsapp_paths(browser, default_profile_dir)


def get_status_source_config(
    source_mode,
    selected_web_browser="chrome",
    selected_web_profile=None,
):
    if source_mode == "desktop":
        return {
            "key": "desktop",
            "label": "WhatsApp Desktop",
            "indexeddb_dir": WHATSAPP_WEBVIEW_INDEXEDDB_DIR,
            "blob_dir": WHATSAPP_WEBVIEW_BLOB_DIR,
            "legacy_status_path": WHATSAPP_STATUS_PATH,
            "profile_name": None,
        }

    if source_mode == "web":
        web_source = get_preferred_web_profile(selected_web_browser, selected_web_profile)
        return {
            "key": f"{web_source['browser']}-{web_source['profile_name'].lower().replace(' ', '-')}",
            "label": f"WhatsApp Web ({web_source['browser_label']})",
            "indexeddb_dir": web_source["indexeddb_dir"],
            "blob_dir": web_source["blob_dir"],
            "legacy_status_path": "",
            "profile_name": web_source["profile_name"],
            "profile_dir": web_source["profile_dir"],
            "available": web_source["available"],
            "browser": web_source["browser"],
            "browser_label": web_source["browser_label"],
        }

    raise ValueError(f"Unknown source mode: {source_mode}")


def get_status_source_diagnostics(
    source_mode,
    selected_web_browser="chrome",
    selected_web_profile=None,
):
    if source_mode == "desktop":
        legacy_exists = os.path.exists(WHATSAPP_STATUS_PATH)
        webview_exists = os.path.isdir(WHATSAPP_WEBVIEW_INDEXEDDB_DIR)
        return {
            "mode": "desktop",
            "label": "WhatsApp Desktop",
            "available": legacy_exists or webview_exists,
            "legacy_exists": legacy_exists,
            "webview_exists": webview_exists,
            "selected_status_path": WHATSAPP_STATUS_PATH,
            "webview_indexeddb_dir": WHATSAPP_WEBVIEW_INDEXEDDB_DIR,
        }

    if source_mode == "web":
        browser_root = _get_web_browser_root(selected_web_browser)
        browser_profiles = get_web_profiles(selected_web_browser)
        browser_sources = [profile for profile in browser_profiles if profile["available"]]
        selected_source = get_preferred_web_profile(selected_web_browser, selected_web_profile)
        return {
            "mode": "web",
            "label": f"WhatsApp Web ({selected_source['browser_label']})",
            "available": bool(selected_source["available"]),
            "browser": selected_source["browser"],
            "browser_label": selected_source["browser_label"],
            "browser_installed": os.path.isdir(browser_root),
            "profile_count": len(browser_profiles),
            "profile_name": selected_source["profile_name"],
            "profile_dir": selected_source["profile_dir"],
            "indexeddb_dir": selected_source["indexeddb_dir"],
            "blob_dir": selected_source["blob_dir"],
            "profiles": browser_profiles,
            "profiles_with_whatsapp": len(browser_sources),
        }

    raise ValueError(f"Unknown source mode: {source_mode}")


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
    defaults = {
        "save_dir": DEFAULT_SAVE_DIR,
        "theme_mode": "light",
        "discovery_source": "desktop",
        "web_browser": "chrome",
        "web_profile": "",
    }
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            saved = json.load(f)
            if isinstance(saved, dict):
                defaults.update(saved)
    return defaults

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
