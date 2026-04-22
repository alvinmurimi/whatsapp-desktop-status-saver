import flet as ft
from dataclasses import dataclass

from status_handler import (
    delete_file,
    download_status,
    get_status_preview_path,
    open_status_item,
)
from utils import get_cached_thumbnail, get_existing_thumbnail
from webview_status_source import StatusRecord


@dataclass
class StatusCardHandle:
    control: ft.Container
    refresh_preview: callable


BROWSER_ICON_PATHS = {
    "chrome": "browsers/chrome.png",
    "edge": "browsers/edge.png",
    "firefox": "browsers/firefox.png",
}


def _build_preview_content(item, file_path, thumbnail_path):
    if thumbnail_path:
        return ft.Image(
            src=thumbnail_path,
            width=160,
            height=160,
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
                    ft.Icons.PLAY_CIRCLE_FILL if is_video else ft.Icons.IMAGE_OUTLINED,
                    size=32,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    opacity=0.45,
                ),
                ft.Text(
                    "Preview loading" if file_path else "Tap save to fetch",
                    size=10,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
            spacing=6,
        ),
        width=160,
        height=160,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        border_radius=ft.BorderRadius.all(12),
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
        width=160,
        height=160,
        border_radius=ft.BorderRadius.all(12),
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
            on_action(f"An error occurred: {str(e)}")

    async def handle_open_click(_):
        result = await open_status_item(item)
        if result.startswith("Error"):
            on_action(result)

    preview_host.on_click = handle_open_click
    refresh_preview()

    action_icon = ft.Icons.DELETE if is_download_section else ft.Icons.SAVE_ALT
    action_color = ft.Colors.ERROR if is_download_section else ft.Colors.PRIMARY
    action_tip = "Delete" if is_download_section else "Save"

    control = ft.Container(
        content=ft.Column(
            [
                preview_host,
                ft.Container(
                    content=ft.IconButton(
                        icon=action_icon,
                        icon_color=action_color,
                        icon_size=18,
                        tooltip=action_tip,
                        on_click=handle_button_click,
                        style=ft.ButtonStyle(padding=ft.padding.all(4)),
                    ),
                    alignment=ft.Alignment(0, 0),
                    height=34,
                ),
            ],
            spacing=4,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        width=172,
        border_radius=ft.BorderRadius.all(16),
        padding=ft.padding.symmetric(vertical=8, horizontal=6),
        bgcolor=ft.Colors.SURFACE_CONTAINER,
    )
    return StatusCardHandle(control=control, refresh_preview=refresh_preview)


def build_browser_icon_button(browser_id, label, selected, on_click):
    """Inline chip with real browser SVG logo — same height as the source pill."""
    path = BROWSER_ICON_PATHS.get(browser_id)
    if path:
        icon = ft.Image(src=path, width=20, height=20, fit=ft.BoxFit.CONTAIN)
    else:
        icon = ft.Container(
            content=ft.Text(browser_id[0].upper(), size=11, color="white", weight=ft.FontWeight.BOLD),
            width=20, height=20, bgcolor="#666", border_radius=10, alignment=ft.Alignment(0, 0),
        )

    return ft.Container(
        content=ft.Row([icon, ft.Text(label, size=13)], spacing=6, tight=True),
        padding=ft.padding.symmetric(horizontal=14, vertical=9),
        border_radius=20,
        border=ft.border.all(2, ft.Colors.PRIMARY if selected else ft.Colors.TRANSPARENT),
        bgcolor=ft.Colors.PRIMARY_CONTAINER if selected else ft.Colors.SURFACE_CONTAINER_HIGH,
        on_click=on_click,
        ink=True,
        tooltip=f"Use {label}",
    )


def build_loading_state(message="Loading statuses..."):
    return ft.Container(
        content=ft.Column(
            [
                ft.ProgressRing(width=36, height=36, stroke_width=3),
                ft.Text(
                    message,
                    theme_style=ft.TextThemeStyle.BODY_MEDIUM,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=16,
            tight=True,
        ),
        expand=True,
        alignment=ft.Alignment(0, 0),
    )


def build_empty_state(message, icon=ft.Icons.PHOTO_LIBRARY_OUTLINED):
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(icon, size=56, color=ft.Colors.ON_SURFACE_VARIANT, opacity=0.3),
                ft.Text(
                    "Nothing here yet",
                    theme_style=ft.TextThemeStyle.TITLE_MEDIUM,
                    color=ft.Colors.ON_SURFACE,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Text(
                    message,
                    theme_style=ft.TextThemeStyle.BODY_SMALL,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
            tight=True,
        ),
        expand=True,
        alignment=ft.Alignment(0, 0),
        padding=ft.padding.symmetric(horizontal=48),
    )


def build_unavailable_state(title, body, detail_lines):
    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.WIFI_OFF, color=ft.Colors.ERROR, size=20),
                        ft.Text(
                            title,
                            theme_style=ft.TextThemeStyle.TITLE_MEDIUM,
                            color=ft.Colors.ON_SURFACE,
                            weight=ft.FontWeight.W_600,
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(body, color=ft.Colors.ON_SURFACE_VARIANT, size=13),
                ft.Container(
                    content=ft.Text(
                        "\n".join(detail_lines),
                        selectable=True,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        size=12,
                    ),
                    padding=ft.padding.symmetric(horizontal=14, vertical=10),
                    border_radius=8,
                    bgcolor=ft.Colors.SURFACE_CONTAINER,
                    border=ft.border.all(1, ft.Colors.with_opacity(0.1, ft.Colors.ON_SURFACE)),
                ),
            ],
            spacing=12,
            tight=True,
        ),
        padding=ft.padding.symmetric(horizontal=24, vertical=20),
        margin=ft.margin.only(top=20, left=20, right=20),
        border_radius=16,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        border=ft.border.all(1, ft.Colors.with_opacity(0.07, ft.Colors.ON_SURFACE)),
    )


def create_title_bar(page, refresh_current_view, theme_icon_ref):
    return ft.Container(
        content=ft.Row(
            [
                ft.Row(
                    [
                        ft.Container(
                            content=ft.Icon(
                                ft.Icons.PHOTO_LIBRARY_OUTLINED,
                                color=ft.Colors.PRIMARY,
                                size=18,
                            ),
                            width=34,
                            height=34,
                            bgcolor=ft.Colors.PRIMARY_CONTAINER,
                            border_radius=10,
                            alignment=ft.Alignment(0, 0),
                        ),
                        ft.Text(
                            "Status Saver",
                            theme_style=ft.TextThemeStyle.TITLE_MEDIUM,
                            color=ft.Colors.ON_SURFACE,
                            weight=ft.FontWeight.W_600,
                        ),
                    ],
                    spacing=10,
                ),
                ft.Row(
                    [
                        ft.IconButton(
                            icon=ft.Icons.REFRESH,
                            icon_color=ft.Colors.ON_SURFACE_VARIANT,
                            on_click=refresh_current_view,
                            tooltip="Refresh",
                            icon_size=20,
                        ),
                        theme_icon_ref,
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        bgcolor=ft.Colors.SURFACE,
        padding=ft.padding.symmetric(horizontal=16, vertical=10),
        border=ft.border.only(
            bottom=ft.BorderSide(1, ft.Colors.with_opacity(0.08, ft.Colors.ON_SURFACE))
        ),
    )


def create_navigation_rail(on_tab_change):
    return ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=80,
        group_alignment=-0.9,
        bgcolor=ft.Colors.SURFACE,
        indicator_color=ft.Colors.PRIMARY_CONTAINER,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.PHOTO_CAMERA,
                selected_icon=ft.Icons.PHOTO_CAMERA,
                label="Photos",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.VIDEOCAM,
                selected_icon=ft.Icons.VIDEOCAM,
                label="Videos",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.FOLDER,
                selected_icon=ft.Icons.FOLDER,
                label="Saved",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SETTINGS,
                selected_icon=ft.Icons.SETTINGS,
                label="Settings",
            ),
        ],
        on_change=on_tab_change,
    )
