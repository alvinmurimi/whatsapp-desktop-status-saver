import flet as ft
import asyncio
from app import main

if __name__ == "__main__":
    asyncio.run(ft.app(target=main))