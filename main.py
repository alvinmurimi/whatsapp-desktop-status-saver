import asyncio
import sys

import flet as ft

from app import main

MIN_PYTHON = (3, 10)


def validate_python_version():
    if sys.version_info < MIN_PYTHON:
        version = ".".join(str(part) for part in MIN_PYTHON)
        raise RuntimeError(f"WhatsApp Status Saver requires Python {version}+.")

if __name__ == "__main__":
    validate_python_version()
    asyncio.run(ft.app(target=main))
