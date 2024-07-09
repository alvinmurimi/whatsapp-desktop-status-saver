import flet as ft
from status_handler import download_status, delete_file, load_statuses
from utils import get_cached_thumbnail
import asyncio
import os

def build_status_card(file_path, is_download_section, save_dir, on_action):
    file_name = os.path.basename(file_path)
    thumbnail_path = get_cached_thumbnail(file_path)
    
    async def handle_button_click(_):
        if is_download_section:
            result = await delete_file(file_path)
        else:
            result = await download_status(file_path, save_dir)
        on_action(result)

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
                            icon=ft.icons.DELETE if is_download_section else ft.icons.SAVE_ALT,
                            icon_color=ft.colors.RED if is_download_section else ft.colors.TEAL,
                            tooltip="Delete" if is_download_section else "Download",
                            on_click=handle_button_click,
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

def create_title_bar(page, theme_changed):
    def maximize(e):
        page.window.maximized = not page.window.maximized
        page.update()

    def minimize(e):
        page.window.minimized = True
        page.update()

    def close(e):
        page.window.close()

    return ft.Container(
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

def create_navigation_rail(on_tab_change):
    return ft.NavigationRail(
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