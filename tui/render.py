"""
kiln.tui.render — pure formatting for the status bar / needs panel (no textual).

Kept textual-free so the formatting is unit-tested directly; the widgets just render
these strings. Input is the per-tick status snapshot (engine._status_snapshot):
{status, model, branch, needs, thresholds, hottest, cooldowns, stats}.
"""

from __future__ import annotations


def short_model(model: str) -> str:
    """`claude-opus-4-8` -> `opus-4-8` (drops the vendor prefix)."""
    return model.removeprefix("claude-") if model else "—"


def fmt_tok(n: int) -> str:
    """Compact token count: 4321 -> `4.3k`, 42 -> `42`."""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def agent_label(is_self: bool, model: str | None = None) -> str:
    """Reply label: `Agnika [model]`; self-triggered replies are marked `Agnika (self)`."""
    base = "Agnika (self)" if is_self else "Agnika"
    return f"{base} [{model}]" if model else base


def status_line1(snap: dict) -> str:
    """Top status line: status · model · branch · hottest need."""
    status = snap.get("status", "idle")
    model = short_model(snap.get("model", ""))
    branch = snap.get("branch") or "—"
    hottest = snap.get("hottest") or ["", 0.0]
    need, level = hottest[0], hottest[1]
    hot = f"{need} {level:.2f}" if need else "—"
    return f"status: {status} · {model} · {branch} · hottest: {hot}"


def status_line2(snap: dict) -> str:
    """Stats line: last-turn tokens + latency, session totals, average latency."""
    s = snap.get("stats", {})
    if not s.get("turns"):
        return "stats: (no turns yet)"
    return (
        f"stats: last {fmt_tok(s['last_tokens'])} tok · {s['last_latency']}s · "
        f"{s['turns']} turns · {fmt_tok(s['tokens_total'])} tok · avg {s['avg_latency']}s"
    )


def _bar(level: float, width: int = 10) -> str:
    """A small fill bar for a 0..1 level."""
    filled = max(0, min(width, round(level * width)))
    return "█" * filled + "░" * (width - filled)


def need_color(level: float, threshold: float | None) -> str:
    """Color by closeness to the trigger threshold: green (calm) -> yellow -> red (at/over)."""
    if not threshold:
        return "white"
    ratio = level / threshold
    if ratio >= 1.0:
        return "red"
    if ratio >= 0.8:
        return "yellow"
    return "green"


def needs_panel_lines(snap: dict) -> list[str]:
    """
    One row per need: `* name  ███░░░░░░░ level/threshold ! cdN → action`, the bar
    colored by closeness to the threshold (green -> yellow -> red). `*` marks the
    hottest need, `!` an at/over-threshold need, `cdN` an active cooldown, and
    `→ action` is the branch that addresses it (NEED_TRIGGERS: chat/deep/idle). This
    is where Lumi shows model reasoning — kiln shows the motivational substrate.
    """
    needs = snap.get("needs", {})
    thresholds = snap.get("thresholds", {})
    actions = snap.get("actions", {})
    cooldowns = snap.get("cooldowns", {})
    hottest = (snap.get("hottest") or ["", 0.0])[0]
    rows: list[str] = []
    for name, level in needs.items():
        thr = thresholds.get(name)
        mark = "*" if name == hottest else " "
        thr_s = f"/{thr:.2f}" if thr is not None else ""
        flag = " !" if (thr is not None and level >= thr) else ""
        cd = cooldowns.get(name, 0)
        cd_s = f" cd{cd}" if cd else ""
        action = actions.get(name)
        action_s = f" → {action}" if action else ""
        color = need_color(level, thr)
        rows.append(
            f"{mark}{name:<10} [{color}]{_bar(level)}[/] {level:.2f}{thr_s}{flag}{cd_s}{action_s}"
        )
    return rows
