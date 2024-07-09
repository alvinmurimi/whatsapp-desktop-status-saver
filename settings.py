import os
import json

DEFAULT_SAVE_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', 'WhatsappStatuses')
SETTINGS_DIR = os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'WhatsAppStatusSaver')
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
