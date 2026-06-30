"""
kiln tick-server — AgentHost (KILN-052): the `agent_id -> AgentRuntime` registry.

v1.1 boots ONE agent — the home agent **agnika** (`config.DEFAULT_AGENT`) — on server boot, so it
ticks server-side with no client. Later versions register more agents under the same map (Pashu =
v1.2); each is `agent_id`-scoped (its own hub, inbox, thread, and `AgentPaths`).
"""

from __future__ import annotations

from kiln.brain import Brain
from kiln.config import DEFAULT_AGENT, SERVER_AGENTS, AgentConfig, AgentPaths

from .runtime import AgentRuntime


class AgentHost:
    """Owns the live agents. `start` is idempotent per `agent_id`; `stop_all` drains on shutdown."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentRuntime] = {}

    def start(
        self,
        agent_id: str,
        brain: Brain | None = None,
        *,
        ticks: int | None = None,
        live: bool = True,
        paths: AgentPaths | None = None,
        config: AgentConfig | None = None,
        scope: str | None = None,
    ) -> AgentRuntime:
        existing = self._agents.get(agent_id)
        if existing is not None:
            return existing
        runtime = AgentRuntime(
            agent_id, brain=brain, ticks=ticks, live=live, paths=paths, config=config, scope=scope
        ).start()
        self._agents[agent_id] = runtime
        return runtime

    def boot_default(self, brain: Brain | None = None, *, live: bool = True) -> AgentRuntime:
        """Start the home agent (agnika) — v1.1's single agent."""
        return self.start(DEFAULT_AGENT, brain=brain, live=live)

    def boot_configured(
        self, agent_ids: list[str] | None = None, brain: Brain | None = None, *, live: bool = True
    ) -> list[AgentRuntime]:
        """v1.2: start every configured agent (server.yaml `agents:` → SERVER_AGENTS by default) —
        each on its own thread with its own AgentConfig + AgentPaths + scope (idempotent per id)."""
        ids = agent_ids if agent_ids is not None else SERVER_AGENTS
        return [self.start(aid, brain=brain, live=live) for aid in ids]

    def get(self, agent_id: str) -> AgentRuntime | None:
        return self._agents.get(agent_id)

    def agents(self) -> list[str]:
        return list(self._agents)

    def stop_all(self, timeout: float | None = 5.0) -> None:
        for runtime in self._agents.values():
            runtime.stop(timeout)
        self._agents.clear()
