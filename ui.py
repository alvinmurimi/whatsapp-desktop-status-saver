import flet as ft
from dataclasses import dataclass

from status_handler import (
    delete_file,
    download_status,
    get_status_preview_path,
    open_status_item,
)
from utils import get_cached_thumbnail, get_existing_thumbnail
import asyncio
from webview_status_source import StatusRecord

@dataclass
class StatusCardHandle:
    control: ft.Container
    refresh_preview: callable


def _build_preview_content(item, file_path, thumbnail_path):
    if thumbnail_path:
        return ft.Image(
            src=thumbnail_path,
            width=140,
            height=140,
            fit=ft.BoxFit.COVER,
        )

    is_video = False
    if isinstance(item, StatusRecord):
        is_video = item.kind == "videos"
    elif isinstance(file_path, str):
        is_video = file_path.lower().endswith((".mp4", ".avi", ".mov"))

    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(
                    name=ft.Icons.PLAY_CIRCLE_FILL if is_video else ft.Icons.IMAGE_OUTLINED,
                    size=40,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Text(
                    "Preview loading" if file_path else "Tap save to fetch",
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
            spacing=8,
        ),
        width=140,
        height=140,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        border_radius=ft.BorderRadius.all(8),
        alignment=ft.Alignment(0, 0),
    )


def build_status_card(
    item,
    is_download_section,
    save_dir,
    on_action,
    on_delete=None,
    eager_thumbnail=True,
):
    preview_host = ft.Container(
        width=140,
        height=140,
        border_radius=ft.BorderRadius.all(8),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        ink=True,
    )

    def refresh_preview():
        file_path = get_status_preview_path(item)
        thumbnail_path = None
        if file_path:
            thumbnail_path = (
                get_cached_thumbnail(file_path)
                if eager_thumbnail
                else get_existing_thumbnail(file_path)
            )
        preview_host.content = _build_preview_content(item, file_path, thumbnail_path)

    async def handle_button_click(_):
        try:
            if is_download_section:
                file_path = get_status_preview_path(item)
                result = await delete_file(file_path)
                if "Deleted" in result and on_delete:
                    await on_delete()
            else:
                result = await download_status(item, save_dir)
            on_action(result)
        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            on_action(error_message)

    async def handle_open_click(_):
        result = await open_status_item(item)
        if result.startswith("Error"):
            on_action(result)

    preview_host.on_click = handle_open_click
    refresh_preview()

    control = ft.Container(
        content=ft.Column(
            [
                preview_host,
                ft.Row(
                    controls=[
                        ft.IconButton(
                            icon=ft.Icons.DELETE if is_download_section else ft.Icons.SAVE_ALT,
                            icon_color=ft.Colors.RED if is_download_section else ft.Colors.TEAL,
                            tooltip="Delete" if is_download_section else "Download",
                            on_click=handle_button_click,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_EVENLY,
                ),
            ],
            spacing=5,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        width=150,
        border=ft.Border.all(1, ft.Colors.with_opacity(0.1, ft.Colors.TRANSPARENT)),
        border_radius=ft.BorderRadius.all(10),
        padding=5,
    )
    return StatusCardHandle(control=control, refresh_preview=refresh_preview)

def create_title_bar(page, theme_changed):
    return ft.Container(
        content=ft.Row(
            [
                ft.Container(
                    content=ft.Text(
                        "WhatsApp Status Saver",
                        theme_style=ft.TextThemeStyle.HEADLINE_SMALL,
                        color=ft.Colors.ON_SURFACE,
                    ),
                    padding=ft.padding.Padding(10, 10, 10, 10),
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.Icons.WB_SUNNY_OUTLINED if page.theme_mode == ft.ThemeMode.LIGHT else ft.Icons.WB_SUNNY,
                    icon_color=ft.Colors.ON_SURFACE,
                    on_click=theme_changed,
                    tooltip="Toggle Theme"
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            expand=True
        ),
        bgcolor=ft.Colors.SURFACE,
        padding=ft.padding.Padding(10, 10, 10, 10),
    )

def create_navigation_rail(on_tab_change):
    return ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=100,
        min_extended_width=400,
        group_alignment=-0.9,
        bgcolor=ft.Colors.SURFACE,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.PHOTO_CAMERA, selected_icon=ft.Icons.PHOTO_CAMERA, label="Photos"
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.VIDEOCAM, selected_icon=ft.Icons.VIDEOCAM, label="Videos",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.FOLDER, selected_icon=ft.Icons.FOLDER, label="Downloads",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SETTINGS, selected_icon=ft.Icons.SETTINGS, label="Settings",
            ),
        ],
        on_change=on_tab_change
    )
