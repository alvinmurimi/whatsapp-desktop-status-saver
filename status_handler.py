import os
import shutil
import asyncio
from utils import get_all_status_files

def load_statuses(file_type, save_dir, page=1, items_per_page=20):
    try:
        if file_type == "downloads":
            all_files = [os.path.join(save_dir, f) for f in os.listdir(save_dir) if os.path.isfile(os.path.join(save_dir, f))]
        else:
            all_files = get_all_status_files(file_type)
        
        all_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        start = (page - 1) * items_per_page
        end = start + items_per_page
        return all_files[start:end]
    except Exception as e:
        print(f"Error loading statuses: {str(e)}")
        return []

async def download_status(file_path, dest_dir):
    try:
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        await asyncio.to_thread(shutil.copy, file_path, dest_dir)
        return f"Downloaded: {os.path.basename(file_path)} to {dest_dir}"
    except Exception as e:
        return f"Error downloading: {str(e)}"

async def delete_file(file_path):
    try:
        await asyncio.to_thread(os.remove, file_path)
        return f"Deleted: {os.path.basename(file_path)}"
    except Exception as e:
        return f"Error deleting: {str(e)}"