import os
import sys


def resource_path(relative_path):
    """Return an absolute resource path for source and PyInstaller builds."""
    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, relative_path)


def first_existing_resource(*relative_paths):
    """Return the first existing resource path, or an empty string."""
    for relative_path in relative_paths:
        path = resource_path(relative_path)
        if os.path.isfile(path):
            return path
    return ""
