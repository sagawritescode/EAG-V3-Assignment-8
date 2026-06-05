#!/usr/bin/env python3
"""Run Session 8 validation queries from queries.md / Assignment.md.

Usage (from the code/ directory):
  uv run python run_queries.py --list
  uv run python run_queries.py --clear              # wipe session traces
  uv run python run_queries.py --clear --memory     # sessions + memory index
  uv run python run_queries.py --all                # base five (hello, a, i, j, k)
  uv run python run_queries.py --all-queries        # base five + assignment queries
  uv run python run_queries.py hello a j
  uv run python run_query_k.py                      # SIGKILL demo (auto kill + --resume)
  uv run python run_queries.py companies currency --show-graph
  uv run python run_queries.py critic_pass critic_fail
  uv run python run_queries.py --all-queries --clear --show-graph
  uv run python run_queries.py --all-queries          # saves progress after each query
  uv run python run_queries.py --resume-run           # continue after interrupt or K failure

Base query keys: hello, a, i, j, k
Assignment query keys: companies, currency, critic_pass, critic_fail, chrono
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import signal
import sys
import time
from dataclasses import asdict, dataclass, field, fields
import networkx as nx

from flow import Executor
from persistence import SESSIONS_ROOT, SessionStore

# Verbatim query texts from queries.md (Query I: see note below).
QUERIES: dict[str, tuple[str, str]] = {
    "hello": (
        "Query hello",
        "Say hello.",
    ),
    "a": (
        "Query A",
        (
            "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth "
            "date, death date, and three key contributions to information theory."
        ),
    ),
    # queries.md references a "populations section" that is not in the file;
    # this is the S8 parallel-fan-out validation query (planner.md / README).
    "i": (
        "Query I",
        (
            "Find the populations of London, Paris, Berlin and tell me which two "
            "are closest in size."
        ),
    ),
    "j": (
        "Query J",
        "Read /nonexistent/path.txt and tell me what's in it.",
    ),
    "k": (
        "Query K",
        (
            "For Lagos, Cairo, and Kinshasa, find current populations and growth rates "
            "and tell me which is growing fastest."
        ),
    ),
    "companies": (
        "Query companies fan-out (Assignment part 2)",
        (
            "What year were Apple, Microsoft, and Google founded? List each "
            "company's founding year and tell me which company is the oldest."
        ),
    ),
    "currency": (
        "Query currency fan-out + coder (Assignment part 4)",
        (
            "Fetch the current USD exchange rates for EUR, GBP, and JPY. "
            "Which currency moved most vs USD in the last week?"
        ),
    ),
    # "chess": (
    #     "Query chess move evaluation + coder",
    #     (
    #         "In the 2013 World Chess Championship game 6 (Carlsen vs Anand), "
    #         "find the engine evaluations for moves 20, 22, and 24. Which of "
    #         "these three moves had the highest evaluation?"
    #     ),
    # ),
    "critic_pass": (
        "Query critic pass (distiller auto-critic, Assignment part 3)",
        (
            "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth "
            "date, death date, and three key contributions to information theory."
        ),
    ),
    "critic_fail": (
        "Query critic fail + recovery (planner-emitted critic, Assignment part 3)",
        (
            "Research the Apollo 11 moon landing. Write exactly one haiku in 5-7-5 "
            "syllable form. Your final answer must be only the three haiku lines — "
            "no title, no explanation, no preamble."
        ),
    ),
    "chrono": (
        "Query chronologer (Assignment part 5)",
        (
            "Research the key milestones of the James Webb Space Telescope from "
            "program announcement through first science images. Present them in "
            "chronological order."
        ),
    ),
}

BASE_QUERY_ORDER = ("hello", "a", "i", "j", "k")
ASSIGNMENT_QUERY_ORDER = (
    "companies", "currency", "critic_pass", "critic_fail", "chrono",
)
ALL_QUERY_ORDER = BASE_QUERY_ORDER + ASSIGNMENT_QUERY_ORDER
# Back-compat alias used by --all and help text.
QUERY_ORDER = BASE_QUERY_ORDER
K_SESSION_ID = "s8_K_resumed_v2"
RUN_PROGRESS_PATH = SESSIONS_ROOT.parent / "run_queries_progress.json"

_active_progress: "RunProgress | None" = None


@dataclass
class RunProgress:
    """Batch run checkpoint: which queries to run and where we stopped."""

    keys: list[str]
    next_index: int = 0
    sessions: dict[str, str] = field(default_factory=dict)
    completed_keys: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "RunProgress":
        data = json.loads(text)
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


def _load_progress() -> RunProgress | None:
    if not RUN_PROGRESS_PATH.exists():
        return None
    return RunProgress.from_json(RUN_PROGRESS_PATH.read_text())


def _save_progress(progress: RunProgress) -> None:
    RUN_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUN_PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(progress.to_json())
    tmp.replace(RUN_PROGRESS_PATH)


def _clear_progress() -> None:
    if RUN_PROGRESS_PATH.exists():
        RUN_PROGRESS_PATH.unlink()


def _session_id_for(key: str) -> str:
    if key == "k":
        return K_SESSION_ID
    return f"s8_{key}"


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


def _sigint_handler(signum: int, frame: object) -> None:
    if _active_progress is not None:
        _save_progress(_active_progress)
        print(
            "\n[run_queries] interrupted; progress saved. "
            "Resume with: uv run python run_queries.py --resume-run"
        )
    raise SystemExit(130)


def _clear_sessions() -> int:
    if not SESSIONS_ROOT.exists():
        return 0
    removed = 0
    for path in SESSIONS_ROOT.iterdir():
        if path.is_dir() and path.name != ".gitkeep":
            shutil.rmtree(path)
            removed += 1
    return removed


def _clear_memory() -> None:
    import memory as memory_svc

    memory_svc.clear()


def clear_state(*, memory: bool = False, progress: bool = True) -> None:
    n = _clear_sessions()
    print(f"[run_queries] removed {n} session(s) under {SESSIONS_ROOT}")
    if progress:
        _clear_progress()
        print("[run_queries] cleared run_queries_progress.json")
    if memory:
        _clear_memory()
        print("[run_queries] cleared memory.json and vector index")


def _preview_query(text: str) -> str:
    preview = text.replace("\n", " ")
    if len(preview) > 72:
        preview = preview[:69] + "..."
    return preview


def list_queries() -> None:
    print("Session 8 base queries (queries.md):\n")
    for key in BASE_QUERY_ORDER:
        title, text = QUERIES[key]
        print(f"  {key:12s}  {title}")
        print(f"              {_preview_query(text)}\n")
    print("Assignment extension queries:\n")
    for key in ASSIGNMENT_QUERY_ORDER:
        title, text = QUERIES[key]
        print(f"  {key:12s}  {title}")
        print(f"              {_preview_query(text)}\n")
    print("Run everything:  uv run python run_queries.py --all-queries")
    print("Run base five:   uv run python run_queries.py --all")
    print("Query K demo:    uv run python run_query_k.py  # auto SIGKILL + --resume")


async def _run_one(
    key: str,
    *,
    session_id: str | None = None,
    resume: bool = False,
    show_graph: bool = False,
) -> str:
    if key not in QUERIES:
        raise SystemExit(
            f"unknown query key: {key!r} "
            f"(choose from: {', '.join(ALL_QUERY_ORDER)})"
        )
    sid = session_id or _session_id_for(key)
    if not resume and _query_needs_resume(sid):
        resume = True
        print(f"[run_queries] resuming incomplete session {sid}")
    elif not resume and _query_is_complete(sid):
        print(f"[run_queries] session {sid} already complete; skipping fresh run")
        return ""

    title, query = QUERIES[key]
    print(f"\n[run_queries] starting {title} ({key})")
    print(f"[run_queries] session_id={sid} resume={resume}")
    t0 = time.perf_counter()
    answer = await Executor().run(
        query, session_id=sid, resume=resume, show_graph=show_graph,
    )
    elapsed = time.perf_counter() - t0
    print(f"[run_queries] finished {key} in {elapsed:.1f}s")
    return answer


def _load_graph(session_id: str) -> nx.DiGraph | None:
    store = SessionStore(session_id)
    try:
        return store.read_graph()
    except Exception:
        return None


async def _run_keys(
    keys: list[str],
    *,
    show_graph: bool = False,
    progress: RunProgress | None = None,
) -> None:
    global _active_progress
    track = progress is not None or len(keys) > 1
    if track and progress is None:
        progress = RunProgress(keys=keys)
    _active_progress = progress

    start = progress.next_index if progress else 0
    total = len(keys)
    try:
        for i in range(start, total):
            key = keys[i]
            sid = _session_id_for(key)
            if progress:
                progress.sessions[key] = sid

            if (
                progress
                and key in progress.completed_keys
                and _query_is_complete(sid)
            ):
                print(f"\n[run_queries] skipping {key} ({i + 1}/{total}): already complete")
                progress.next_index = i + 1
                _save_progress(progress)
                continue

            print(f"\n{'#' * 78}")
            print(f"[run_queries] query {i + 1}/{total}: {key}")
            print(f"{'#' * 78}")

            try:
                await _run_one(key, session_id=sid, show_graph=show_graph)
            except Exception:
                if progress:
                    progress.next_index = i
                    _save_progress(progress)
                raise

            if progress:
                if key not in progress.completed_keys:
                    progress.completed_keys.append(key)
                progress.next_index = i + 1
                _save_progress(progress)
    finally:
        _active_progress = None
        if progress and progress.next_index >= total:
            _clear_progress()
            print("[run_queries] batch complete; progress file cleared")


def main(argv: list[str] | None = None) -> int:
    cli = sys.argv[1:] if argv is None else argv

    parser = argparse.ArgumentParser(
        description="Run Session 8 validation queries from queries.md.",
    )
    parser.add_argument(
        "queries",
        nargs="*",
        metavar="QUERY",
        help=f"One or more of: {', '.join(ALL_QUERY_ORDER)}",
    )
    parser.add_argument("-q", "--query", action="append", dest="queries_opt",
                        help="Query key (repeatable)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run base five only: hello, a, i, j, k",
    )
    parser.add_argument(
        "--all-queries",
        action="store_true",
        help="Run base five plus assignment queries (companies, currency, "
             "critic_pass, critic_fail, chrono)",
    )
    parser.add_argument("--list", action="store_true", help="Print query catalogue and exit")
    parser.add_argument("--clear", action="store_true",
                        help="Delete all session dirs under state/sessions/")
    parser.add_argument("--memory", action="store_true",
                        help="With --clear, also wipe memory.json and the vector index")
    parser.add_argument(
        "--show-graph",
        action="store_true",
        help="Print PLAN / EXPANDED / EXECUTED Mermaid graphs",
    )
    parser.add_argument(
        "--resume-run",
        action="store_true",
        help="Continue a multi-query batch saved in state/run_queries_progress.json",
    )
    args = parser.parse_args(cli or None)

    signal.signal(signal.SIGINT, _sigint_handler)

    if args.list:
        list_queries()
        return 0

    if args.clear:
        clear_state(memory=args.memory)
        if (
            not args.queries
            and not args.queries_opt
            and not args.all
            and not args.all_queries
        ):
            return 0

    progress: RunProgress | None = None
    if args.resume_run:
        progress = _load_progress()
        if progress is None:
            raise SystemExit(
                f"no saved batch progress at {RUN_PROGRESS_PATH}; "
                "run a multi-query batch first"
            )
        keys = list(progress.keys)
        print(
            f"[run_queries] resuming batch at query {progress.next_index + 1}/"
            f"{len(keys)} ({progress.keys[progress.next_index] if progress.next_index < len(keys) else 'done'})"
        )
    else:
        keys = []
        if args.all_queries:
            keys = list(ALL_QUERY_ORDER)
        elif args.all:
            keys = list(BASE_QUERY_ORDER)
        for source in (args.queries, args.queries_opt or []):
            for raw in source:
                for part in raw.split(","):
                    part = part.strip().lower()
                    if part:
                        keys.append(part)

    if not keys:
        parser.print_help()
        print("\nExamples:")
        print("  uv run python run_queries.py hello")
        print("  uv run python run_queries.py --all")
        print("  uv run python run_queries.py --all-queries --show-graph")
        print("  uv run python run_queries.py -q a -q j")
        print("  uv run python run_queries.py companies currency")
        print("  uv run python run_queries.py critic_pass critic_fail")
        print("  uv run python run_queries.py --clear --memory")
        print("  uv run python run_query_k.py                 # SIGKILL demo")
        print("  uv run python run_queries.py chrono --show-graph")
        print("  uv run python run_queries.py --all-queries   # saves progress")
        print("  uv run python run_queries.py --resume-run    # after interrupt")
        return 1

    if not args.resume_run:
        # De-dupe while preserving order.
        seen: set[str] = set()
        ordered: list[str] = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
        keys = ordered

    unknown = [k for k in keys if k not in QUERIES]
    if unknown:
        raise SystemExit(f"unknown query key(s): {', '.join(unknown)}")

    if progress is None and len(keys) > 1:
        progress = RunProgress(keys=keys)

    asyncio.run(_run_keys(
        keys,
        show_graph=args.show_graph,
        progress=progress if len(keys) > 1 else None,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
