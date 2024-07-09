import os
import json
import platform

# Determine the operating system
SYSTEM = platform.system()

# Default paths and settings
if SYSTEM == "Windows":
    WHATSAPP_STATUS_PATH = os.path.expandvars(r'%userprofile%\AppData\Local\Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm\LocalState\shared\transfers')
    SETTINGS_DIR = os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'WhatsAppStatusSaver')
elif SYSTEM == "Darwin":  # macOS
    WHATSAPP_STATUS_PATH = os.path.expanduser('~/Library/Containers/net.whatsapp.WhatsApp/Data/Library/Application Support/WhatsApp/shared/transfers')
    SETTINGS_DIR = os.path.expanduser('~/Library/Application Support/WhatsAppStatusSaver')
else:
    raise NotImplementedError(f"Unsupported operating system: {SYSTEM}")
    

DEFAULT_SAVE_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', 'WhatsappStatuses')
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
THUMBNAIL_CACHE_DIR = os.path.join(SETTINGS_DIR, "thumbnail_cache")

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