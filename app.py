import flet as ft
import os
import asyncio
from config import load_settings, save_settings, WHATSAPP_STATUS_PATH
from ui import build_status_card, create_title_bar, create_navigation_rail
from status_handler import load_statuses, download_status, delete_file

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

    page_content = ft.Column(
        alignment=ft.MainAxisAlignment.START,
        expand=True
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
            elif index == 1:
                file_type = "videos"
            elif index == 2:
                file_type = "downloads"
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

                page_content.controls = [grid_view]

                total_items = len(files)
                is_download_section = (file_type == "downloads")
                for i, file_path in enumerate(files):
                    grid_view.controls.append(build_status_card(file_path, is_download_section, save_dir, lambda result: page.snack_bar.open(result)))
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

    rail = create_navigation_rail(on_tab_change)

    LIGHT_SEED_COLOR = ft.colors.LIGHT_BLUE
    DARK_SEED_COLOR = ft.colors.DEEP_PURPLE

    page.theme = ft.theme.Theme(color_scheme_seed=LIGHT_SEED_COLOR, use_material3=True)
    page.dark_theme = ft.theme.Theme(color_scheme_seed=DARK_SEED_COLOR, use_material3=True)

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

    await show_content(0)  # Load initial content
    return page