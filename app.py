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
from ui import (
    StatusCardHandle,
    build_status_card,
    build_browser_icon_button,
    build_loading_state,
    build_empty_state,
    build_unavailable_state,
    create_title_bar,
    create_navigation_rail,
)
from status_handler import (
    count_statuses,
    get_status_item_key,
    get_status_preview_path,
    load_statuses,
    refresh_status_cache,
    warm_status_previews,
)
from live_text_hydration import (
    hydrate_live_text_records,
    records_need_live_hydration,
)
from utils import get_cached_thumbnail


def show_snack_bar(page, message):
    normalized_message = message.strip()
    lowered_message = normalized_message.lower()

    duration = 1400
    if lowered_message.startswith("downloaded:"):
        display_message = (
            normalized_message.split(" to ", 1)[0]
            .replace("Downloaded:", "Saved")
            .strip()
        )
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
    if current_source not in {"desktop", "web", "all"}:
        current_source = "desktop"
    current_web_browser = settings.get("web_browser", "chrome")
    if current_web_browser not in get_supported_web_browsers():
        current_web_browser = "chrome"
    current_web_profile = settings.get("web_profile", "")
    auto_refresh_enabled = bool(settings.get("auto_refresh_enabled", False))
    current_scroll_pixels = 0.0
    pending_auto_refresh = {
        "index": None,
        "message": "",
    }
    auto_refresh_snapshots = {}

    # ── Theme ──────────────────────────────────────────────────────────────────
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.TEAL, use_material3=True)
    page.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.TEAL, use_material3=True)

    # ── Content area ───────────────────────────────────────────────────────────
    page_content = ft.Column(
        alignment=ft.MainAxisAlignment.START,
        horizontal_alignment=ft.CrossAxisAlignment.START,
        expand=True,
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        scroll_interval=10,
    )
    page_content.controls = [build_loading_state("Loading statuses...")]

    pick_directory_dialog = ft.FilePicker()
    if pick_directory_dialog not in page.services:
        page.services.append(pick_directory_dialog)
    clipboard_service = ft.Clipboard()
    if clipboard_service not in page.services:
        page.services.append(clipboard_service)

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

    # ── Media grid ─────────────────────────────────────────────────────────────
    media_grid = ft.ResponsiveRow(controls=[], spacing=12, run_spacing=12)
    media_footer_label = ft.Text(
        color=ft.Colors.ON_SURFACE_VARIANT,
        text_align=ft.TextAlign.CENTER,
        size=12,
    )
    media_content = ft.Container(
        content=ft.Column(
            [
                media_grid,
                ft.Container(
                    content=media_footer_label,
                    padding=ft.padding.only(top=8, bottom=16),
                    alignment=ft.Alignment(0, 0),
                ),
            ],
            spacing=16,
        ),
        padding=16,
        bgcolor=ft.Colors.SURFACE,
    )
    current_view["footer_label"] = media_footer_label

    # ── Source bar state ───────────────────────────────────────────────────────
    # Source pill buttons
    desktop_pill_label = ft.Text("Desktop", size=13, weight=ft.FontWeight.W_500)
    desktop_pill_icon = ft.Icon(ft.Icons.COMPUTER, size=15)
    web_pill_label = ft.Text("Web", size=13, weight=ft.FontWeight.W_500)
    web_pill_icon = ft.Icon(ft.Icons.LANGUAGE, size=15)
    all_pill_label = ft.Text("All", size=13, weight=ft.FontWeight.W_500)
    all_pill_icon = ft.Icon(ft.Icons.APPS, size=15)

    desktop_pill = ft.Container(
        content=ft.Row(
            [desktop_pill_icon, desktop_pill_label],
            spacing=6,
            tight=True,
        ),
        padding=ft.padding.symmetric(horizontal=16, vertical=9),
        border_radius=ft.BorderRadius(20, 0, 0, 20),
        on_click=lambda _: page.run_task(change_source, "desktop"),
        ink=True,
    )
    web_pill = ft.Container(
        content=ft.Row(
            [web_pill_icon, web_pill_label],
            spacing=6,
            tight=True,
        ),
        padding=ft.padding.symmetric(horizontal=16, vertical=9),
        border_radius=ft.BorderRadius(0, 0, 0, 0),
        on_click=lambda _: page.run_task(change_source, "web"),
        ink=True,
    )
    all_pill = ft.Container(
        content=ft.Row(
            [all_pill_icon, all_pill_label],
            spacing=6,
            tight=True,
        ),
        padding=ft.padding.symmetric(horizontal=16, vertical=9),
        border_radius=ft.BorderRadius(0, 20, 20, 0),
        on_click=lambda _: page.run_task(change_source, "all"),
        ink=True,
    )

    # Browser icon row (replaces browser dropdown)
    browser_icon_row = ft.Row([], spacing=6, visible=False)

    # Profile dropdown — borderless pill style, fixed height to match source pill
    web_profile_dropdown = ft.Dropdown(
        hint_text="Profile",
        dense=True,
        width=180,
        border_radius=20,
        border_color=ft.Colors.with_opacity(0.15, ft.Colors.ON_SURFACE),
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
        visible=False,
    )

    # ── Helper: rebuild browser icon buttons ───────────────────────────────────
    def rebuild_browser_icons():
        supported = get_supported_web_browsers()
        browser_icon_row.controls = [
            build_browser_icon_button(
                browser_id=b,
                label=get_web_browser_label(b),
                selected=(b == current_web_browser),
                on_click=lambda _, br=b: page.run_task(change_web_browser_by_id, br),
            )
            for b in supported
        ]
        browser_icon_row.visible = current_source == "web"

    def get_available_web_profiles():
        return get_web_profiles(current_web_browser)

    def get_current_profile_display_name():
        for profile in get_available_web_profiles():
            if profile["profile_name"] == current_web_profile:
                return profile.get("profile_display_name") or profile["profile_name"]
        return current_web_profile or "Default"

    def refresh_web_profile_dropdown():
        nonlocal current_web_profile
        profiles = get_available_web_profiles()
        option_values = [p["profile_name"] for p in profiles]

        web_profile_dropdown.options = [
            ft.dropdown.Option(
                key=p["profile_name"],
                text=(
                    p.get("profile_display_name") or p["profile_name"]
                )
                if p["available"]
                else f"{p.get('profile_display_name') or p['profile_name']} (no data)",
            )
            for p in profiles
        ]

        if option_values:
            if current_web_profile not in option_values:
                preferred = next(
                    (p["profile_name"] for p in profiles if p["available"]),
                    option_values[0],
                )
                current_web_profile = preferred
                settings["web_profile"] = current_web_profile
                save_settings(settings)
            web_profile_dropdown.value = current_web_profile
        else:
            current_web_profile = ""
            web_profile_dropdown.value = None

        web_profile_dropdown.visible = current_source == "web"

    def refresh_source_pill_style():
        pill_specs = [
            ("desktop", desktop_pill, desktop_pill_label, desktop_pill_icon),
            ("web", web_pill, web_pill_label, web_pill_icon),
            ("all", all_pill, all_pill_label, all_pill_icon),
        ]
        for source_name, pill, label, icon in pill_specs:
            is_selected = current_source == source_name
            pill.bgcolor = ft.Colors.PRIMARY_CONTAINER if is_selected else None
            label.color = (
                ft.Colors.ON_PRIMARY_CONTAINER
                if is_selected
                else ft.Colors.ON_SURFACE_VARIANT
            )
            icon.color = (
                ft.Colors.ON_PRIMARY_CONTAINER
                if is_selected
                else ft.Colors.ON_SURFACE_VARIANT
            )

    def refresh_source_controls():
        refresh_source_pill_style()
        rebuild_browser_icons()
        refresh_web_profile_dropdown()

    # ── Source / browser / profile change handlers ─────────────────────────────
    async def change_source(new_source):
        nonlocal current_source
        if new_source == current_source:
            return
        current_source = new_source
        settings["discovery_source"] = new_source
        save_settings(settings)
        refresh_source_controls()
        if current_view["index"] in (0, 1, 2):
            await show_content(current_view["index"])
        else:
            page.update()

    async def change_web_browser_by_id(browser_id):
        nonlocal current_web_browser, current_web_profile
        if browser_id == current_web_browser:
            return
        current_web_browser = browser_id
        current_web_profile = ""
        settings["web_browser"] = current_web_browser
        settings["web_profile"] = current_web_profile
        save_settings(settings)
        refresh_source_controls()
        if current_source == "web" and current_view["index"] in (0, 1, 2):
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
        if current_source == "web" and current_view["index"] in (0, 1, 2):
            await show_content(current_view["index"])
        else:
            page.update()

    web_profile_dropdown.on_select = change_web_profile

    async def refresh_current_view(_=None):
        if current_view["index"] in (0, 1, 2):
            await asyncio.to_thread(
                refresh_status_cache,
                current_source,
                current_web_browser,
                current_web_profile,
            )
        await show_content(current_view["index"])

    # ── Helpers ────────────────────────────────────────────────────────────────
    def get_file_type(index):
        return {0: "photos", 1: "videos", 2: "texts", 3: "downloads"}.get(index)

    def get_source_label():
        if current_source == "desktop":
            return "WhatsApp Desktop"
        if current_source == "all":
            return "All local sources"
        if current_web_profile:
            return (
                f"WhatsApp Web ({get_web_browser_label(current_web_browser)}"
                f" - {get_current_profile_display_name()})"
            )
        return f"WhatsApp Web ({get_web_browser_label(current_web_browser)})"

    def get_source_signature():
        return (current_source, current_web_browser, current_web_profile)

    def get_media_tab_indexes():
        return (0, 1, 2)

    def get_media_file_types():
        return ("photos", "videos", "texts")

    # ── Render functions ───────────────────────────────────────────────────────
    def render_empty_state(file_type):
        if file_type == "downloads":
            msg = "Status you save will appear here."
            icon = ft.Icons.FOLDER
        elif file_type == "texts":
            msg = f"No text statuses found from {get_source_label()}."
            icon = ft.Icons.FORMAT_QUOTE
        elif current_source == "all":
            msg = f"No {file_type} found from {get_source_label()}."
            icon = ft.Icons.APPS
        elif current_source == "web":
            browser_label = get_web_browser_label(current_web_browser)
            msg = (
                f"No {file_type} found from {get_source_label()}.\n"
                f"Open web.whatsapp.com in {browser_label}, view statuses, then refresh."
            )
            icon = ft.Icons.LANGUAGE
        else:
            msg = f"No {file_type} found from {get_source_label()}."
            icon = ft.Icons.PHOTO_CAMERA
        page_content.controls = [build_empty_state(msg, icon)]

    def render_source_unavailable():
        diagnostics = get_status_source_diagnostics(
            current_source, current_web_browser, current_web_profile
        )
        if current_source == "desktop":
            body = (
                "WhatsApp Desktop could not be found on this machine. "
                "Install WhatsApp Desktop or switch to Web source above."
            )
            detail_lines = [
                f"Desktop cache: {diagnostics['selected_status_path']}",
                f"WebView path:  {diagnostics['webview_indexeddb_dir']}",
            ]
        else:
            browser_label = diagnostics["browser_label"]
            if not diagnostics["browser_installed"]:
                body = (
                    f"{browser_label} is not installed. "
                    "Install it or switch back to WhatsApp Desktop."
                )
            elif diagnostics["profile_count"] <= 0:
                body = (
                    f"No {browser_label} profiles found. "
                    f"Open {browser_label} once, then log in to web.whatsapp.com."
                )
            else:
                body = (
                    f"WhatsApp Web data not found for {browser_label} profile "
                    f"'{diagnostics.get('profile_display_name') or diagnostics['profile_name']}'. "
                    f"Open {browser_label} with that profile, log in to web.whatsapp.com, then refresh."
                )
            detail_lines = [
                f"Profile:      {diagnostics.get('profile_display_name') or diagnostics['profile_name']}",
                f"IndexedDB:    {diagnostics['indexeddb_dir']}",
            ]

        page_content.controls = [
            build_unavailable_state(
                f"{get_source_label()} is not available",
                body,
                detail_lines,
            )
        ]

    def render_loading(message="Loading...", detail=None):
        page_content.controls = [build_loading_state(message, detail)]

    def update_media_footer():
        shown = len(current_view["items"])
        total = current_view["total_count"]
        ftype = current_view["file_type"]
        footer = f"Showing {shown} of {total} {ftype} from {get_source_label()}"
        if current_view["is_loading_more"]:
            footer += " - Loading more..."
        elif not current_view["has_more"]:
            footer += " - All loaded"
        media_footer_label.value = footer

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
                on_copy_text=copy_text_to_clipboard,
            )
            card_handle.control.col = {"sm": 6, "md": 4, "lg": 3, "xl": 2}
            media_grid.controls.append(card_handle.control)
            current_view["card_handles"][item_key] = card_handle

    def sync_media_items(items):
        next_controls = []
        next_handles = {}
        for item in items:
            item_key = get_status_item_key(item)
            card_handle = current_view["card_handles"].get(item_key)
            if card_handle is None:
                card_handle = build_status_card(
                    item,
                    False,
                    save_dir,
                    lambda result: show_snack_bar(page, result),
                    eager_thumbnail=False,
                    on_copy_text=copy_text_to_clipboard,
                )
                card_handle.control.col = {"sm": 6, "md": 4, "lg": 3, "xl": 2}
            next_controls.append(card_handle.control)
            next_handles[item_key] = card_handle

        media_grid.controls = next_controls
        current_view["card_handles"] = next_handles

    def render_media_content():
        ensure_media_content()
        update_media_footer()

    async def copy_text_to_clipboard(text_value):
        try:
            await clipboard_service.set(text_value)
            show_snack_bar(page, "Copied text")
        except Exception as error:
            show_snack_bar(page, f"Error copying text: {error}")

    def render_downloads_content(items):
        if not items:
            render_empty_state("downloads")
            return
        grid = ft.ResponsiveRow(controls=[], spacing=12, run_spacing=12)
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

    # ── Thumbnail warming ──────────────────────────────────────────────────────
    def warm_thumbnails(paths):
        for file_path in paths:
            get_cached_thumbnail(file_path)

    async def hydrate_texts_in_background(load_token, index, items):
        if not records_need_live_hydration(items):
            return
        result = await asyncio.to_thread(hydrate_live_text_records, items)
        updated = int(result.get("updated") or 0)
        if updated <= 0:
            return

        message = f"Updated {updated} text status{'es' if updated != 1 else ''}"
        if current_view["index"] == index and current_view["token"] == load_token:
            await silent_refresh_current_view(message)
        else:
            show_snack_bar(page, message)

    async def warm_current_batch(load_token, index, items):
        if not items:
            return
        text_items_to_prepare = [
            item
            for item in items
            if getattr(item, "kind", None) == "texts" and not get_status_preview_path(item)
        ]
        if text_items_to_prepare:
            show_snack_bar(
                page,
                f"Preparing {len(text_items_to_prepare)} text preview{'s' if len(text_items_to_prepare) != 1 else ''}",
            )
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
        if text_items_to_prepare:
            show_snack_bar(
                page,
                f"{len(text_items_to_prepare)} text preview{'s' if len(text_items_to_prepare) != 1 else ''} ready",
            )
            page.run_task(hydrate_texts_in_background, load_token, index, list(items))

    async def refresh_downloads():
        await show_content(3)

    async def silent_refresh_current_view(message=None):
        if current_view["index"] not in get_media_tab_indexes():
            return

        index = current_view["index"]
        file_type = current_view["file_type"]
        load_token = current_view["token"] + 1
        current_view["token"] = load_token

        total_count = await asyncio.to_thread(
            count_statuses,
            file_type,
            save_dir,
            current_source,
            current_web_browser,
            current_web_profile,
        )
        target_count = max(current_view["loaded_count"], MEDIA_BATCH_SIZE)
        target_count = min(max(target_count + 12, MEDIA_BATCH_SIZE), max(total_count, MEDIA_BATCH_SIZE))
        latest_items = await asyncio.to_thread(
            load_statuses,
            file_type,
            save_dir,
            1,
            target_count,
            False,
            current_source,
            current_web_browser,
            current_web_profile,
        )

        if current_view["index"] != index or current_view["token"] != load_token:
            return

        previous_keys = {get_status_item_key(item) for item in current_view["items"]}
        current_view["items"] = list(latest_items)
        current_view["loaded_count"] = len(current_view["items"])
        current_view["total_count"] = total_count
        current_view["has_more"] = current_view["loaded_count"] < total_count
        sync_media_items(current_view["items"])
        render_media_content()
        page.update()

        new_items = [
            item for item in current_view["items"]
            if get_status_item_key(item) not in previous_keys
        ]
        if new_items:
            page.run_task(warm_current_batch, load_token, index, new_items[:PREVIEW_BATCH_SIZE])
        if message:
            show_snack_bar(page, message)

    async def apply_pending_auto_refresh():
        if pending_auto_refresh["index"] != current_view["index"]:
            return
        message = pending_auto_refresh["message"]
        pending_auto_refresh["index"] = None
        pending_auto_refresh["message"] = ""
        await silent_refresh_current_view(message)

    async def background_refresh_loop():
        while True:
            await asyncio.sleep(45)
            if not auto_refresh_enabled:
                continue

            source_signature = get_source_signature()
            snapshot = {}
            for file_type in get_media_file_types():
                total = await asyncio.to_thread(
                    count_statuses,
                    file_type,
                    save_dir,
                    current_source,
                    current_web_browser,
                    current_web_profile,
                )
                latest_items = await asyncio.to_thread(
                    load_statuses,
                    file_type,
                    save_dir,
                    1,
                    1,
                    False,
                    current_source,
                    current_web_browser,
                    current_web_profile,
                )
                latest_key = get_status_item_key(latest_items[0]) if latest_items else None
                snapshot[file_type] = (total, latest_key)

            previous_snapshot = auto_refresh_snapshots.get(source_signature)
            auto_refresh_snapshots[source_signature] = snapshot
            if previous_snapshot is None:
                continue

            changed_types = [
                file_type
                for file_type in get_media_file_types()
                if snapshot.get(file_type) != previous_snapshot.get(file_type)
            ]
            if not changed_types:
                continue

            delta = sum(
                max(0, snapshot[file_type][0] - previous_snapshot.get(file_type, (0, None))[0])
                for file_type in changed_types
            )
            if delta > 0:
                message = f"{delta} new status{'es' if delta != 1 else ''} found in background"
            else:
                labels = ", ".join(changed_types)
                message = f"Background refresh updated {labels}"

            if current_view["index"] in get_media_tab_indexes() and current_scroll_pixels <= 8 and not current_view["is_loading_more"]:
                await silent_refresh_current_view(message)
            else:
                pending_auto_refresh["index"] = current_view["index"]
                pending_auto_refresh["message"] = message
                show_snack_bar(page, message)

    # ── Infinite scroll ────────────────────────────────────────────────────────
    async def maybe_load_more():
        if (
            current_view["index"] not in (0, 1, 2)
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
        nonlocal current_scroll_pixels
        current_scroll_pixels = e.pixels
        if pending_auto_refresh["index"] is not None and current_scroll_pixels <= 8:
            page.run_task(apply_pending_auto_refresh)
        if current_view["index"] not in (0, 1, 2):
            return
        if current_view["is_loading_more"] or not current_view["has_more"]:
            return
        if e.max_scroll_extent <= 0:
            return
        if (e.max_scroll_extent - e.pixels) <= LOAD_MORE_THRESHOLD_PX:
            page.run_task(maybe_load_more)

    page_content.on_scroll = on_content_scroll

    # ── Main content loader ────────────────────────────────────────────────────
    async def show_content(index, append=False):
        if index == 4:
            current_view["index"] = 4
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
            if file_type == "downloads":
                render_loading("Loading saved items...", "Checking your downloads folder")
            elif current_source == "desktop":
                render_loading(f"Scanning WhatsApp Desktop for {file_type}...", "Checking local WebView storage")
            elif current_source == "all":
                render_loading(f"Scanning all local sources for {file_type}...", "Combining Desktop and browser records")
            else:
                browser_label = get_web_browser_label(current_web_browser)
                render_loading(
                    f"Reading {browser_label} - {get_current_profile_display_name()} for {file_type}...",
                    "Checking the selected browser profile",
                )
            page.update()

        load_token = current_view["token"]

        if file_type == "downloads":
            items = await asyncio.to_thread(
                load_statuses, file_type, save_dir, 1, None, True
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

        diagnostics = None
        if current_source != "all":
            if not append:
                render_loading("Checking source availability...", get_source_label())
                page.update()
            diagnostics = get_status_source_diagnostics(
                current_source, current_web_browser, current_web_profile
            )
        if diagnostics and not diagnostics["available"]:
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
        if not append:
            render_loading("Reading local status records...", f"Preparing {file_type} from {get_source_label()}")
            page.update()
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
            if not append:
                render_loading("Counting available statuses...", f"Building the {file_type} view")
                page.update()
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

        if not append:
            render_loading("Preparing the view...", "Laying out cards and warming previews")
            page.update()
        append_media_items(batch_items if append else current_view["items"])
        render_media_content()
        page.update()

        preview_items = batch_items[:PREVIEW_BATCH_SIZE]
        remaining_items = batch_items[PREVIEW_BATCH_SIZE:]
        if preview_items:
            page.run_task(warm_current_batch, load_token, index, preview_items)
        if remaining_items:
            page.run_task(warm_current_batch, load_token, index, remaining_items)

    # ── Settings view ──────────────────────────────────────────────────────────
    def show_settings():
        nonlocal auto_refresh_enabled

        async def clear_thumbnail_cache(e):
            try:
                for file in os.listdir(THUMBNAIL_CACHE_DIR):
                    fp = os.path.join(THUMBNAIL_CACHE_DIR, file)
                    if os.path.isfile(fp):
                        os.unlink(fp)
                show_snack_bar(page, "Thumbnail cache cleared")
            except Exception as ex:
                show_snack_bar(page, f"Error clearing cache: {str(ex)}")

        def on_save_click(e):
            new_save_dir = save_dir_input.value
            settings["save_dir"] = new_save_dir
            save_settings(settings)
            nonlocal save_dir
            save_dir = new_save_dir
            show_snack_bar(page, f"Save directory updated")

        def on_auto_refresh_change(e):
            nonlocal auto_refresh_enabled
            auto_refresh_enabled = bool(e.control.value)
            settings["auto_refresh_enabled"] = auto_refresh_enabled
            save_settings(settings)
            show_snack_bar(
                page,
                "Background updates enabled" if auto_refresh_enabled else "Background updates disabled",
            )

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
            label="Save directory",
            expand=True,
            read_only=True,
            filled=True,
            border_radius=12,
        )
        auto_refresh_switch = ft.Switch(value=auto_refresh_enabled, on_change=on_auto_refresh_change)

        def _row_setting(icon, title, subtitle, action):
            return ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            content=ft.Icon(icon, size=20, color=ft.Colors.PRIMARY),
                            width=40,
                            height=40,
                            bgcolor=ft.Colors.PRIMARY_CONTAINER,
                            border_radius=12,
                            alignment=ft.Alignment(0, 0),
                        ),
                        ft.Column(
                            [
                                ft.Text(title, size=14, weight=ft.FontWeight.W_500, color=ft.Colors.ON_SURFACE),
                                ft.Text(subtitle, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                            ],
                            spacing=1,
                            tight=True,
                            expand=True,
                        ),
                        action,
                    ],
                    spacing=14,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.padding.symmetric(horizontal=16, vertical=14),
                border_radius=14,
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                border=ft.border.all(1, ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE)),
            )

        page_content.controls = [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Settings",
                            theme_style=ft.TextThemeStyle.HEADLINE_SMALL,
                            color=ft.Colors.ON_SURFACE,
                            weight=ft.FontWeight.W_600,
                        ),
                        # Save directory row
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Container(
                                                content=ft.Icon(ft.Icons.FOLDER_OPEN, size=20, color=ft.Colors.PRIMARY),
                                                width=40, height=40,
                                                bgcolor=ft.Colors.PRIMARY_CONTAINER,
                                                border_radius=12,
                                                alignment=ft.Alignment(0, 0),
                                            ),
                                            ft.Column(
                                                [
                                                    ft.Text("Save location", size=14, weight=ft.FontWeight.W_500),
                                                    ft.Text("Where downloaded statuses are stored", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                                                ],
                                                spacing=1,
                                                tight=True,
                                                expand=True,
                                            ),
                                        ],
                                        spacing=14,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Row(
                                        [
                                            save_dir_input,
                                            ft.OutlinedButton(
                                                "Browse",
                                                icon=ft.Icons.FOLDER,
                                                on_click=pick_directory_click,
                                            ),
                                            ft.FilledButton(
                                                "Save",
                                                on_click=on_save_click,
                                            ),
                                        ],
                                        spacing=10,
                                    ),
                                ],
                                spacing=14,
                                tight=True,
                            ),
                            padding=ft.padding.symmetric(horizontal=16, vertical=14),
                            border_radius=14,
                            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                            border=ft.border.all(1, ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE)),
                        ),
                        # Cache row
                        _row_setting(
                            ft.Icons.SYNC,
                            "Automatic background updates",
                            "Refresh statuses quietly in the background without interrupting your browsing",
                            auto_refresh_switch,
                        ),
                        _row_setting(
                            ft.Icons.AUTO_DELETE,
                            "Thumbnail cache",
                            "Clear locally stored preview images to free up space",
                            ft.OutlinedButton(
                                "Clear",
                                icon=ft.Icons.DELETE_SWEEP,
                                on_click=clear_thumbnail_cache,
                            ),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.START,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    spacing=12,
                ),
                padding=ft.padding.all(24),
                bgcolor=ft.Colors.SURFACE,
                alignment=ft.Alignment(-1, -1),
                expand=True,
            )
        ]
        page.update()

    # ── Theme toggle ───────────────────────────────────────────────────────────
    _is_light = page.theme_mode == ft.ThemeMode.LIGHT
    theme_icon_btn = ft.IconButton(
        icon=ft.Icons.WB_SUNNY_OUTLINED if _is_light else ft.Icons.WB_SUNNY,
        icon_color=ft.Colors.ON_SURFACE_VARIANT,
        tooltip="Toggle theme",
        icon_size=20,
    )

    def theme_changed(e):
        is_light = page.theme_mode == ft.ThemeMode.LIGHT
        new_mode = ft.ThemeMode.DARK if is_light else ft.ThemeMode.LIGHT
        settings["theme_mode"] = "dark" if new_mode == ft.ThemeMode.DARK else "light"
        save_settings(settings)
        page.theme_mode = new_mode
        theme_icon_btn.icon = (
            ft.Icons.WB_SUNNY if new_mode == ft.ThemeMode.DARK else ft.Icons.WB_SUNNY_OUTLINED
        )
        page.update()

    theme_icon_btn.on_click = theme_changed

    # ── Tab change ─────────────────────────────────────────────────────────────
    async def on_tab_change(e):
        await show_content(e.control.selected_index)

    # ── Source bar assembly ────────────────────────────────────────────────────
    refresh_source_controls()

    source_toggle = ft.Container(
        content=ft.Row(
            [
                # Segmented pill: Desktop | Web
                ft.Container(
                    content=ft.Row([desktop_pill, web_pill, all_pill], spacing=0),
                    border_radius=20,
                    border=ft.border.all(1.5, ft.Colors.with_opacity(0.2, ft.Colors.PRIMARY)),
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                ),
                # Browser chips — visible when web is active (same height as pill, no layout shift)
                browser_icon_row,
                # Profile selector — visible when web is active
                web_profile_dropdown,
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        # Fixed height: prevents layout shift when web controls appear/disappear
        height=58,
        padding=ft.padding.symmetric(horizontal=16),
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        border=ft.border.only(
            bottom=ft.BorderSide(1, ft.Colors.with_opacity(0.07, ft.Colors.ON_SURFACE))
        ),
    )

    # ── Page layout ────────────────────────────────────────────────────────────
    rail = create_navigation_rail(on_tab_change)
    title_bar = create_title_bar(page, refresh_current_view, theme_icon_btn)

    page.add(
        ft.Column(
            [
                title_bar,
                source_toggle,
                ft.Row(
                    [
                        rail,
                        ft.VerticalDivider(width=1),
                        page_content,
                    ],
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ],
            expand=True,
        )
    )

    page.run_task(background_refresh_loop)
    page.run_task(show_content, 0)
    return page
