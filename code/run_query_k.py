#!/usr/bin/env python3
"""Query K resumable-execution demo (queries.md).

From the code/ directory:

  uv run python run_query_k.py          # run, auto-SIGKILL at 2/3 researchers done
  uv run python run_query_k.py --resume

Graph state persists under state/sessions/s8_K_resumed_v2/.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import networkx as nx

from flow import Executor
from persistence import SessionStore

QUERY = (
    "For Lagos, Cairo, and Kinshasa, find current populations and growth rates "
    "and tell me which is growing fastest."
)
SESSION_ID = "s8_K_resumed_v2"


def _load_graph(session_id: str) -> nx.DiGraph | None:
    store = SessionStore(session_id)
    try:
        return store.read_graph()
    except Exception:
        return None


def _query_is_complete(session_id: str) -> bool:
    g = _load_graph(session_id)
    if g is None:
        return False
    return any(
        d.get("skill") == "formatter" and d.get("status") == "complete"
        for _, d in g.nodes(data=True)
    )


def _query_needs_resume(session_id: str) -> bool:
    return _load_graph(session_id) is not None and not _query_is_complete(session_id)


def _kill_ready(session_id: str) -> bool:
    """True mid-gather: planner complete, 2/3 researchers complete, 1 running."""
    g = _load_graph(session_id)
    if g is None:
        return False

    by_skill: dict[str, list[str]] = {}
    for nid, data in g.nodes(data=True):
        skill = data.get("skill", "")
        by_skill.setdefault(skill, []).append(nid)

    planner = by_skill.get("planner", [])
    researchers = by_skill.get("researcher", [])
    if len(planner) != 1 or len(researchers) != 3:
        return False

    if g.nodes[planner[0]].get("status") != "complete":
        return False

    for nid in by_skill.get("formatter", []):
        if g.nodes[nid].get("status") != "pending":
            return False

    statuses = [g.nodes[n].get("status") for n in researchers]
    complete = sum(1 for s in statuses if s == "complete")
    running = sum(1 for s in statuses if s == "running")
    return complete == 2 and running == 1


def _run_kill_phase(
    *,
    show_graph: bool = False,
    poll_s: float = 0.15,
    timeout_s: float = 20.0,
) -> None:
    """Start Query K and SIGKILL when the parallel Researcher layer matches queries.md."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "_worker"]
    if show_graph:
        cmd.append("--show-graph")
    print(f"[run_query_k] kill phase: spawning worker (session {SESSION_ID})")
    proc = subprocess.Popen(cmd, cwd=Path(__file__).parent)
    deadline = time.monotonic() + timeout_s
    killed = False
    try:
        while proc.poll() is None:
            if time.monotonic() > deadline:
                proc.terminate()
                proc.wait(timeout=10)
                raise RuntimeError(f"kill phase timed out after {timeout_s:.0f}s")
            if _kill_ready(SESSION_ID):
                os.kill(proc.pid, signal.SIGKILL)
                killed = True
                print(
                    "[run_query_k] SIGKILL sent (2 researchers done, 1 running)"
                )
                break
            time.sleep(poll_s)
    finally:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    if killed:
        print(f"[run_query_k] kill phase complete; resume with:")
        print("  uv run python run_query_k.py --resume")
        return

    if _query_is_complete(SESSION_ID):
        print(
            "[run_query_k] worker finished before SIGKILL; "
            "session is already complete (resume skipped)"
        )
        return
    if _query_needs_resume(SESSION_ID):
        print(
            f"[run_query_k] worker stopped early (exit {proc.returncode}); "
            f"session {SESSION_ID} saved for resume"
        )
        return

    raise RuntimeError(
        f"kill phase ended in an unexpected state (exit {proc.returncode}); "
        f"inspect state/sessions/{SESSION_ID}/"
    )


def _worker_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-graph", action="store_true")
    args = parser.parse_args(argv)
    asyncio.run(
        Executor().run(
            QUERY,
            session_id=SESSION_ID,
            resume=False,
            show_graph=args.show_graph,
        )
    )


def main(argv: list[str] | None = None) -> int:
    cli = sys.argv[1:] if argv is None else argv
    if cli and cli[0] == "_worker":
        _worker_main(cli[1:])
        return 0

    parser = argparse.ArgumentParser(
        description="Run Query K (SIGKILL + resume demo).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the persisted session after a SIGKILL",
    )
    parser.add_argument(
        "--show-graph",
        action="store_true",
        help="Print PLAN / EXPANDED / EXECUTED Mermaid graphs",
    )
    args = parser.parse_args(cli)

    if args.resume:
        print(f"[run_query_k] resuming session {SESSION_ID}")
        asyncio.run(
            Executor().run(
                QUERY,
                session_id=SESSION_ID,
                resume=True,
                show_graph=args.show_graph,
            )
        )
        return 0

    _run_kill_phase(show_graph=args.show_graph)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
