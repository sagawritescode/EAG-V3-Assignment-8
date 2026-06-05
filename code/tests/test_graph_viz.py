"""Unit tests for graph_viz Mermaid renderers."""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_viz import graph_to_mermaid, plan_to_mermaid
from schemas import NodeSpec


def test_plan_to_mermaid_fan_out() -> None:
    specs = [
        NodeSpec(skill="researcher", inputs=[], metadata={"label": "rEUR"}),
        NodeSpec(skill="researcher", inputs=[], metadata={"label": "rGBP"}),
        NodeSpec(
            skill="coder",
            inputs=["n:rEUR", "n:rGBP"],
            metadata={"label": "calc"},
        ),
        NodeSpec(
            skill="formatter",
            inputs=["USER_QUERY", "n:rEUR", "n:rGBP", "n:calc"],
            metadata={"label": "out"},
        ),
    ]
    md = plan_to_mermaid(specs, parent="n:1", title="planner intent")

    assert "flowchart TD" in md
    assert "rEUR" in md
    assert "rGBP" in md
    assert "calc" in md
    assert "rEUR --> calc" in md or "rEUR_researcher --> calc" in md
    assert "n_1" in md
    assert "USER_QUERY" in md or "n_1 --> out" in md


def test_graph_to_mermaid_status_classes() -> None:
    g = nx.DiGraph()
    g.add_node("n:1", skill="planner", inputs=["USER_QUERY"], status="complete")
    g.add_node("n:2", skill="formatter", inputs=["n:1"], status="pending")
    g.add_edge("n:1", "n:2")

    md = graph_to_mermaid(g, title="executed")

    assert "classDef complete" in md
    assert "classDef pending" in md
    assert "n_1" in md
    assert "n_2" in md
    assert "n_1 --> n_2" in md
    assert "USER_QUERY" in md
