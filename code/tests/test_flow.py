"""Unit tests for flow.Graph.extend_from critic auto-insertion."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow import Graph
from schemas import AgentResult
from skills import SkillRegistry


def test_critic_auto_inserted_before_preplanned_pending_successor() -> None:
    """distiller:critic must gate formatter even when the Planner pre-wired it."""
    graph = Graph()
    registry = SkillRegistry()
    dist_nid = graph.add_node("distiller", inputs=["n:1"])
    fmt_nid = graph.add_node("formatter", inputs=[dist_nid])
    graph.g.add_edge(dist_nid, fmt_nid)
    graph.mark(dist_nid, "complete")

    result = AgentResult(success=True, agent_name="distiller", output={"fields": {}})
    added = graph.extend_from(dist_nid, result, registry=registry)

    critic_nodes = [
        nid for nid, d in graph.g.nodes(data=True) if d.get("skill") == "critic"
    ]
    assert len(critic_nodes) == 1
    critic_nid = critic_nodes[0]
    assert graph.g.has_edge(dist_nid, critic_nid)
    assert graph.g.has_edge(critic_nid, fmt_nid)
    assert not graph.g.has_edge(dist_nid, fmt_nid)
    assert graph.g.nodes[critic_nid]["metadata"] == {
        "target": dist_nid,
        "child": fmt_nid,
    }
    assert critic_nid in added
