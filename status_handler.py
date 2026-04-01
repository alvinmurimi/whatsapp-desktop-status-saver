import os
import shutil
import asyncio
import concurrent.futures
import subprocess
import sys
from utils import get_all_status_files
from webview_status_source import (
    StatusRecord,
    ensure_record_cached,
    get_cached_record_path,
    get_webview_status_files,
    get_webview_status_records,
)

def _paginate(files, page=1, items_per_page=None):
    if items_per_page is None or items_per_page <= 0:
        return files

    start = max(0, (page - 1) * items_per_page)
    end = start + items_per_page
    return files[start:end]


def load_statuses(file_type, save_dir, page=1, items_per_page=None, materialize=True):
    try:
        if file_type == "downloads":
            if not os.path.isdir(save_dir):
                return []

            all_files = [
                os.path.join(save_dir, f)
                for f in os.listdir(save_dir)
                if os.path.isfile(os.path.join(save_dir, f))
            ]
            all_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return _paginate(all_files, page=page, items_per_page=items_per_page)

        webview_records = get_webview_status_records(
            file_type,
            page=page,
            items_per_page=items_per_page,
        )
        if webview_records:
            if materialize:
                return get_webview_status_files(
                    file_type,
                    page=page,
                    items_per_page=items_per_page,
                )
            return webview_records

        all_files = get_all_status_files(file_type)
        all_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        return _paginate(all_files, page=page, items_per_page=items_per_page)
    except Exception as e:
        print(f"Error loading statuses: {str(e)}")
        return []


def count_statuses(file_type, save_dir):
    try:
        if file_type == "downloads":
            if not os.path.isdir(save_dir):
                return 0
            return sum(
                1
                for file_name in os.listdir(save_dir)
                if os.path.isfile(os.path.join(save_dir, file_name))
            )

        webview_records = get_webview_status_records(file_type, page=1, items_per_page=None)
        if webview_records:
            return len(webview_records)

        return len(get_all_status_files(file_type))
    except Exception as e:
        print(f"Error counting statuses: {str(e)}")
        return 0

def get_status_preview_path(item):
    if isinstance(item, StatusRecord):
        return get_cached_record_path(item)
    return item if isinstance(item, str) and os.path.exists(item) else None


def warm_status_previews(items):
    warmed_paths = []
    records = [item for item in items if isinstance(item, StatusRecord)]
    direct_paths = [item for item in items if isinstance(item, str) and os.path.exists(item)]

    if records:
        worker_count = min(6, len(records))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            for cached_path in executor.map(ensure_record_cached, records):
                if cached_path:
                    warmed_paths.append(cached_path)

    warmed_paths.extend(direct_paths)
    return warmed_paths


def get_status_item_key(item):
    if isinstance(item, StatusRecord):
        return item.status_id
    if isinstance(item, str):
        return item
    return str(item)


async def download_status(file_path, dest_dir):
    try:
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        source_path = file_path
        if isinstance(file_path, StatusRecord):
            source_path = await asyncio.to_thread(ensure_record_cached, file_path)
            if not source_path:
                return "Error downloading: could not fetch the selected status"

        await asyncio.to_thread(shutil.copy, source_path, dest_dir)
        return f"Downloaded: {os.path.basename(source_path)} to {dest_dir}"
    except Exception as e:
        return f"Error downloading: {str(e)}"


async def open_status_item(item):
    try:
        file_path = item
        if isinstance(item, StatusRecord):
            file_path = await asyncio.to_thread(ensure_record_cached, item)
            if not file_path:
                return "Error opening: could not fetch the selected status"

        if not isinstance(file_path, str) or not os.path.exists(file_path):
            return "Error opening: file not found"

        if sys.platform.startswith("win"):
            await asyncio.to_thread(os.startfile, file_path)
        elif sys.platform == "darwin":
            await asyncio.to_thread(subprocess.run, ["open", file_path], check=True)
        else:
            await asyncio.to_thread(subprocess.run, ["xdg-open", file_path], check=True)

        return f"Opened: {os.path.basename(file_path)}"
    except Exception as e:
        return f"Error opening: {str(e)}"

async def delete_file(file_path):
    try:
        await asyncio.to_thread(os.remove, file_path)
        return f"Deleted: {os.path.basename(file_path)}"
    except Exception as e:
        return f"Error deleting: {str(e)}"
