import os
import datetime
import hashlib
from PIL import Image
import cv2
from functools import lru_cache
from config import WHATSAPP_STATUS_PATH
from config import THUMBNAIL_CACHE_DIR

@lru_cache(maxsize=1)
def get_all_status_files(file_type):
    now = datetime.datetime.now()
    yesterday = now - datetime.timedelta(days=1)
    status_files = []
    
    for root, dirs, files in os.walk(WHATSAPP_STATUS_PATH):
        for file in files:
            if (file_type == 'photos' and file.startswith('IMG-') and file.endswith('.jpg')) or \
               (file_type == 'videos' and file.startswith('VID-') and file.endswith('.mp4')):
                file_path = os.path.join(root, file)
                file_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
                if yesterday <= file_time <= now:
                    status_files.append(file_path)
    
    status_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return status_files

def get_cached_thumbnail(file_path, size=(150, 150)):
    file_hash = hashlib.md5(file_path.encode()).hexdigest()
    cache_file = os.path.join(THUMBNAIL_CACHE_DIR, f"{file_hash}_{size[0]}x{size[1]}.png")
    
    if os.path.exists(cache_file):
        return cache_file
    
    thumbnail = create_thumbnail(file_path, size)
    if thumbnail:
        thumbnail.save(cache_file, "PNG")
        return cache_file
    
    return None

def create_thumbnail(file_path, size=(150, 150)):
    if file_path.lower().endswith(('.png', '.jpg', '.jpeg')):
        with Image.open(file_path) as img:
            img.thumbnail(size)
            return img
    elif file_path.lower().endswith(('.mp4', '.avi', '.mov')):
        cap = cv2.VideoCapture(file_path)
        ret, frame = cap.read()
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame)
            img.thumbnail(size)
            cap.release()
            return img
        cap.release()
    return None