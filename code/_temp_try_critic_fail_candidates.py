#!/usr/bin/env python3
"""TEMPORARY — batch-try critic_fail candidate queries.

Delete this file once you pick the final critic_fail query for run_queries.py.

Usage (from code/):
  uv run python _temp_try_critic_fail_candidates.py --list
  uv run python _temp_try_critic_fail_candidates.py --all
  uv run python _temp_try_critic_fail_candidates.py moby_exact hobbit_subtitle
  uv run python _temp_try_critic_fail_candidates.py --all --memory   # clear memory each run

Results append to state/critic_fail_candidate_results.json
Sessions live under state/sessions/s8_cand_<key>/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx

from flow import Executor
from persistence import SESSIONS_ROOT, SessionStore
from schemas import AgentResult

# ---------------------------------------------------------------------------
# Candidate queries — edit or delete this block when done experimenting.
# ---------------------------------------------------------------------------
CANDIDATES: dict[str, tuple[str, str]] = {
    # Tier 1 — exact strings / many fields (best critic_fail candidates)
    "moby_exact": (
        "Moby-Dick: year + publisher + exact full title",
        (
            "Fetch https://en.wikipedia.org/wiki/Moby-Dick and tell me the year "
            "of first publication, the original publisher, and the novel's full "
            "title exactly as stated on Wikipedia."
        ),
    ),
    "hobbit_subtitle": (
        "The Hobbit: author + year + protagonist + exact full subtitle",
        (
            "Fetch https://en.wikipedia.org/wiki/The_Hobbit and tell me the author, "
            "year of first publication, the protagonist's name, and the novel's full "
            "subtitle exactly as stated on Wikipedia."
        ),
    ),
    "pride_title": (
        "Pride and Prejudice: author + year + publisher + exact full title",
        (
            "Fetch https://en.wikipedia.org/wiki/Pride_and_Prejudice and tell me the "
            "author, year of first publication, original publisher, and the novel's "
            "full title exactly as stated on Wikipedia."
        ),
    ),
    "1984_dual": (
        "Nineteen Eighty-Four: UK vs US publication year + publishers",
        (
            "Fetch https://en.wikipedia.org/wiki/Nineteen_Eighty-Four and tell me "
            "George Orwell's name, the UK publication year, the US publication year, "
            "and the publisher for each country."
        ),
    ),
    # Tier 2 — similar-name confusion
    "hobbit_lotr": (
        "The Hobbit: Bilbo vs Frodo (same page, different heroes)",
        (
            "Fetch https://en.wikipedia.org/wiki/The_Hobbit and tell me the "
            "protagonist of The Hobbit, the author, and the hobbit who carries the "
            "One Ring in The Lord of the Rings."
        ),
    ),
    "adams_family": (
        "John Adams: birth year vs John Quincy Adams birth year",
        (
            "Fetch https://en.wikipedia.org/wiki/John_Adams and tell me John Adams's "
            "birth year, the year he became U.S. president, and John Quincy Adams's "
            "birth year."
        ),
    ),
    "frankenstein": (
        "Frankenstein: author vs scientist vs monster name",
        (
            "Fetch https://en.wikipedia.org/wiki/Frankenstein and tell me the "
            "author's name, the name of the scientist who creates the monster, "
            "and the name of the monster."
        ),
    ),
    # Tier 3 — numeric / unit precision
    "golden_gate": (
        "Golden Gate Bridge: length in feet + exact paint color name",
        (
            "Fetch https://en.wikipedia.org/wiki/Golden_Gate_Bridge and tell me the "
            "year it opened, its total length in feet, and the official name of its "
            "paint color exactly as Wikipedia states it."
        ),
    ),
    "everest_heights": (
        "Mount Everest: 2020 vs 1955 survey elevations",
        (
            "Fetch https://en.wikipedia.org/wiki/Mount_Everest and tell me the "
            "elevation in meters from the 2020 survey and the elevation from the "
            "1955 survey."
        ),
    ),
    "statue_liberty": (
        "Statue of Liberty: four infobox fields",
        (
            "Fetch https://en.wikipedia.org/wiki/Statue_of_Liberty and tell me the "
            "year it was dedicated, its height in feet, the designer's name, and the "
            "island it stands on."
        ),
    ),
}

CANDIDATE_ORDER = tuple(CANDIDATES.keys())
RESULTS_PATH = SESSIONS_ROOT.parent / "critic_fail_candidate_results.json"


@dataclass
class RunOutcome:
    key: str
    label: str
    session_id: str
    elapsed_s: float
    path: str
    had_distiller: bool
    had_auto_critic: bool
    first_critic_verdict: str | None
    critic_fail_count: int
    recovery_count: int
    distiller_fields: dict | None
    first_fail_rationale: str | None
    final_answer_preview: str
    error: str | None
    ran_at: str


def _session_id(key: str) -> str:
    return f"s8_cand_{key}"


def _clear_candidate_session(key: str) -> None:
    path = SESSIONS_ROOT / _session_id(key)
    if path.is_dir():
        shutil.rmtree(path)


def _load_graph(session_id: str) -> nx.DiGraph | None:
    try:
        return SessionStore(session_id).read_graph()
    except Exception:
        return None


def _node_output(data: dict) -> dict:
    """Extract skill output dict from a graph node (AgentResult or plain dict)."""
    result = data.get("result")
    if result is None:
        return {}
    if isinstance(result, AgentResult):
        return result.output or {}
    if isinstance(result, dict):
        return result.get("output") or {}
    return {}


def _skill_path(g: nx.DiGraph) -> str:
    order: list[str] = []
    seen: set[str] = set()
    for nid in nx.topological_sort(g):
        skill = g.nodes[nid].get("skill", "?")
        if skill not in seen:
            order.append(skill)
            seen.add(skill)
    return " → ".join(order)


def _analyze(session_id: str, answer: str) -> dict:
    g = _load_graph(session_id)
    if g is None:
        return {
            "path": "(no graph)",
            "had_distiller": False,
            "had_auto_critic": False,
            "first_critic_verdict": None,
            "critic_fail_count": 0,
            "recovery_count": 0,
            "distiller_fields": None,
            "first_fail_rationale": None,
        }

    critics = [
        (nid, g.nodes[nid])
        for nid in nx.topological_sort(g)
        if g.nodes[nid].get("skill") == "critic"
    ]
    distillers = [
        (nid, g.nodes[nid])
        for nid in nx.topological_sort(g)
        if g.nodes[nid].get("skill") == "distiller"
    ]

    first_verdict = None
    fail_count = 0
    first_fail_rationale = None
    for _, data in critics:
        out = _node_output(data)
        verdict = out.get("verdict")
        if first_verdict is None:
            first_verdict = verdict
        if verdict == "fail":
            fail_count += 1
            if first_fail_rationale is None:
                first_fail_rationale = out.get("rationale")

    recovery_count = sum(
        1 for _, data in g.nodes(data=True)
        if data.get("skill") == "planner" and data.get("status") == "complete"
    ) - 1  # subtract initial planner
    recovery_count = max(0, recovery_count)

    distiller_fields = None
    if distillers:
        out = _node_output(distillers[0][1])
        distiller_fields = out.get("fields")

    return {
        "path": _skill_path(g),
        "had_distiller": bool(distillers),
        "had_auto_critic": bool(critics),
        "first_critic_verdict": first_verdict,
        "critic_fail_count": fail_count,
        "recovery_count": recovery_count,
        "distiller_fields": distiller_fields,
        "first_fail_rationale": first_fail_rationale,
    }


def _append_result(outcome: RunOutcome) -> None:
    rows: list[dict] = []
    if RESULTS_PATH.exists():
        rows = json.loads(RESULTS_PATH.read_text())
    rows.append(asdict(outcome))
    RESULTS_PATH.write_text(json.dumps(rows, indent=2))


def _print_summary(rows: list[RunOutcome]) -> None:
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    hdr = f"{'key':<18} {'dist?':<6} {'crit?':<6} {'1st':<6} {'fails':<6} {'recv':<5} path"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if r.error:
            print(f"{r.key:<18} ERROR: {r.error}")
            continue
        print(
            f"{r.key:<18} "
            f"{'yes' if r.had_distiller else 'no':<6} "
            f"{'yes' if r.had_auto_critic else 'no':<6} "
            f"{r.first_critic_verdict or '-':<6} "
            f"{r.critic_fail_count:<6} "
            f"{r.recovery_count:<5} "
            f"{r.path}"
        )
        if r.first_fail_rationale:
            print(f"  fail: {r.first_fail_rationale[:100]}")
    print(f"\nFull log: {RESULTS_PATH}")


async def _run_candidate(key: str, *, clear_memory: bool) -> RunOutcome:
    if key not in CANDIDATES:
        raise SystemExit(f"unknown candidate {key!r}; use --list")

    label, query = CANDIDATES[key]
    sid = _session_id(key)
    _clear_candidate_session(key)

    if clear_memory:
        import memory as memory_svc
        memory_svc.clear()

    print(f"\n{'#' * 78}")
    print(f"[candidates] {key}: {label}")
    print(f"[candidates] session={sid}")
    print(f"{'#' * 78}")

    t0 = time.perf_counter()
    error = None
    answer = ""
    try:
        answer = await Executor().run(query, session_id=sid, resume=False)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - t0

    analysis = _analyze(sid, answer)
    preview = answer.replace("\n", " ")[:120] if answer else "(empty)"
    outcome = RunOutcome(
        key=key,
        label=label,
        session_id=sid,
        elapsed_s=round(elapsed, 1),
        path=analysis["path"],
        had_distiller=analysis["had_distiller"],
        had_auto_critic=analysis["had_auto_critic"],
        first_critic_verdict=analysis["first_critic_verdict"],
        critic_fail_count=analysis["critic_fail_count"],
        recovery_count=analysis["recovery_count"],
        distiller_fields=analysis["distiller_fields"],
        first_fail_rationale=analysis["first_fail_rationale"],
        final_answer_preview=preview,
        error=error,
        ran_at=datetime.now(timezone.utc).isoformat(),
    )
    _append_result(outcome)
    print(f"[candidates] done in {elapsed:.1f}s → 1st critic={outcome.first_critic_verdict}, "
          f"fails={outcome.critic_fail_count}, recovery={outcome.recovery_count}")
    return outcome


def _list_candidates() -> None:
    print("TEMPORARY critic_fail candidates (delete _temp_try_critic_fail_candidates.py when done):\n")
    for key in CANDIDATE_ORDER:
        label, text = CANDIDATES[key]
        preview = text.replace("\n", " ")
        if len(preview) > 70:
            preview = preview[:67] + "..."
        print(f"  {key:<18}  {label}")
        print(f"                    {preview}\n")
    print("Run all:  uv run python _temp_try_critic_fail_candidates.py --all")
    print("Run one:  uv run python _temp_try_critic_fail_candidates.py moby_exact")


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description="TEMP: try critic_fail candidate queries (safe to delete later)",
    )
    parser.add_argument("keys", nargs="*", help=f"candidate keys (default: --all → {len(CANDIDATES)} queries)")
    parser.add_argument("--all", action="store_true", help="run every candidate")
    parser.add_argument("--list", action="store_true", help="print catalogue and exit")
    parser.add_argument("--memory", action="store_true", help="clear memory before each run")
    parser.add_argument("--reset-results", action="store_true", help="wipe results JSON before run")
    args = parser.parse_args()

    if args.list:
        _list_candidates()
        return

    if args.reset_results and RESULTS_PATH.exists():
        RESULTS_PATH.unlink()
        print(f"[candidates] cleared {RESULTS_PATH}")

    keys = list(CANDIDATE_ORDER) if args.all or not args.keys else args.keys
    unknown = [k for k in keys if k not in CANDIDATES]
    if unknown:
        raise SystemExit(f"unknown keys: {unknown}; use --list")

    outcomes: list[RunOutcome] = []
    for i, key in enumerate(keys, 1):
        print(f"\n[candidates] {i}/{len(keys)}")
        outcomes.append(await _run_candidate(key, clear_memory=args.memory))

    _print_summary(outcomes)


if __name__ == "__main__":
    asyncio.run(_main())
