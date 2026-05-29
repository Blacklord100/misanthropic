"""Build Breakthrough.app with py2app.

From the repo root:
    pip install -e ".[app]" py2app
    python packaging/setup_app.py py2app
    open dist/Breakthrough.app

The app is a menu-bar agent (LSUIElement = no Dock icon).
"""
from setuptools import setup

from breakthrough import __version__

APP = ["packaging/app_main.py"]

OPTIONS = {
    "argv_emulation": False,
    "packages": ["breakthrough"],
    "includes": ["rumps"],
    "plist": {
        "CFBundleName": "Breakthrough",
        "CFBundleDisplayName": "Breakthrough",
        "CFBundleIdentifier": "com.breakthrough.app",
        "CFBundleVersion": __version__,
        "CFBundleShortVersionString": __version__,
        "LSUIElement": True,  # menu-bar only, no Dock icon
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "MIT",
    },
}

setup(
    app=APP,
    name="Breakthrough",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
