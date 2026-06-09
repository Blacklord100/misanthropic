"""Build Misanthropic.app with py2app.

From the repo root:
    pip install -e ".[app]" py2app
    python packaging/setup_app.py py2app
    open dist/Misanthropic.app

The app is a menu-bar agent (LSUIElement = no Dock icon).
"""
from setuptools import setup

from misanthropic import __version__

APP = ["packaging/app_main.py"]

OPTIONS = {
    "argv_emulation": False,
    "packages": ["misanthropic"],
    "includes": ["rumps"],
    "plist": {
        "CFBundleName": "Misanthropic",
        "CFBundleDisplayName": "Misanthropic",
        "CFBundleIdentifier": "com.misanthropic.app",
        "CFBundleVersion": __version__,
        "CFBundleShortVersionString": __version__,
        "LSUIElement": True,  # menu-bar only, no Dock icon
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "MIT",
    },
}

setup(
    app=APP,
    name="Misanthropic",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
