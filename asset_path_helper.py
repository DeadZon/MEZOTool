from pathlib import Path
import sys


def resource_path(relative_path: str) -> str:
    """Return an asset path that works from source and from a PyInstaller bundle."""
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidate = base_path / relative_path
    if candidate.exists():
        return str(candidate)

    project_root = Path(__file__).resolve().parents[1]
    return str(project_root / relative_path)
