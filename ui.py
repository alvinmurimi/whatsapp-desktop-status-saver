import flet as ft
from dataclasses import dataclass
from datetime import datetime

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

TEXT_FONT_FAMILIES = {
    0: "Segoe UI",
    1: "Georgia",
    2: "Script MT Bold",
    3: "Comic Sans MS",
    4: "Arial",
}


def _argb_to_hex(value, default="#1B2A33"):
    if value is None:
        return default
    normalized = int(value) & 0xFFFFFFFF
    return f"#{(normalized >> 16) & 0xFF:02x}{(normalized >> 8) & 0xFF:02x}{normalized & 0xFF:02x}"


def _text_status_label(item):
    text_value = (item.text_value or "").strip() if isinstance(item, StatusRecord) else ""
    if text_value:
        return text_value
    if isinstance(item, StatusRecord) and item.text_subtype == "url":
        return "Link preview"
    if isinstance(item, StatusRecord):
        return _format_status_timestamp(getattr(item, "timestamp", None))
    return "Recent status"


def _display_text_status_label(item):
    label = _text_status_label(item)
    if isinstance(item, StatusRecord) and item.text_subtype == "url":
        for marker in ("/", "?", "&", "=", "-", "_", ".", "#"):
            label = label.replace(marker, f"{marker}\u200b")
    return label


def _text_status_subtitle(item):
    if not isinstance(item, StatusRecord):
        return None
    if (item.text_value or "").strip():
        return "Link status" if item.text_subtype == "url" else None
    return "Link status" if item.text_subtype == "url" else "Text status"


def _text_status_footer(item):
    if not isinstance(item, StatusRecord):
        return None
    if item.music_title and item.music_artist:
        return f"{item.music_title} - {item.music_artist}"
    if item.music_title:
        return item.music_title
    return None


def _text_font_family(item):
    if not isinstance(item, StatusRecord):
        return TEXT_FONT_FAMILIES[0]
    return TEXT_FONT_FAMILIES.get(item.font_id or 0, TEXT_FONT_FAMILIES[0])


def _format_status_timestamp(timestamp):
    if not timestamp:
        return "Recent status"
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime("%I:%M %p").lstrip("0")
    except (OSError, OverflowError, ValueError):
        return "Recent status"


def _build_text_preview(item):
    subtitle = _text_status_subtitle(item)
    footer = _text_status_footer(item)
    raw_label = _text_status_label(item)
    label = _display_text_status_label(item)
    font_family = _text_font_family(item)
    is_link = isinstance(item, StatusRecord) and item.text_subtype == "url"
    title_size = 13 if is_link else 18
    title_weight = ft.FontWeight.W_500 if font_family == "Script MT Bold" else ft.FontWeight.W_600
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    label,
                    size=title_size,
                    color=_argb_to_hex(getattr(item, "text_color", None), "#FFFFFF"),
                    text_align=ft.TextAlign.CENTER,
                    weight=title_weight,
                    font_family=font_family,
                    selectable=True,
                    tooltip=raw_label,
                    max_lines=8 if is_link else 7,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                *(
                    [
                        ft.Text(
                            subtitle,
                            size=11,
                            color=ft.Colors.with_opacity(0.82, _argb_to_hex(getattr(item, "text_color", None), "#FFFFFF")),
                            text_align=ft.TextAlign.CENTER,
                            font_family=font_family,
                        )
                    ]
                    if subtitle
                    else []
                ),
                *(
                    [
                        ft.Text(
                            footer,
                            size=10,
                            color=ft.Colors.with_opacity(0.72, _argb_to_hex(getattr(item, "text_color", None), "#FFFFFF")),
                            text_align=ft.TextAlign.CENTER,
                            selectable=True,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        )
                    ]
                    if footer
                    else []
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
            spacing=8,
        ),
        width=160,
        height=160,
        bgcolor=_argb_to_hex(getattr(item, "background_color", None), "#1B2A33"),
        border_radius=ft.BorderRadius.all(12),
        padding=16,
        alignment=ft.Alignment(0, 0),
    )


def _build_music_badge(item):
    if not isinstance(item, StatusRecord):
        return None
    title = getattr(item, "music_title", None)
    artist = getattr(item, "music_artist", None)
    if not title and not artist:
        return None
    label = title or "Music"
    if artist:
        label = f"{label} - {artist}"
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.MUSIC_NOTE, size=12, color=ft.Colors.ON_PRIMARY_CONTAINER),
                ft.Container(
                    content=ft.Text(
                        label,
                        size=10,
                        color=ft.Colors.ON_PRIMARY_CONTAINER,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        tooltip=label,
                    ),
                    expand=True,
                ),
            ],
            spacing=4,
            tight=False,
        ),
        bgcolor=ft.Colors.with_opacity(0.92, ft.Colors.PRIMARY_CONTAINER),
        border_radius=999,
        padding=ft.padding.symmetric(horizontal=8, vertical=4),
        left=8,
        right=8,
        bottom=8,
        tooltip=label,
    )


def _build_preview_content(item, file_path, thumbnail_path):
    if thumbnail_path:
        return ft.Image(
            src=thumbnail_path,
            width=160,
            height=160,
            fit=ft.BoxFit.COVER,
        )

    if isinstance(item, StatusRecord) and item.kind == "texts":
        return _build_text_preview(item)

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
    on_copy_text=None,
):
    preview_host = ft.Container(
        width=160,
        height=160,
        border_radius=ft.BorderRadius.all(12),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        ink=not (isinstance(item, StatusRecord) and item.kind == "texts"),
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
        preview_base = _build_preview_content(item, file_path, thumbnail_path)
        music_badge = _build_music_badge(item)
        preview_host.content = (
            ft.Stack([preview_base, music_badge], expand=True)
            if music_badge
            else preview_base
        )

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

    if not (isinstance(item, StatusRecord) and item.kind == "texts"):
        preview_host.on_click = handle_open_click
    refresh_preview()

    action_icon = ft.Icons.DELETE if is_download_section else ft.Icons.SAVE_ALT
    action_color = ft.Colors.ERROR if is_download_section else ft.Colors.PRIMARY
    action_tip = "Delete" if is_download_section else "Save"

    async def handle_copy_click(_):
        if not isinstance(item, StatusRecord):
            return
        text_value = (item.text_value or "").strip()
        if not text_value:
            on_action("Nothing to copy from this text status")
            return
        if on_copy_text:
            await on_copy_text(text_value)
        else:
            on_action("Copy is not available")

    action_row_controls = []
    if isinstance(item, StatusRecord) and item.kind == "texts" and not is_download_section:
        copy_tip = "Copy link" if item.text_subtype == "url" else "Copy text"
        save_tip = "Save as image"
        action_row_controls = [
            ft.IconButton(
                icon=ft.Icons.CONTENT_COPY,
                icon_color=ft.Colors.PRIMARY,
                icon_size=18,
                tooltip=copy_tip,
                on_click=handle_copy_click,
                style=ft.ButtonStyle(padding=ft.padding.all(4)),
            ),
            ft.IconButton(
                icon=ft.Icons.SAVE_ALT,
                icon_color=ft.Colors.PRIMARY,
                icon_size=18,
                tooltip=save_tip,
                on_click=handle_button_click,
                style=ft.ButtonStyle(padding=ft.padding.all(4)),
            ),
        ]
    else:
        action_row_controls = [
            ft.IconButton(
                icon=action_icon,
                icon_color=action_color,
                icon_size=18,
                tooltip=action_tip,
                on_click=handle_button_click,
                style=ft.ButtonStyle(padding=ft.padding.all(4)),
            )
        ]

    control = ft.Container(
        content=ft.Column(
            [
                preview_host,
                ft.Container(
                    content=ft.Row(
                        action_row_controls,
                        alignment=ft.MainAxisAlignment.CENTER,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=0,
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
    """Inline chip with a real browser logo at the same height as the source pill."""
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


def build_loading_state(message="Loading statuses...", detail=None):
    detail_control = None
    if detail:
        detail_control = ft.Text(
            detail,
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
            text_align=ft.TextAlign.CENTER,
        )
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
            ]
            + ([detail_control] if detail_control else []),
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
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
                icon=ft.Icons.FORMAT_QUOTE,
                selected_icon=ft.Icons.FORMAT_QUOTE,
                label="Texts",
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
