"""
kiln — model run accounting (model + spent tokens) and chat colors.

usage_record() is a pure function: it normalizes usage (an SDK object or a CLI
dict) into a stable dict {model, input, output, total}. Each brain branch (seam)
returns it alongside the text; we print it on one line via print_tech() under the answer.
"""

from __future__ import annotations

import json
import sys

BOT_NAME = "Agnika"  # answer label in chat (display only)


def _usage_tokens(usage) -> tuple[int | None, int | None]:
    """(input, output) tokens from usage — an SDK object or a CLI dict; or (None, None)."""
    if usage is None:
        return None, None
    get = usage.get if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    return get("input_tokens"), get("output_tokens")


def usage_record(model: str, usage) -> dict:
    """Normalizes usage into {model, input, output, total} (the brain seam contract)."""
    in_tok, out_tok = _usage_tokens(usage)
    in_tok, out_tok = in_tok or 0, out_tok or 0
    return {"model": model, "input": in_tok, "output": out_tok, "total": in_tok + out_tok}


# Chat line colors (ANSI). Different colors for you and for the bot.
USER_COLOR = "\033[1;36m"  # you — bright cyan
BOT_COLOR = "\033[1;32m"  # bot — bright green
TECH_COLOR = "\033[2;32m"  # technical line — dim green
COLOR_RESET = "\033[0m"


def _c(text: str, color: str) -> str:
    """Colors with ANSI only when output goes to a terminal (not a file/pipe)."""
    return f"{color}{text}{COLOR_RESET}" if sys.stdout.isatty() else text


def print_tech(usage: dict | None, latency: float | None = None) -> None:
    """One compact technical line under the answer (dim green): model + tokens (+ latency)."""
    if not usage:
        return
    m = usage["model"]
    short = m.split("-")[1] if "-" in m else m  # claude-haiku-4-5-… -> haiku
    tail = f" · {round(latency, 2)}s" if latency is not None else ""
    print(
        _c(
            f"      · {short} · {usage['input']}→{usage['output']} tok ({usage['total']}){tail}",
            TECH_COLOR,
        )
    )


def _cli_error_detail(result) -> str:
    """Readable reason for a failed claude call: stderr or the `result` field
    from JSON stdout (where --output-format json puts the error text)."""
    if result.stderr.strip():
        return result.stderr.strip()[:300]
    out = result.stdout.strip()
    if not out:
        return "no details"
    try:
        return ((json.loads(out).get("result") or out).strip())[:300]
    except json.JSONDecodeError:
        return out[:300]
