import flet as ft
import os
import asyncio
from config import (
    get_web_profiles,
    get_supported_web_browsers,
    get_web_browser_label,
    load_settings,
    save_settings,
    THUMBNAIL_CACHE_DIR,
    get_status_source_diagnostics,
)
from ui import build_status_card, create_title_bar, create_navigation_rail
from status_handler import (
    count_statuses,
    get_status_item_key,
    load_statuses,
    refresh_status_cache,
    warm_status_previews,
)
from utils import get_cached_thumbnail


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

    snackbar = ft.SnackBar(
        content=ft.Text(display_message),
        duration=duration,
        behavior=ft.SnackBarBehavior.FLOATING,
        dismiss_direction=ft.DismissDirection.DOWN,
        show_close_icon=False,
    )
    dialogs = getattr(page, "_dialogs", None)
    if dialogs is not None:
        for dialog in list(dialogs.controls):
            if isinstance(dialog, ft.SnackBar):
                dialog.open = False
                dialogs.controls.remove(dialog)
        dialogs.update()
    page.show_dialog(snackbar)
        
async def main(page: ft.Page):
    MEDIA_BATCH_SIZE = 24
    PREVIEW_BATCH_SIZE = 8
    LOAD_MORE_THRESHOLD_PX = 160

    page.title = "WhatsApp Status Saver"
    page.window.width = 1200
    page.window.height = 800
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
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
    current_source = settings.get("discovery_source", "desktop")
    if current_source not in {"desktop", "web"}:
        current_source = "desktop"
    current_web_browser = settings.get("web_browser", "chrome")
    if current_web_browser not in get_supported_web_browsers():
        current_web_browser = "chrome"
    current_web_profile = settings.get("web_profile", "")

    page_content = ft.Column(
        alignment=ft.MainAxisAlignment.START,
        horizontal_alignment=ft.CrossAxisAlignment.START,
        expand=True,
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        scroll_interval=10,
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
        "card_handles": {},
        "footer_label": None,
    }

    desktop_source_button = ft.ElevatedButton(content="Desktop")
    web_source_button = ft.ElevatedButton(content="Web")
    web_browser_dropdown = ft.Dropdown(
        label="Browser",
        width=170,
        dense=True,
        visible=False,
    )
    web_profile_dropdown = ft.Dropdown(
        label="Browser profile",
        width=220,
        dense=True,
        visible=False,
    )

    media_grid = ft.ResponsiveRow(
        controls=[],
        spacing=12,
        run_spacing=12,
    )
    media_footer_label = ft.Text(
        color=ft.Colors.ON_SURFACE_VARIANT,
        text_align=ft.TextAlign.CENTER,
    )
    media_content = ft.Container(
        content=ft.Column(
            [
                media_grid,
                ft.Container(
                    content=media_footer_label,
                    padding=ft.padding.only(top=8, bottom=8),
                    alignment=ft.Alignment(0, 0),
                ),
            ],
            spacing=16,
        ),
        padding=16,
        bgcolor=ft.Colors.SURFACE,
    )
    current_view["footer_label"] = media_footer_label

    def get_available_web_profiles():
        return get_web_profiles(current_web_browser)

    def refresh_web_browser_dropdown():
        supported_browsers = get_supported_web_browsers()
        web_browser_dropdown.options = [
            ft.dropdown.Option(key=browser, text=get_web_browser_label(browser))
            for browser in supported_browsers
        ]
        if current_web_browser in supported_browsers:
            web_browser_dropdown.value = current_web_browser
        elif supported_browsers:
            web_browser_dropdown.value = supported_browsers[0]
        else:
            web_browser_dropdown.value = None
        web_browser_dropdown.visible = current_source == "web"

    def refresh_web_profile_dropdown():
        nonlocal current_web_profile
        profiles = get_available_web_profiles()
        option_values = [profile["profile_name"] for profile in profiles]
        web_profile_dropdown.label = f"{get_web_browser_label(current_web_browser)} profile"

        web_profile_dropdown.options = [
            ft.dropdown.Option(
                key=profile["profile_name"],
                text=(
                    f"{profile['profile_name']}"
                    if profile["available"]
                    else f"{profile['profile_name']} (no WhatsApp Web data)"
                ),
            )
            for profile in profiles
        ]

        if option_values:
            if current_web_profile not in option_values:
                preferred_profile = next(
                    (profile["profile_name"] for profile in profiles if profile["available"]),
                    option_values[0],
                )
                current_web_profile = preferred_profile
                settings["web_profile"] = current_web_profile
                save_settings(settings)
            web_profile_dropdown.value = current_web_profile
        else:
            current_web_profile = ""
            web_profile_dropdown.value = None

        web_profile_dropdown.visible = current_source == "web"

    def refresh_source_buttons():
        desktop_source_button.disabled = current_source == "desktop"
        web_source_button.disabled = current_source == "web"
        refresh_web_browser_dropdown()
        refresh_web_profile_dropdown()

    async def change_source(new_source):
        nonlocal current_source
        if new_source == current_source:
            return

        current_source = new_source
        settings["discovery_source"] = new_source
        save_settings(settings)
        refresh_source_buttons()

        if current_view["index"] in (0, 1):
            await show_content(current_view["index"])
        else:
            page.update()

    async def change_web_browser(e):
        nonlocal current_web_browser, current_web_profile
        selected_browser = e.control.value or "chrome"
        if selected_browser == current_web_browser:
            return

        current_web_browser = selected_browser
        current_web_profile = ""
        settings["web_browser"] = current_web_browser
        settings["web_profile"] = current_web_profile
        save_settings(settings)
        refresh_source_buttons()

        if current_source == "web" and current_view["index"] in (0, 1):
            await show_content(current_view["index"])
        else:
            page.update()

    async def change_web_profile(e):
        nonlocal current_web_profile
        selected_profile = e.control.value or ""
        if selected_profile == current_web_profile:
            return

        current_web_profile = selected_profile
        settings["web_profile"] = current_web_profile
        save_settings(settings)

        if current_source == "web" and current_view["index"] in (0, 1):
            await show_content(current_view["index"])
        else:
            page.update()

    async def refresh_current_view(_=None):
        if current_view["index"] in (0, 1):
            await asyncio.to_thread(
                refresh_status_cache,
                current_source,
                current_web_browser,
                current_web_profile,
            )
        await show_content(current_view["index"])

    def get_file_type(index):
        if index == 0:
            return "photos"
        if index == 1:
            return "videos"
        if index == 2:
            return "downloads"
        return None

    def get_source_label():
        if current_source == "desktop":
            return "WhatsApp Desktop"
        if current_web_profile:
            return (
                f"WhatsApp Web ({get_web_browser_label(current_web_browser)} - "
                f"{current_web_profile})"
            )
        return f"WhatsApp Web ({get_web_browser_label(current_web_browser)})"

    def render_empty_state(file_type):
        guidance = (
            f"No {file_type} available from {get_source_label()}."
            if current_source == "desktop"
            else f"No {file_type} available from {get_source_label()}. "
            f"Make sure you are logged in to web.whatsapp.com in "
            f"{get_web_browser_label(current_web_browser)} and have opened statuses there."
        )
        page_content.controls = [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            guidance,
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

    def render_source_unavailable():
        diagnostics = get_status_source_diagnostics(
            current_source,
            current_web_browser,
            current_web_profile,
        )
        if current_source == "desktop":
            body = (
                "WhatsApp Desktop could not be found on this machine. "
                "Install WhatsApp Desktop or switch to Web above."
            )
            detail_lines = [
                f"Desktop cache path: {diagnostics['selected_status_path']}",
                f"Desktop WebView path: {diagnostics['webview_indexeddb_dir']}",
            ]
        else:
            browser_label = diagnostics["browser_label"]
            if not diagnostics["browser_installed"]:
                body = (
                    f"{browser_label} does not appear to be installed on this machine. "
                    "Install it or switch back to WhatsApp Desktop above."
                )
            elif diagnostics["profile_count"] <= 0:
                body = (
                    f"No {browser_label} profiles were found. Open {browser_label} once to create a profile, "
                    "then log in to web.whatsapp.com."
                )
            else:
                body = (
                    f"WhatsApp Web data was not found for the {browser_label} profile "
                    f"'{diagnostics['profile_name']}'. Open {browser_label} with that profile, "
                    "log in to web.whatsapp.com, then refresh or choose another profile."
                )
            detail_lines = [
                f"{browser_label} profile: {diagnostics['profile_name']}",
                f"Expected IndexedDB path: {diagnostics['indexeddb_dir']}",
            ]

        page_content.controls = [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            f"{get_source_label()} is not available.",
                            theme_style=ft.TextThemeStyle.HEADLINE_SMALL,
                            color=ft.Colors.ON_SURFACE,
                        ),
                        ft.Text(
                            body,
                            color=ft.Colors.ON_SURFACE,
                        ),
                        ft.Container(
                            content=ft.Text(
                                "\n".join(detail_lines),
                                selectable=True,
                                color=ft.Colors.ON_SURFACE,
                            ),
                            padding=12,
                            border_radius=8,
                            bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
                        ),
                    ],
                    spacing=12,
                    tight=True,
                ),
                padding=24,
                margin=20,
                border_radius=16,
                bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.ON_SURFACE),
                width=900,
            )
        ]

    def update_media_footer():
        footer_text = (
            f"Showing {len(current_view['items'])} "
            f"of {current_view['total_count']} {current_view['file_type']} "
            f"from {get_source_label()}"
        )
        if current_view["is_loading_more"]:
            footer_text = f"{footer_text} - Loading more..."
        elif not current_view["has_more"]:
            footer_text = f"{footer_text} - All loaded"
        media_footer_label.value = footer_text

    def ensure_media_content():
        if not page_content.controls or page_content.controls[0] is not media_content:
            page_content.controls = [media_content]

    def reset_media_content():
        media_grid.controls.clear()
        current_view["card_handles"] = {}

    def append_media_items(items):
        for item in items:
            item_key = get_status_item_key(item)
            if item_key in current_view["card_handles"]:
                continue

            card_handle = build_status_card(
                item,
                False,
                save_dir,
                lambda result: show_snack_bar(page, result),
                eager_thumbnail=False,
            )
            card_handle.control.col = {"sm": 6, "md": 4, "lg": 3, "xl": 2}
            media_grid.controls.append(card_handle.control)
            current_view["card_handles"][item_key] = card_handle

    def render_media_content():
        ensure_media_content()
        update_media_footer()

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
            card_handle = build_status_card(
                item,
                True,
                save_dir,
                lambda result: show_snack_bar(page, result),
                on_delete=refresh_downloads,
                eager_thumbnail=True,
            )
            card_handle.control.col = {"sm": 6, "md": 4, "lg": 3, "xl": 2}
            grid.controls.append(card_handle.control)

        page_content.controls = [
            ft.Container(
                content=grid,
                padding=16,
                bgcolor=ft.Colors.SURFACE,
                alignment=ft.Alignment(-1, -1),
                expand=True,
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

        refreshed = False
        for item in items:
            card_handle = current_view["card_handles"].get(get_status_item_key(item))
            if not card_handle:
                continue
            card_handle.refresh_preview()
            refreshed = True

        if refreshed:
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
        update_media_footer()
        page.update()
        try:
            await show_content(current_view["index"], append=True)
        finally:
            current_view["is_loading_more"] = False
            update_media_footer()
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
            current_view["index"] = 3
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
            reset_media_content()
            loading_message = (
                f"Loading {get_source_label()} statuses..."
                if file_type != "downloads"
                else "Loading downloads..."
            )
            render_loading(loading_message)
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

        diagnostics = get_status_source_diagnostics(
            current_source,
            current_web_browser,
            current_web_profile,
        )
        if not diagnostics["available"]:
            if current_view["token"] != load_token or current_view["index"] != index:
                return
            current_view["items"] = []
            current_view["loaded_count"] = 0
            current_view["total_count"] = 0
            current_view["has_more"] = False
            render_source_unavailable()
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
            current_source,
            current_web_browser,
            current_web_profile,
        )
        total_count = current_view["total_count"]
        if not append or total_count <= 0:
            total_count = await asyncio.to_thread(
                count_statuses,
                file_type,
                save_dir,
                current_source,
                current_web_browser,
                current_web_profile,
            )

        if current_view["token"] != load_token or current_view["index"] != index:
            return

        if append:
            current_view["items"].extend(batch_items)
        else:
            current_view["items"] = list(batch_items)

        current_view["loaded_count"] = len(current_view["items"])
        current_view["total_count"] = total_count
        current_view["has_more"] = current_view["loaded_count"] < total_count

        if not current_view["items"] and total_count <= 0:
            render_empty_state(file_type)
            page.update()
            return

        append_media_items(batch_items if append else current_view["items"])
        render_media_content()
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
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    spacing=20,
                ),
                padding=24,
                bgcolor=ft.Colors.SURFACE,
                alignment=ft.Alignment(-1, -1),
                expand=True,
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

    desktop_source_button.on_click = lambda _: page.run_task(change_source, "desktop")
    web_source_button.on_click = lambda _: page.run_task(change_source, "web")
    web_browser_dropdown.on_change = change_web_browser
    web_profile_dropdown.on_change = change_web_profile
    refresh_source_buttons()

    source_toggle = ft.Container(
        content=ft.Row(
            [
                ft.Text(
                    "Source",
                    theme_style=ft.TextThemeStyle.TITLE_SMALL,
                    color=ft.Colors.ON_SURFACE,
                ),
                desktop_source_button,
                web_source_button,
                web_browser_dropdown,
                web_profile_dropdown,
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.padding.only(left=16, right=16, bottom=8),
        bgcolor=ft.Colors.SURFACE,
    )

    rail = create_navigation_rail(on_tab_change)
    title_bar = create_title_bar(page, refresh_current_view, theme_changed)

    page.add(
        ft.Column(
            [title_bar,
                source_toggle,
                ft.Row(
                    [
                        rail,
                        ft.VerticalDivider(width=1),
                        page_content
                    ],
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
            ],
            expand=True
        )
    )

    page.run_task(show_content, 0)
    return page
