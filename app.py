import flet as ft
import os
import asyncio
from config import (
    load_settings,
    save_settings,
    WHATSAPP_STATUS_PATH,
    THUMBNAIL_CACHE_DIR,
    get_whatsapp_storage_diagnostics,
)
from ui import build_status_card, create_title_bar, create_navigation_rail
from status_handler import count_statuses, load_statuses, warm_status_previews
from utils import get_cached_thumbnail
from webview_status_source import has_webview_status_source


def show_snack_bar(page, message):
    normalized_message = message.strip()
    lowered_message = normalized_message.lower()

    duration = 1400
    if lowered_message.startswith("downloaded:"):
        display_message = normalized_message.split(" to ", 1)[0].replace(
            "Downloaded:", "Saved",
        ).strip()
    elif lowered_message.startswith("deleted:"):
        display_message = normalized_message.replace("Deleted:", "Removed").strip()
    elif lowered_message.startswith("error"):
        display_message = normalized_message
        duration = 2600
    else:
        display_message = normalized_message

    page.snack_bar = ft.SnackBar(
        content=ft.Text(display_message),
        open=True,
        duration=duration,
        behavior=ft.SnackBarBehavior.FLOATING,
        dismiss_direction=ft.DismissDirection.DOWN,
        show_close_icon=False,
    )
    page.update()
        
async def main(page: ft.Page):
    MEDIA_BATCH_SIZE = 24
    PREVIEW_BATCH_SIZE = 8
    LOAD_MORE_THRESHOLD_PX = 280

    page.title = "WhatsApp Status Saver"
    page.window.width = 1200
    page.window.height = 800
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.window.always_on_top = False
    page.window.title_bar_hidden = False
    page.window.title_bar_buttons_hidden = False
    page.padding = 0
    page.spacing = 0
    page.bgcolor = ft.Colors.SURFACE

    settings = load_settings()
    save_dir = settings["save_dir"]
    page.theme_mode = (
        ft.ThemeMode.DARK
        if settings.get("theme_mode", "light") == "dark"
        else ft.ThemeMode.LIGHT
    )

    page_content = ft.Column(
        alignment=ft.MainAxisAlignment.START,
        expand=True,
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        scroll_interval=80,
    )
    page_content.controls = [
        ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "Loading statuses...",
                        theme_style=ft.TextThemeStyle.TITLE_MEDIUM,
                        color=ft.Colors.ON_SURFACE,
                    ),
                    ft.ProgressBar(width=400),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                tight=True,
            ),
            expand=True,
            alignment=ft.Alignment(0, 0),
            bgcolor=ft.Colors.SURFACE,
        )
    ]
    pick_directory_dialog = ft.FilePicker()
    if pick_directory_dialog not in page.services:
        page.services.append(pick_directory_dialog)

    current_view = {
        "token": 0,
        "index": 0,
        "file_type": "photos",
        "items": [],
        "loaded_count": 0,
        "total_count": 0,
        "has_more": False,
        "is_loading_more": False,
    }

    def get_file_type(index):
        if index == 0:
            return "photos"
        if index == 1:
            return "videos"
        if index == 2:
            return "downloads"
        return None

    def render_empty_state(file_type):
        page_content.controls = [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            f"No {file_type} available.",
                            theme_style=ft.TextThemeStyle.TITLE_MEDIUM,
                            color=ft.Colors.ON_SURFACE,
                        )
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    tight=True,
                ),
                expand=True,
                alignment=ft.Alignment(0, 0),
                bgcolor=ft.Colors.SURFACE,
            )
        ]

    def render_media_content(index):
        file_type = current_view["file_type"]
        items = current_view["items"]
        total_count = current_view["total_count"]

        if not items:
            render_empty_state(file_type)
            return

        grid = ft.ResponsiveRow(
            controls=[],
            spacing=12,
            run_spacing=12,
        )

        for item in items:
            card = build_status_card(
                item,
                False,
                save_dir,
                lambda result: show_snack_bar(page, result),
                eager_thumbnail=False,
            )
            card.col = {"sm": 6, "md": 4, "lg": 3, "xl": 2}
            grid.controls.append(card)

        footer_text = f"Showing {len(items)} of {total_count} {file_type}"
        if current_view["is_loading_more"]:
            footer_text = f"{footer_text} • Loading more..."
        elif not current_view["has_more"]:
            footer_text = f"{footer_text} • All loaded"

        page_content.controls = [
            ft.Container(
                content=ft.Column(
                    [
                        grid,
                        ft.Container(
                            content=ft.Text(
                                footer_text,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                                text_align=ft.TextAlign.CENTER,
                            ),
                            padding=ft.padding.only(top=8, bottom=8),
                            alignment=ft.Alignment(0, 0),
                        ),
                    ],
                    spacing=16,
                ),
                padding=16,
                bgcolor=ft.Colors.SURFACE,
            )
        ]

    def render_downloads_content(items):
        if not items:
            render_empty_state("downloads")
            return

        grid = ft.ResponsiveRow(
            controls=[],
            spacing=12,
            run_spacing=12,
        )
        for item in items:
            card = build_status_card(
                item,
                True,
                save_dir,
                lambda result: show_snack_bar(page, result),
                on_delete=refresh_downloads,
                eager_thumbnail=True,
            )
            card.col = {"sm": 6, "md": 4, "lg": 3, "xl": 2}
            grid.controls.append(card)

        page_content.controls = [
            ft.Container(
                content=grid,
                padding=16,
                bgcolor=ft.Colors.SURFACE,
            )
        ]

    def render_loading(message="Loading..."):
        page_content.controls = [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            message,
                            theme_style=ft.TextThemeStyle.TITLE_MEDIUM,
                            color=ft.Colors.ON_SURFACE,
                        ),
                        ft.ProgressBar(width=400),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    tight=True,
                ),
                expand=True,
                alignment=ft.Alignment(0, 0),
                bgcolor=ft.Colors.SURFACE,
            )
        ]

    def warm_thumbnails(paths):
        for file_path in paths:
            get_cached_thumbnail(file_path)

    async def warm_current_batch(load_token, index, items):
        if not items:
            return

        warmed_paths = await asyncio.to_thread(warm_status_previews, items)
        if warmed_paths:
            await asyncio.to_thread(warm_thumbnails, warmed_paths)

        if current_view["token"] != load_token or current_view["index"] != index:
            return

        render_media_content(index)
        page.update()

    async def refresh_downloads():
        await show_content(2)

    async def maybe_load_more():
        if (
            current_view["index"] not in (0, 1)
            or current_view["is_loading_more"]
            or not current_view["has_more"]
        ):
            return

        current_view["is_loading_more"] = True
        render_media_content(current_view["index"])
        page.update()
        try:
            await show_content(current_view["index"], append=True)
        finally:
            current_view["is_loading_more"] = False
            if current_view["index"] in (0, 1):
                render_media_content(current_view["index"])
                page.update()

    async def on_content_scroll(e: ft.OnScrollEvent):
        if current_view["index"] not in (0, 1):
            return

        if current_view["is_loading_more"] or not current_view["has_more"]:
            return

        if e.max_scroll_extent <= 0:
            return

        remaining_distance = e.max_scroll_extent - e.pixels
        if remaining_distance <= LOAD_MORE_THRESHOLD_PX:
            page.run_task(maybe_load_more)

    page_content.on_scroll = on_content_scroll

    async def show_content(index, append=False):
        if index == 3:
            show_settings()
            page.update()
            return

        file_type = get_file_type(index)
        if not file_type:
            return

        if not append:
            current_view["token"] += 1
            current_view["index"] = index
            current_view["file_type"] = file_type
            current_view["items"] = []
            current_view["loaded_count"] = 0
            current_view["total_count"] = 0
            current_view["has_more"] = False
            current_view["is_loading_more"] = False
            render_loading("Loading statuses..." if file_type != "downloads" else "Loading downloads...")
            page.update()

        load_token = current_view["token"]

        if file_type == "downloads":
            items = await asyncio.to_thread(
                load_statuses,
                file_type,
                save_dir,
                1,
                None,
                True,
            )
            if current_view["token"] != load_token or current_view["index"] != index:
                return
            current_view["items"] = items
            current_view["loaded_count"] = len(items)
            current_view["total_count"] = len(items)
            current_view["has_more"] = False
            render_downloads_content(items)
            page.update()
            return

        page_number = (current_view["loaded_count"] // MEDIA_BATCH_SIZE) + 1
        batch_items = await asyncio.to_thread(
            load_statuses,
            file_type,
            save_dir,
            page_number,
            MEDIA_BATCH_SIZE,
            False,
        )
        total_count = await asyncio.to_thread(count_statuses, file_type, save_dir)

        if current_view["token"] != load_token or current_view["index"] != index:
            return

        if append:
            current_view["items"].extend(batch_items)
        else:
            current_view["items"] = list(batch_items)

        current_view["loaded_count"] = len(current_view["items"])
        current_view["total_count"] = total_count
        current_view["has_more"] = current_view["loaded_count"] < total_count

        render_media_content(index)
        page.update()

        preview_items = batch_items[:PREVIEW_BATCH_SIZE]
        remaining_items = batch_items[PREVIEW_BATCH_SIZE:]
        if preview_items:
            page.run_task(warm_current_batch, load_token, index, preview_items)
        if remaining_items:
            page.run_task(warm_current_batch, load_token, index, remaining_items)

    def show_settings():
        async def clear_thumbnail_cache(e):
            try:
                for file in os.listdir(THUMBNAIL_CACHE_DIR):
                    file_path = os.path.join(THUMBNAIL_CACHE_DIR, file)
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                show_snack_bar(page, "Thumbnail cache cleared")
            except Exception as e:
                show_snack_bar(page, f"Error clearing cache: {str(e)}")

        def on_save_click(e):
            new_save_dir = save_dir_input.value
            settings["save_dir"] = new_save_dir
            save_settings(settings)
            nonlocal save_dir
            save_dir = new_save_dir
            show_snack_bar(page, f"Save directory updated to: {new_save_dir}")

        async def pick_directory_click(_):
            selected_path = await pick_directory_dialog.get_directory_path(
                dialog_title="Choose where downloads are saved",
                initial_directory=save_dir_input.value or os.path.expanduser("~"),
            )
            if selected_path:
                save_dir_input.value = selected_path
                save_dir_input.update()

        save_dir_input = ft.TextField(
            value=save_dir,
            label="Save Directory",
            width=500,
            read_only=True,
        )
        pick_directory_button = ft.ElevatedButton(
            content="Browse",
            on_click=pick_directory_click,
        )
        clear_cache_button = ft.ElevatedButton(
            content="Clear Thumbnail Cache",
            on_click=clear_thumbnail_cache,
        )
        save_button = ft.ElevatedButton(content="Update", on_click=on_save_click)

        page_content.controls = [
            ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(
                            "Settings",
                            theme_style=ft.TextThemeStyle.HEADLINE_SMALL,
                            color=ft.Colors.ON_SURFACE,
                        ),
                        ft.Row(
                            controls=[save_dir_input, pick_directory_button],
                            spacing=10,
                            wrap=True,
                        ),
                        save_button,
                        clear_cache_button,
                    ],
                    alignment=ft.MainAxisAlignment.START,
                    spacing=20,
                ),
                padding=24,
                bgcolor=ft.Colors.SURFACE,
            )
        ]
        page.update()

    def theme_changed(e):
        is_light = page.theme_mode == ft.ThemeMode.LIGHT
        new_theme_mode = ft.ThemeMode.DARK if is_light else ft.ThemeMode.LIGHT
        settings["theme_mode"] = "dark" if new_theme_mode == ft.ThemeMode.DARK else "light"
        save_settings(settings)
        page.theme_mode = new_theme_mode
        e.control.icon = (
            ft.Icons.WB_SUNNY
            if new_theme_mode == ft.ThemeMode.DARK
            else ft.Icons.WB_SUNNY_OUTLINED
        )
        page.update()

    LIGHT_SEED_COLOR = ft.Colors.LIGHT_BLUE
    DARK_SEED_COLOR = ft.Colors.DEEP_PURPLE

    page.theme = ft.Theme(color_scheme_seed=LIGHT_SEED_COLOR, use_material3=True)
    page.dark_theme = ft.Theme(color_scheme_seed=DARK_SEED_COLOR, use_material3=True)

    async def on_tab_change(e):
        await show_content(e.control.selected_index)

    rail = create_navigation_rail(on_tab_change)
    title_bar = create_title_bar(page, theme_changed)

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

    if not os.path.exists(WHATSAPP_STATUS_PATH) and not has_webview_status_source():
        diagnostics = get_whatsapp_storage_diagnostics()
        known_candidates = "\n".join(diagnostics["known_candidates"])
        package_root_exists = os.path.exists(diagnostics["package_root"])

        page_content.controls = [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "WhatsApp status folder not found.",
                            theme_style=ft.TextThemeStyle.HEADLINE_SMALL,
                            color=ft.Colors.ON_SURFACE,
                        ),
                        ft.Text(
                            "This app was originally built for an older WhatsApp Desktop layout "
                            "that exposed temporary status files in a transfers folder.",
                            color=ft.Colors.ON_SURFACE,
                        ),
                        ft.Text(
                            "On this machine, the WhatsApp package is installed, but that cache "
                            "folder is not present. Newer builds appear to store data in WebView "
                            "cache/session storage instead, so there may be no direct folder for "
                            "this app to scan."
                            if package_root_exists
                            else "WhatsApp Desktop does not appear to be installed in the expected location.",
                            color=ft.Colors.ON_SURFACE,
                        ),
                        ft.Text("Expected status cache path:", color=ft.Colors.ON_SURFACE),
                        ft.Container(
                            content=ft.Text(
                                diagnostics["selected_status_path"],
                                selectable=True,
                                color=ft.Colors.ON_SURFACE,
                            ),
                            padding=12,
                            border_radius=8,
                            bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
                        ),
                        ft.Text("Known checked locations:", color=ft.Colors.ON_SURFACE),
                        ft.Container(
                            content=ft.Text(
                                known_candidates,
                                selectable=True,
                                color=ft.Colors.ON_SURFACE,
                            ),
                            padding=12,
                            border_radius=8,
                            bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
                        ),
                        ft.Text(
                            "You can still change the save folder in Settings, but status discovery "
                            "will stay empty until WhatsApp exposes a readable media cache again.",
                            color=ft.Colors.ON_SURFACE,
                        ),
                    ],
                    spacing=10,
                    tight=True,
                ),
                padding=24,
                margin=20,
                border_radius=16,
                bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.ON_SURFACE),
                width=900,
            )
        ]
        page.update()
        return page

    page.run_task(show_content, 0)
    return page
