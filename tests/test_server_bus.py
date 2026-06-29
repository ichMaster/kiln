"""
KILN-050: the network bus — `ServerChannel` / `ServerOutput` / `BroadcastHub`.

The network form of the v0 `Bridge`: `ServerOutput` satisfies the `Output` seam and fans events to
every subscriber via the hub; `user()` is echo-free; `ServerChannel` drains an inbox queue. Pure
stdlib + `kiln.output` (no `[server]` extra needed). No paid calls.
"""

from __future__ import annotations

import queue

from kiln.output import Output
from server.bus import BroadcastHub, ServerChannel, ServerOutput


def test_serveroutput_satisfies_output_protocol():
    assert isinstance(ServerOutput(BroadcastHub()), Output)


def test_serveroutput_fans_events_to_two_subscribers():
    hub = BroadcastHub()
    a: list[dict] = []
    b: list[dict] = []
    hub.subscribe(a.append)
    hub.subscribe(b.append)
    out = ServerOutput(hub)

    out.agent("привіт", is_self=True, model="haiku", is_curiosity=True)
    out.usage({"model": "m", "total": 3}, latency=0.5)
    out.notice("[exit] saved")
    out.status({"status": "idle"})

    assert a == b  # both subscribers see the same session
    kinds = [e["kind"] for e in a]
    assert kinds == ["agent", "usage", "notice", "status"]
    assert a[0]["is_self"] is True and a[0]["is_curiosity"] is True and a[0]["model"] == "haiku"


def test_serveroutput_user_is_echo_free():
    hub = BroadcastHub()
    seen: list[dict] = []
    hub.subscribe(seen.append)
    ServerOutput(hub).user("моє повідомлення")
    assert seen == []  # input is never echoed onto the bus


def test_serverchannel_drains_inbox_then_none():
    q: queue.Queue[str] = queue.Queue()
    q.put("перший")
    q.put("другий")
    ch = ServerChannel(q)
    assert ch.poll() == "перший"
    assert ch.poll() == "другий"
    assert ch.poll() is None  # empty -> None, non-blocking (the loop never stalls)


def test_broadcast_hub_drops_a_raising_sink_without_blocking_others():
    hub = BroadcastHub()
    good: list[dict] = []

    def dead(_event):
        raise RuntimeError("client gone")

    hub.subscribe(dead)
    hub.subscribe(good.append)
    hub.broadcast({"kind": "notice", "text": "x"})  # dead raises -> dropped; good still gets it
    hub.broadcast({"kind": "notice", "text": "y"})

    assert good == [{"kind": "notice", "text": "x"}, {"kind": "notice", "text": "y"}]
    assert hub.subscriber_count() == 1  # the dead sink was dropped


def test_unsubscribe_stops_delivery():
    hub = BroadcastHub()
    seen: list[dict] = []
    off = hub.subscribe(seen.append)
    hub.broadcast({"kind": "notice", "text": "x"})
    off()
    hub.broadcast({"kind": "notice", "text": "y"})
    assert seen == [{"kind": "notice", "text": "x"}]  # nothing after unsubscribe
