# S8 Shared Code — DAG Agent (Session 8)

A DAG-based multi-agent orchestrator for EAGV3 Session 8. The Planner decomposes queries into a growing NetworkX graph; the Executor runs skills in parallel where the graph allows; a Critic gates flagged producers; and graph state persists to disk so runs can survive interruption and resume.

Skills are defined as YAML entries plus prompt files — no Python class per skill. Adding a new capability is usually an `agent_config.yaml` edit and a new file under `code/prompts/`.

## Architecture

```
User query
    │
    ▼
 Planner ──► Researcher / Retriever / Distiller / Coder / …
    │              (parallel fan-out where the plan allows)
    ▼
  Critic (on skills marked critic: true)
    │
    ▼
 Formatter ──► final answer
```

- **Orchestrator** (`code/flow.py`) — grows and executes the graph
- **Gateway V8** (`gateway/`) — LLM routing, embeddings, batch chat on port **8108**
- **MCP server** (`code/mcp_server.py`) — web search, fetch, sandbox files, memory index
- **Persistence** (`code/persistence.py`) — session graphs and per-node traces under `code/state/sessions/`

The gateway auto-starts when you run a query. You can also start it manually (see below).

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — package manager used throughout
- **Ollama** (recommended) — for local embeddings (`nomic-embed-text`)

  ```bash
  ollama pull nomic-embed-text
  ```

- **At least one LLM API key** — Gemini, Groq, Cerebras, NVIDIA NIM, OpenRouter, or GitHub Models (see `.env.example`)
- **Optional:** `TAVILY_API_KEY` for higher-quality web search (DuckDuckGo is the free fallback)

## Quick start

### 1. Clone and configure secrets

```bash
cd S8SharedCode
cp .env.example .env
# Edit .env — add the API keys you have. You do not need every provider.
```

The gateway reads `.env` from the repo root (`S8SharedCode/.env`) or from `gateway/.env`. **Never commit `.env`** — it is listed in `.gitignore`.

### 2. Install dependencies

```bash
# Agent + MCP tools
cd code
uv sync

# LLM gateway (separate venv)
cd ../gateway
uv sync
```

### 3. Run a sanity check

From `code/`:

```bash
uv run python run_queries.py hello
```

Expected: a two-node DAG (Planner → Formatter) answering in a few seconds.

### 4. Run the full validation suite

```bash
# Base queries from queries.md: hello, a, i, j, k
uv run python run_queries.py --all

# Base + assignment queries (parallel fan-out, critic, coder, chronologer)
uv run python run_queries.py --all-queries

# List every query key
uv run python run_queries.py --list
```

Use `--show-graph` to print Mermaid diagrams (PLAN / EXPANDED / EXECUTED). Use `--clear` to wipe session traces before a fresh run.

## Query reference

| Key | What it exercises |
|-----|-------------------|
| `hello` | Minimum DAG — Planner → Formatter |
| `a` | Wikipedia fetch + Distiller + Critic pass |
| `i` | Parallel fan-out (three Researchers) |
| `j` | Graceful failure — unanswerable file path |
| `k` | Resumable execution after SIGKILL |
| `companies` | Assignment: parallel founding-year research |
| `currency` | Assignment: fan-out + Coder + SandboxExecutor |
| `critic_pass` | Assignment: Distiller auto-critic passes |
| `critic_fail` | Assignment: Critic fail → Planner recovery |
| `chrono` | Assignment: Chronologer skill |

### Query K — SIGKILL + resume demo

Query K is run separately to demonstrate crash recovery:

```bash
# Phase 1: runs until 2/3 Researchers finish, then SIGKILL
uv run python run_query_k.py

# Phase 2: resume from persisted graph
uv run python run_query_k.py --resume
```

State is written to `code/state/sessions/s8_K_resumed_v2/`.

### Batch runs with checkpointing

```bash
uv run python run_queries.py --all-queries --clear   # start fresh
# Ctrl+C mid-run saves progress
uv run python run_queries.py --resume-run             # continue where you left off
```

## Running the gateway manually

The agent calls `ensure_gateway()` before LLM work, but you can start V8 yourself for the dashboard:

```bash
cd gateway
./run.sh
# or: uv run python main.py
```

- Dashboard: http://localhost:8108
- Health check: `curl -s http://localhost:8108/v1/routers`

Port defaults to **8108** (`GATEWAY_V8_PORT` in `.env`).

## Tests

From `code/`:

```bash
uv run pytest tests/ -q
```

Unit tests cover recovery classification, graph visualization, and flow helpers. Tests marked `network` or `embed` need a live gateway and/or internet.

## Project layout

```
S8SharedCode/
├── .env.example          # template — copy to .env
├── README.md
├── Assignment.md         # assignment brief
├── queries.md            # canonical query descriptions
├── code/
│   ├── flow.py           # Executor + graph growth
│   ├── agent_config.yaml # skill catalogue
│   ├── prompts/          # one markdown prompt per skill
│   ├── run_queries.py    # batch query runner
│   ├── run_query_k.py    # SIGKILL/resume demo
│   ├── mcp_server.py     # tool server (stdio)
│   ├── state/            # runtime (gitignored except .gitkeep)
│   └── tests/
└── gateway/
    ├── main.py           # FastAPI gateway V8
    ├── client.py         # Python SDK
    └── run.sh
```

## Skills

Configured in `code/agent_config.yaml`:

| Skill | Role |
|-------|------|
| `planner` | Decomposes queries; synthesises recovery subgraphs on failure |
| `researcher` | Multi-step web research (`web_search`, `fetch_url`) |
| `retriever` | Memory + FAISS search (`search_knowledge`) |
| `distiller` | Structured extraction (auto-Critic on outgoing edges) |
| `summariser` | Condenses long content |
| `chronologer` | Orders dated events into a timeline |
| `critic` | Pass/fail verdict on upstream output |
| `coder` | Emits Python for numeric computation |
| `sandbox_executor` | Runs Coder output in a sandbox |
| `formatter` | Terminal node — produces `final_answer` |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Gateway V8 failed to start` | Run `cd gateway && uv sync && ./run.sh` and check `.env` keys |
| Embedding / memory errors | Ensure Ollama is running with `nomic-embed-text`, or set `GEMINI_API_KEY` for fallback |
| Web search returns little | Add `TAVILY_API_KEY`, or rely on DuckDuckGo (no key) |
| Stale session state | `uv run python run_queries.py --clear` from `code/` |
| Port 8108 in use | Set `GATEWAY_V8_PORT` in `.env` or stop the other process |

## Assignment deliverables

See `Assignment.md` for the full brief. This repo implements:

1. Five base queries (hello, A, I, J, K)
2. Parallel fan-out (`companies`, `i`)
3. Critic pass and fail with recovery (`critic_pass`, `critic_fail`)
4. Coder + SandboxExecutor (`currency`)
5. New Chronologer skill (`chrono`)

Session traces and final answers are written under `code/state/sessions/<session_id>/` after each run.
