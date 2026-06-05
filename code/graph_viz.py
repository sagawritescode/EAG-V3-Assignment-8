"""Mermaid renderers for the Session 8 execution graph.

Two views:
  - plan_to_mermaid: label-based DAG from the Planner's emitted NodeSpecs
  - graph_to_mermaid: the live NetworkX DiGraph with per-node status styling
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import networkx as nx

from schemas import NodeSpec

_STATUS_STYLES = {
    "complete": "fill:#d4edda,stroke:#28a745",
    "failed": "fill:#f8d7da,stroke:#dc3545",
    "skipped": "fill:#e2e3e5,stroke:#6c757d",
    "pending": "fill:#fff3cd,stroke:#ffc107",
    "running": "fill:#cce5ff,stroke:#007bff",
}


def _sanitize_id(raw: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    return out or "node"


def _escape_label(text: str) -> str:
    return text.replace('"', "'")


def _as_dict(spec: NodeSpec | dict) -> dict:
    if isinstance(spec, NodeSpec):
        return spec.model_dump()
    return dict(spec)


def _display_spec(spec: dict) -> str:
    skill = spec.get("skill", "?")
    label = (spec.get("metadata") or {}).get("label")
    if label:
        return f"{skill}/{label}"
    return skill


def plan_to_mermaid(
    specs: Sequence[NodeSpec | dict],
    *,
    parent: str = "planner",
    title: str = "plan",
) -> str:
    """Render the Planner's label-based intent graph as Mermaid flowchart text."""
    norm = [_as_dict(s) for s in specs]
    label_to_mid: dict[str, str] = {}
    node_rows: list[tuple[str, str]] = []
    spec_mids: list[str] = []
    edges: list[tuple[str, str]] = []

    for i, spec in enumerate(norm):
        label = (spec.get("metadata") or {}).get("label")
        mid = _sanitize_id(label or f"{spec.get('skill', 'node')}_{i}")
        if isinstance(label, str) and label:
            label_to_mid[label] = mid
        spec_mids.append(mid)
        node_rows.append((mid, _display_spec(spec)))

    parent_id = _sanitize_id(parent)
    lines = ["flowchart TD", f"    %% {title}", f'    {parent_id}["{_escape_label(parent)}"]']

    for spec, mid in zip(norm, spec_mids):
        inputs = list(spec.get("inputs") or [])
        if not inputs:
            edges.append((parent_id, mid))
            continue

        linked = False
        for inp in inputs:
            if inp == "USER_QUERY":
                edges.append((parent_id, mid))
                linked = True
            elif inp.startswith("n:"):
                suffix = inp[2:]
                if suffix in label_to_mid:
                    edges.append((label_to_mid[suffix], mid))
                    linked = True
            elif inp.startswith("art:"):
                art_id = _sanitize_id(inp)
                if art_id not in {row[0] for row in node_rows}:
                    node_rows.append((art_id, inp))
                edges.append((art_id, mid))
                linked = True
        if not linked:
            edges.append((parent_id, mid))

    for mid, display in node_rows:
        lines.append(f'    {mid}["{_escape_label(display)}"]')
    for src, dst in edges:
        lines.append(f"    {src} --> {dst}")

    return "\n".join(lines)


def graph_to_mermaid(g: nx.DiGraph, *, title: str = "executed") -> str:
    """Render the live orchestrator graph with status-based node styling."""
    lines = ["flowchart TD", f"    %% {title}"]
    classes: dict[str, list[str]] = {k: [] for k in _STATUS_STYLES}

    needs_uq = any(
        "USER_QUERY" in (d.get("inputs") or [])
        for _, d in g.nodes(data=True)
    )
    if needs_uq:
        lines.append('    USER_QUERY["USER_QUERY"]')

    for nid, data in g.nodes(data=True):
        skill = data.get("skill", "?")
        label = (data.get("metadata") or {}).get("label")
        status = data.get("status", "pending")
        mid = _sanitize_id(nid)
        display = nid
        if label:
            display += f" {skill}/{label}"
        else:
            display += f" {skill}"
        display += f" ({status})"
        lines.append(f'    {mid}["{_escape_label(display)}"]')
        if status in classes:
            classes[status].append(mid)

    if needs_uq:
        for nid, data in g.nodes(data=True):
            if "USER_QUERY" not in (data.get("inputs") or []):
                continue
            lines.append(f"    USER_QUERY --> {_sanitize_id(nid)}")

    for src, dst in g.edges():
        lines.append(f"    {_sanitize_id(src)} --> {_sanitize_id(dst)}")

    for status, style in _STATUS_STYLES.items():
        lines.append(f"    classDef {status} {style}")
    for status, mids in classes.items():
        if mids:
            lines.append(f"    class {','.join(mids)} {status}")

    return "\n".join(lines)


def save_mermaid(mermaid: str, path) -> None:
    """Write mermaid text to disk (used for session-dir artifacts)."""
    path.write_text(mermaid)
