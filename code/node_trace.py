"""Compact per-node input/output summaries for live execution logs."""

from __future__ import annotations

import json
from typing import Any

from schemas import AgentResult
from skills import resolve_inputs

_MAX = 200


def _trunc(text: str, limit: int = _MAX) -> str:
    t = " ".join(text.split())
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


def _upstream_hint(output: dict | None, skill: str | None = None) -> str:
    if not isinstance(output, dict):
        return "(no output)"
    if skill == "researcher":
        parts = []
        if output.get("question"):
            parts.append(f"q={_trunc(str(output['question']), 80)}")
        sources = output.get("sources") or []
        if sources:
            parts.append(f"{len(sources)} source(s)")
        findings = output.get("findings")
        if isinstance(findings, str) and findings.strip():
            parts.append(f"findings={_trunc(findings)}")
        return " · ".join(parts) or _trunc(json.dumps(output, default=str))
    if skill == "distiller":
        fields = output.get("fields")
        if isinstance(fields, dict) and fields:
            return "fields=" + _trunc(json.dumps(fields, ensure_ascii=False))
    if skill == "coder":
        code = output.get("code")
        if isinstance(code, str) and code.strip():
            lines = len(code.splitlines())
            return f"code ({lines} lines) rationale={_trunc(str(output.get('rationale', '')), 80)}"
    if skill == "sandbox_executor":
        stdout = output.get("stdout")
        if isinstance(stdout, str) and stdout.strip():
            return f"stdout={_trunc(stdout)}"
        return f"exit={output.get('exit_code')}"
    if skill == "critic":
        return (
            f"verdict={output.get('verdict')} "
            f"reason={_trunc(str(output.get('rationale', '')), 120)}"
        )
    if skill == "formatter":
        fa = output.get("final_answer")
        if isinstance(fa, str):
            return f"answer={_trunc(fa)}"
    if skill == "summariser":
        summary = output.get("summary")
        if isinstance(summary, str):
            return f"summary={_trunc(summary)}"
    if skill == "chronologer":
        events = output.get("events") or output.get("timeline")
        if events:
            return f"events={_trunc(json.dumps(events, ensure_ascii=False))}"
    if skill == "retriever":
        hits = output.get("hits") or output.get("chunks")
        if isinstance(hits, list):
            return f"{len(hits)} hit(s)"
    for key in ("rationale", "question", "summary", "final_answer", "findings"):
        if output.get(key):
            return f"{key}={_trunc(str(output[key]))}"
    return _trunc(json.dumps(output, ensure_ascii=False, default=str))


def format_node_inputs(
    nid: str,
    graph_nodes: dict,
    query: str,
) -> list[str]:
    """Human-readable lines describing what this node received."""
    data = graph_nodes.get(nid, {})
    skill = data.get("skill", "?")
    meta = data.get("metadata") or {}
    lines: list[str] = []

    if isinstance(meta, dict):
        if meta.get("question"):
            lines.append(f"question: {_trunc(str(meta['question']))}")
        if meta.get("failure_report"):
            lines.append(f"failure: {_trunc(str(meta['failure_report']))}")
        if meta.get("label"):
            lines.append(f"label: {meta['label']}")

    resolved = resolve_inputs(list(data.get("inputs") or []), graph_nodes, query)
    if not resolved and not lines:
        return ["(no inputs)"]

    for r in resolved:
        kind = r.get("kind", "?")
        rid = r.get("id", "?")
        if kind == "query":
            lines.append(f"USER_QUERY → {_trunc(str(r.get('value', '')))}")
        elif kind == "upstream":
            up_skill = r.get("skill", "?")
            out = r.get("output")
            lines.append(
                f"{rid} ({up_skill}) → {_upstream_hint(out if isinstance(out, dict) else None, up_skill)}"
            )
        elif kind == "upstream-missing":
            lines.append(f"{rid} → (upstream not ready)")
        elif kind == "artifact":
            text = r.get("text", "")
            lines.append(f"{rid} → artifact {_trunc(str(text))}")
        elif kind == "artifact-missing":
            lines.append(f"{rid} → (artifact missing: {r.get('error', '?')})")
        elif kind == "literal":
            lines.append(f"{rid} → {_trunc(str(r.get('value', '')))}")
        else:
            lines.append(f"{rid} → {_trunc(str(r))}")

    if not lines:
        lines.append("(no inputs)")
    return lines


def format_node_output(skill: str, result: AgentResult | None) -> list[str]:
    """Human-readable lines describing what this node produced."""
    if result is None:
        return ["(no result)"]
    if not result.success:
        err = result.error or "unknown error"
        return [f"FAILED: {_trunc(err, 300)}"]

    out = result.output or {}
    lines: list[str] = []

    if skill == "planner":
        if out.get("rationale"):
            lines.append(f"rationale: {_trunc(str(out['rationale']))}")
        nodes = out.get("nodes") or []
        if nodes:
            planned = []
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                label = (n.get("metadata") or {}).get("label", "?")
                planned.append(f"{n.get('skill', '?')}:{label}")
            lines.append(f"plan: {', '.join(planned)}")
        if result.successors:
            succ = []
            for s in result.successors:
                label = (s.metadata or {}).get("label", "?")
                succ.append(f"{s.skill}:{label}")
            lines.append(f"queued: {', '.join(succ)}")
    elif skill == "researcher":
        if out.get("question"):
            lines.append(f"question: {_trunc(str(out['question']))}")
        sources = out.get("sources") or []
        if sources:
            urls = [
                _trunc(str(s.get("url", s)), 80)
                for s in sources[:3]
                if isinstance(s, dict)
            ]
            extra = f" (+{len(sources) - 3} more)" if len(sources) > 3 else ""
            lines.append(f"sources: {', '.join(urls)}{extra}")
        if out.get("findings"):
            lines.append(f"findings: {_trunc(str(out['findings']))}")
    elif skill == "distiller":
        fields = out.get("fields")
        if isinstance(fields, dict):
            lines.append(f"fields: {_trunc(json.dumps(fields, ensure_ascii=False))}")
        else:
            lines.append(_upstream_hint(out, skill))
    elif skill == "coder":
        if out.get("rationale"):
            lines.append(f"rationale: {_trunc(str(out['rationale']))}")
        code = out.get("code")
        if isinstance(code, str):
            preview = "\n".join(code.splitlines()[:6])
            if len(code.splitlines()) > 6:
                preview += "\n…"
            lines.append(f"code ({len(code.splitlines())} lines):\n    " +
                         preview.replace("\n", "\n    "))
    elif skill == "sandbox_executor":
        if out.get("stdout"):
            lines.append(f"stdout:\n    {str(out['stdout']).rstrip().replace(chr(10), chr(10) + '    ')}")
        if out.get("stderr"):
            lines.append(f"stderr: {_trunc(str(out['stderr']))}")
        lines.append(f"exit_code: {out.get('exit_code')}")
    elif skill == "critic":
        lines.append(f"verdict: {out.get('verdict')}")
        if out.get("rationale"):
            lines.append(f"reason: {_trunc(str(out['rationale']))}")
    elif skill == "formatter":
        if out.get("final_answer"):
            lines.append(f"final_answer: {_trunc(str(out['final_answer']), 400)}")
    elif skill == "summariser":
        if out.get("summary"):
            lines.append(f"summary: {_trunc(str(out['summary']))}")
    elif skill == "chronologer":
        events = out.get("events") or out.get("timeline")
        if events:
            lines.append(f"timeline: {_trunc(json.dumps(events, ensure_ascii=False))}")
    elif skill == "retriever":
        hits = out.get("hits") or out.get("chunks") or []
        if isinstance(hits, list):
            lines.append(f"hits: {len(hits)}")
            for h in hits[:3]:
                if isinstance(h, dict):
                    lines.append(f"  - {_trunc(str(h.get('descriptor') or h.get('chunk') or h))}")
    else:
        lines.append(_upstream_hint(out, skill))

    if not lines:
        lines.append(_trunc(json.dumps(out, ensure_ascii=False, default=str)))
    if result.provider:
        lines.append(f"provider: {result.provider}")
    return lines


def print_node_trace(
    nid: str,
    skill: str,
    status: str,
    result: AgentResult | None,
    graph_nodes: dict,
    query: str,
    *,
    elapsed_s: float = 0.0,
) -> None:
    """Print a structured block: header, inputs, outputs."""
    err_suffix = ""
    if result and result.error and status != "complete":
        err_suffix = f"  err={_trunc(result.error, 80)}"
    print(f"[{nid}] {skill:18s} {status:8s} ({elapsed_s:.1f}s){err_suffix}")

    for line in format_node_inputs(nid, graph_nodes, query):
        print(f"  in   {line}")
    for line in format_node_output(skill, result):
        first = True
        for sub in line.split("\n"):
            prefix = "  out  " if first else "       "
            print(f"{prefix}{sub}")
            first = False
    print()
