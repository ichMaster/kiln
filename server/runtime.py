"""
kiln tick-server — AgentRuntime (KILN-052): one living agent on a dedicated thread.

Starts `engine.run()` on a daemon thread (the TUI shape) with a `ServerChannel`/`ServerOutput` over
this agent's `BroadcastHub` and its `AgentPaths`. The agent ticks with **no client attached**;
a blocking model call on the agent thread never stalls the async server — it's a different thread.

Exposes `submit` (input → inbox), `subscribe`/`unsubscribe` (client ← hub), `latest_status` (cached,
so a freshly attached client gets state immediately), and `stop` (cooperative — sets the engine's
`stop_event`, so the loop's `finally` still persists/summarizes before the thread ends).
"""

from __future__ import annotations

import queue
import threading

from kiln.brain import Brain
from kiln.config import DEFAULT_AGENT, AgentConfig, AgentPaths, agent_scope

from .bus import BroadcastHub, ServerChannel, ServerOutput, Sink


class AgentRuntime:
    """One agent: an inbox + a hub + the engine loop on its own thread."""

    def __init__(
        self,
        agent_id: str,
        brain: Brain | None = None,
        *,
        ticks: int | None = None,
        live: bool = True,
        paths: AgentPaths | None = None,
        config: AgentConfig | None = None,
        scope: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.hub = BroadcastHub()
        self._inbox: queue.Queue[str] = queue.Queue()
        self._channel = ServerChannel(self._inbox)
        self._output = ServerOutput(self.hub)
        self._brain = brain  # None → engine.run() picks LiveBrain (live) / MockBrain (dry)
        # Default to the agent's standard root; an explicit override is for tests / custom hosting.
        self._paths = paths if paths is not None else AgentPaths.for_agent(agent_id)
        # v1.2 per-agent calibration: the DEFAULT agent runs on the module globals (config=None — a
        # test monkeypatching e.g. eng.TICK_SECONDS still wins, agnika IS the globals); a companion
        # gets its own AgentConfig.for_agent(id). An explicit `config` overrides either.
        if config is not None:
            self._config = config
        elif agent_id and agent_id != DEFAULT_AGENT:
            self._config = AgentConfig.for_agent(agent_id)
        else:
            self._config = None
        # v1.2 permission scope (set, NOT enforced until tools/1.5): home agent broad, rest narrow.
        self.scope = scope if scope is not None else agent_scope(agent_id)
        self._ticks = ticks
        self._live = live
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_status: dict | None = None
        self.hub.subscribe(self._remember_status)  # cache the last status for new attachers

    def _remember_status(self, event: dict) -> None:
        if event.get("kind") == "status":
            self._latest_status = event.get("snapshot")

    def start(self) -> AgentRuntime:
        """Launch the engine loop on a daemon thread (idempotent)."""
        if self._thread is not None:
            return self
        # Lazy import: keeps `import server` light and avoids any import cycle (engine never imports
        # server). engine.run is the same entry the TUI drives on its own thread.
        from kiln.engine import run

        self._thread = threading.Thread(
            target=run,
            kwargs={
                "ticks": self._ticks,
                "live": self._live,
                "channel": self._channel,
                "brain": self._brain,
                "output": self._output,
                "paths": self._paths,
                "stop_event": self._stop,
                "config": self._config,  # v1.2: agent calibration (None = the agnika globals)
            },
            name=f"agent-{self.agent_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def submit(self, text: str) -> None:
        """Queue a user line / slash command for the agent (the WS layer calls this)."""
        self._inbox.put(text)

    def subscribe(self, sink: Sink):
        """Attach a client sink to this agent's event stream; returns an unsubscribe callable."""
        return self.hub.subscribe(sink)

    def unsubscribe(self, sink: Sink) -> None:
        self.hub.unsubscribe(sink)

    def latest_status(self) -> dict | None:
        """The most recent status snapshot (for a one-shot to a just-attached client)."""
        return self._latest_status

    def recent_history(self, limit: int = 20) -> list[dict]:
        """The last `limit` PERSISTED turns across this agent's sessions (snapshot / GET history).
        Read-only, no model calls. The live session's turns aren't persisted until close — they
        stream as `agent` events from attach onward."""
        from kiln.store import load_store

        store = load_store(self._paths.store_file)
        ordered = sorted(store.get("sessions", []), key=lambda s: s.get("started_at") or "")
        turns: list[dict] = []
        for session in ordered:
            turns.extend(store.get("messages", {}).get(session.get("id"), []))
        return turns[-limit:] if limit and limit > 0 else turns

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the loop to finish on its own (e.g. a bounded `ticks=N` run)."""
        if self._thread is not None:
            self._thread.join(timeout)

    def stop(self, timeout: float | None = 5.0) -> None:
        """Ask the loop to exit after the current tick and wait for its `finally` to persist."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
