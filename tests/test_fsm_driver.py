"""
KILN-064 — the FSM driver: run() over the event queue + advance + the action registry. Integration
against MockBrain (zero paid calls): a scripted user.message drives idle → responding → idle; a
seeded connection crossing drives the reach-out path; quiet ticks stay idle — all matching v1.2.
(The whole existing suite + the deterministic dry-run are the byte-for-byte pin; here we assert the
driver's status sequence + effects directly.)
"""

from kiln import engine as eng
from kiln.brain import MockBrain
from kiln.config import AgentPaths
from kiln.engine import State, save_state


def _tmp_paths(root) -> AgentPaths:
    s, k = root / "state", root / "kiln"
    return AgentPaths(
        state_dir=s,
        needs_file=k / "needs.json",
        store_file=k / "store.json",
        usage_ledger=k / "usage-ledger.jsonl",
        usage_report=k / "usage-report.md",
        canon_file=s / "canon.md",
        prompts_file=s / "prompts.md",
    )


class Rec:
    """Records the Output seam so we can assert the per-tick status sequence + what was emitted."""

    def __init__(self):
        self.statuses: list[str] = []
        self.agents: list[tuple[str, dict]] = []
        self.users: list[str] = []
        self.notices: list[str] = []

    def user(self, t):
        self.users.append(t)

    def agent(self, t, **k):
        self.agents.append((t, k))

    def usage(self, u, latency=None):
        pass

    def notice(self, t):
        self.notices.append(t)

    def status(self, s):
        self.statuses.append(s["status"])


def _run(paths, rec, inputs=None, ticks=3):
    eng.run(
        ticks=ticks,
        live=False,
        channel=eng.ScriptedChannel(inputs or {}),
        brain=MockBrain(),
        output=rec,
        paths=paths,
    )


# --- a user turn: idle → responding → idle ------------------------------------


def test_user_message_drives_idle_responding_idle(tmp_path):
    rec = Rec()
    _run(_tmp_paths(tmp_path / "a"), rec, inputs={0: "привіт"}, ticks=3)
    # tick 0 answers (responding); the quiet ticks fall back to idle
    assert rec.statuses[0] == "responding"
    assert rec.statuses[-1] == "idle"
    # a normal turn echoes the user and emits a (non-self) reply
    assert rec.users == ["привіт"]
    replies = [k for _, k in rec.agents]
    assert replies and not any(k.get("is_self") for k in replies)


# --- a connection crossing: she reaches out (is_self) -------------------------


def test_connection_crossing_drives_reach_out(tmp_path):
    paths = _tmp_paths(tmp_path / "b")
    paths.needs_file.parent.mkdir(parents=True, exist_ok=True)
    save_state(State(needs={"connection": 0.9}), paths.needs_file)  # above the reach-out threshold
    rec = Rec()
    _run(paths, rec, inputs={}, ticks=2)
    # she speaks first this tick — responding, via an is_self line, and no user was echoed
    assert "responding" in rec.statuses
    assert any(k.get("is_self") for _, k in rec.agents)
    assert rec.users == []


# --- nothing to say: quiet ticks stay idle ------------------------------------


def test_quiet_ticks_stay_idle(tmp_path):
    rec = Rec()
    _run(_tmp_paths(tmp_path / "c"), rec, inputs={}, ticks=3)
    assert set(rec.statuses) == {"idle"}
    assert rec.agents == []  # a silent tick prints nothing


# --- the driver actually drove the FSM (not the old if/elif) ------------------


def test_driver_uses_the_registry_and_table(tmp_path):
    # a light structural pin: run() builds a registry covering the table's actions, and the driver
    # goes through advance/fire (exercised above). Here just assert the seam run() relies on holds.
    from kiln import fsm

    assert eng.default_registry().actions() == fsm.table_actions()
