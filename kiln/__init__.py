"""kiln — дешевий always-on tick-сервер для живих текстових агентів.

Пакет: config/history/usage (листки) -> memory -> commands -> engine.
Консольний вхід — kiln.__main__:main (команда `kiln` або `python -m kiln`).
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["__version__"]


def _read_version() -> str:
    """Версія з кореневого файлу VERSION (єдине джерело правди; синхрон із pyproject)."""
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "0.0.0"


__version__ = _read_version()
