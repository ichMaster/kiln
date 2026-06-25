"""kiln — a cheap always-on tick server for live text agents.

Package: config/history/usage (leaves) -> memory -> commands -> engine.
Console entry point is kiln.__main__:main (the `kiln` command or `python -m kiln`).
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["__version__"]


def _read_version() -> str:
    """Version from the root VERSION file (single source of truth; synced with pyproject)."""
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "0.0.0"


__version__ = _read_version()
