import flet as ft
import os
import shutil
import datetime
import json
import io
import base64
from PIL import Image
import cv2
import asyncio
import hashlib
from functools import lru_cache

# Default paths and settings
WHATSAPP_STATUS_PATH = os.path.expandvars(r'%userprofile%\AppData\Local\Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm\LocalState\shared\transfers')
DEFAULT_SAVE_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', 'WhatsappStatuses')
SETTINGS_DIR = os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'WhatsAppStatusSaver')
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
THUMBNAIL_CACHE_DIR = os.path.join(SETTINGS_DIR, "thumbnail_cache")

if not os.path.exists(SETTINGS_DIR):
    os.makedirs(SETTINGS_DIR)

if not os.path.exists(THUMBNAIL_CACHE_DIR):
    os.makedirs(THUMBNAIL_CACHE_DIR)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return {"save_dir": DEFAULT_SAVE_DIR, "theme_mode": "light"}

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f)

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

async def main(page: ft.Page):
    page.title = "WhatsApp Status Saver"
    page.window.width = 1200
    page.window.height = 800
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.window.always_on_top = True
    page.window.title_bar_hidden = True
    page.window.title_bar_buttons_hidden = True

    settings = load_settings()
    save_dir = settings["save_dir"]
    page.theme_mode = settings.get("theme_mode", "light")

    async def download_status(file_path, dest_dir):
        try:
            if not os.path.exists(dest_dir):
                os.makedirs(dest_dir)
            await asyncio.to_thread(shutil.copy, file_path, dest_dir)
            page.snack_bar = ft.SnackBar(ft.Text(f"Downloaded: {os.path.basename(file_path)} to {dest_dir}"), open=True)
        except Exception as e:
            page.snack_bar = ft.SnackBar(ft.Text(f"Error downloading: {str(e)}"), open=True)
        page.update()

    async def delete_file(file_path):
        try:
            await asyncio.to_thread(os.remove, file_path)
            page.snack_bar = ft.SnackBar(ft.Text(f"Deleted: {os.path.basename(file_path)}"), open=True)
            await show_content(2)  # Refresh the downloads view
        except Exception as e:
            page.snack_bar = ft.SnackBar(ft.Text(f"Error deleting: {str(e)}"), open=True)
        page.update()

    def build_status_card(file_path, show_delete_button=False):
        file_name = os.path.basename(file_path)
        thumbnail_path = get_cached_thumbnail(file_path)
        
        return ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Image(
                            src=thumbnail_path,
                            width=140,
                            height=140,
                            fit=ft.ImageFit.COVER
                        ),
                        width=140,
                        height=140,
                    ),
                    ft.Row(
                        controls=[
                            ft.IconButton(
                                icon=ft.icons.DELETE if show_delete_button else ft.icons.SAVE_ALT,
                                icon_color=ft.colors.TEAL if show_delete_button else ft.colors.TEAL,
                                tooltip="Delete" if show_delete_button else "Download",
                                on_click=lambda _: asyncio.create_task(delete_file(file_path) if show_delete_button else download_status(file_path, save_dir)),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_EVENLY,
                    ),
                ],
                spacing=5,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            width=150,
            height=200,
            border=ft.border.all(1, ft.colors.with_opacity(0.1, ft.colors.TRANSPARENT)),
            border_radius=ft.border_radius.all(10),
            padding=5,
        )

    async def show_content(index, page_num=1):
        items_per_page = 20

        progress_bar = ft.ProgressBar(width=400)
        page_content.controls = [
            ft.Column([
                ft.Text("Loading...", style=ft.TextThemeStyle.TITLE_MEDIUM),
                progress_bar
            ], alignment=ft.MainAxisAlignment.CENTER)
        ]
        page.update()

        async def load_content():
            if index == 0:
                file_type = "photos"
                icon = ft.icons.PHOTO_CAMERA
            elif index == 1:
                file_type = "videos"
                icon = ft.icons.VIDEOCAM
            elif index == 2:
                file_type = "downloads"
                icon = ft.icons.FOLDER
            else:
                return

            files = await asyncio.to_thread(load_statuses, file_type, save_dir, page_num, items_per_page)
            
            if not files:
                page_content.controls = [ft.Text(f"No {file_type} available.")]
            else:
                grid_view = ft.GridView(
                    controls=[],
                    padding=10,
                    spacing=10,
                    run_spacing=10,
                    max_extent=200,
                    expand=True,
                )
                
                load_more_button = ft.ElevatedButton(
                    "Load More",
                    icon=icon,
                    on_click=lambda _: asyncio.create_task(load_more_content(index, page_num + 1))
                )

                page_content.controls = [grid_view, load_more_button]

                total_items = len(files)
                for i, file_path in enumerate(files):
                    grid_view.controls.append(build_status_card(file_path))
                    progress = (i + 1) / total_items
                    progress_bar.value = progress
                    if i % 5 == 0 or i == total_items - 1:
                        page.update()
                    await asyncio.sleep(0.01)

        if index == 3:
            show_settings()
        else:
            await load_content()

        page.update()

    async def load_more_content(index, next_page):
        items_per_page = 20
        file_type = "photos" if index == 0 else "videos" if index == 1 else "downloads"
        
        load_more_button = page_content.controls[-1]
        load_more_button.disabled = True
        page.update()

        new_items = await asyncio.to_thread(load_statuses, file_type, save_dir, next_page, items_per_page)
        
        if new_items:
            grid_view = page_content.controls[0]
            for file_path in new_items:
                grid_view.controls.append(build_status_card(file_path))
                await asyncio.sleep(0.01)
                page.update()
        
        if len(new_items) < items_per_page:
            page_content.controls.pop()
        else:
            load_more_button.disabled = False
        
        page.update()

    def show_settings():
        def on_save_click(e):
            new_save_dir = save_dir_input.value
            settings["save_dir"] = new_save_dir
            save_settings(settings)
            nonlocal save_dir
            save_dir = new_save_dir
            page.snack_bar = ft.SnackBar(ft.Text(f"Save directory updated to: {new_save_dir}"), open=True)
            page.go("/")

        def pick_directory_result(e: ft.FilePickerResultEvent):
            if e.path:
                save_dir_input.value = e.path
                save_dir_input.update()

        pick_directory_dialog = ft.FilePicker(on_result=pick_directory_result)
        save_dir_input = ft.TextField(value=save_dir, label="Save Directory", width=500, read_only=True)
        pick_directory_button = ft.ElevatedButton(text="Browse", on_click=lambda _: pick_directory_dialog.get_directory_path())

        save_button = ft.ElevatedButton(text="Update", on_click=on_save_click)

        page.overlay.append(pick_directory_dialog)

        page_content.controls = [ft.Column(
            controls=[ft.Row(controls=[save_dir_input, pick_directory_button], spacing=10), save_button],
            alignment=ft.MainAxisAlignment.START,
            spacing=20,
            expand=True
        )]
        page.update()

    def theme_changed(e):
        new_theme_mode = "dark" if page.theme_mode == "light" else "light"
        settings["theme_mode"] = new_theme_mode
        save_settings(settings)
        page.theme_mode = new_theme_mode
        page.update()

    if not os.path.exists(WHATSAPP_STATUS_PATH):
        page.add(ft.Text("WhatsApp status folder not found."))
        return

    async def on_tab_change(e):
        await show_content(e.control.selected_index)

    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=100,
        min_extended_width=400,
        group_alignment=-0.9,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.icons.PHOTO_CAMERA, selected_icon=ft.icons.PHOTO_CAMERA, label="Photos"
            ),
            ft.NavigationRailDestination(
                icon=ft.icons.VIDEOCAM, selected_icon=ft.icons.VIDEOCAM, label="Videos",
            ),
            ft.NavigationRailDestination(
                icon=ft.icons.FOLDER, selected_icon=ft.icons.FOLDER, label="Downloads",
            ),
            ft.NavigationRailDestination(
                icon=ft.icons.SETTINGS, selected_icon=ft.icons.SETTINGS, label="Settings",
            ),
        ],
        on_change=on_tab_change
    )

    page_content = ft.Column(
        alignment=ft.MainAxisAlignment.START,
        expand=True
    )

    LIGHT_SEED_COLOR = ft.colors.LIGHT_BLUE
    DARK_SEED_COLOR = ft.colors.DEEP_PURPLE

    page.theme = ft.theme.Theme(color_scheme_seed=LIGHT_SEED_COLOR, use_material3=True)
    page.dark_theme = ft.theme.Theme(color_scheme_seed=DARK_SEED_COLOR, use_material3=True)

    def maximize(e):
        page.window.maximized = not page.window.maximized
        page.update()

    def minimize(e):
        page.window.minimized = True
        page.update()

    def close(e):
        page.window.close()

    title_bar = ft.Container(
        content=ft.Row(
            [
                ft.WindowDragArea(
                    ft.Container(
                        content=ft.Text("WhatsApp Status Saver", style="headlineSmall"),
                        padding=ft.padding.Padding(10, 10, 10, 10),
                        expand=True,
                    ),
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.icons.WB_SUNNY_OUTLINED if page.theme_mode == "light" else ft.icons.WB_SUNNY,
                    on_click=theme_changed,
                    tooltip="Toggle Theme"
                ),
                ft.IconButton(icon=ft.icons.MINIMIZE, on_click=minimize),
                ft.IconButton(icon=ft.icons.CROP_DIN, on_click=maximize),
                ft.IconButton(icon=ft.icons.CLOSE, on_click=close),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            expand=True
        ),
        bgcolor=ft.colors.SURFACE,
        padding=ft.padding.Padding(10, 10, 10, 10),
    )

    page.add(
        ft.Column(
            [title_bar,
                ft.Row(
                    [
                        rail,
                        ft.VerticalDivider(width=1),
                        page_content
                    ],
                    expand=True,
                )
            ],
            expand=True
        )
    )

    await show_content(0)  # Load initial content
    return page

if __name__ == "__main__":
    asyncio.run(ft.app(target=main))