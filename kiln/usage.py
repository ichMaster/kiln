"""
kiln — облік виконання моделі (модель + витрачені токени) і кольори чату.

usage_record() — чиста функція: нормалізує usage (об'єкт SDK чи словник CLI) у
стабільний словник {model, input, output, total}. Його повертає кожна гілка
мозку (seam) разом із текстом; друкуємо одним рядком print_tech() під відповіддю.
"""

from __future__ import annotations

import json
import sys

BOT_NAME = "Agnika"               # підпис відповіді в чаті (лише відображення)


def _usage_tokens(usage) -> tuple[int | None, int | None]:
    """(input, output) токени з usage — об'єкта SDK чи словника CLI; або (None, None)."""
    if usage is None:
        return None, None
    get = usage.get if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    return get("input_tokens"), get("output_tokens")


def usage_record(model: str, usage) -> dict:
    """Нормалізує usage у {model, input, output, total} (контракт seam'а мозку)."""
    in_tok, out_tok = _usage_tokens(usage)
    in_tok, out_tok = in_tok or 0, out_tok or 0
    return {"model": model, "input": in_tok, "output": out_tok, "total": in_tok + out_tok}


# Кольори рядків чату (ANSI). Різні кольори для тебе й для бота.
USER_COLOR = "\033[1;36m"        # ти — яскраво-блакитний
BOT_COLOR = "\033[1;32m"         # бот — яскраво-зелений
TECH_COLOR = "\033[2;32m"        # технічний рядок — тьмяно-зелений
COLOR_RESET = "\033[0m"


def _c(text: str, color: str) -> str:
    """Фарбує ANSI-кольором лише коли вивід іде в термінал (не в файл/пайп)."""
    return f"{color}{text}{COLOR_RESET}" if sys.stdout.isatty() else text


def print_tech(usage: dict | None) -> None:
    """Один компактний технічний рядок під відповіддю (тьмяно-зелений): модель + токени."""
    if not usage:
        return
    m = usage["model"]
    short = m.split("-")[1] if "-" in m else m       # claude-haiku-4-5-… -> haiku
    print(_c(f"      · {short} · {usage['input']}→{usage['output']} ток ({usage['total']})", TECH_COLOR))


def _cli_error_detail(result) -> str:
    """Зрозуміла причина невдалого claude-виклику: stderr або поле `result`
    із JSON stdout (туди --output-format json кладе текст помилки)."""
    if result.stderr.strip():
        return result.stderr.strip()[:300]
    out = result.stdout.strip()
    if not out:
        return "без деталей"
    try:
        return ((json.loads(out).get("result") or out).strip())[:300]
    except json.JSONDecodeError:
        return out[:300]
