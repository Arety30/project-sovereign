# Project Sovereign

Project Sovereign is a fully offline agentic system built by Syed Abrar Ali for small local language models in the 1B to 3B parameter range. Small models are cheap to run on a laptop but weak at long tasks; Sovereign compensates with cognitive scaffolding: it asks the model to plan a checklist first, runs a ReAct-style loop (thought, one tool action, observation), forces a separate self-reflection pass after every tool call, and tolerates the malformed output small models produce. A FastAPI backend drives the loop, exposes tools for the filesystem, the terminal and a Chromium browser (Playwright), persists every step to SQLite, offers a local vector memory, and streams progress over WebSockets to a single-page dashboard. Everything talks to a local Ollama server; no cloud API is involved.

## Where it is used

Sovereign is Abrar's workbench for testing how far a 1.5B or 3B model can be pushed on real tasks (edit files, run shell commands, browse and scrape a page) on his own Ubuntu laptop. It runs as a local web app on port 8000 and is operated from the browser dashboard. `blueprint.md` is the original 20-phase design document and `progress.md` the phase-by-phase build log.

## How it works

### Run loop (`backend/main.py` -> `backend/core/agent.py`)

1. `POST /run` with `{task, session_id, model}` starts `run_agent_loop` as an asyncio background task (only one agent may run at a time).
2. `create_agent()` (`agent_factory.py`) builds an `LLMClient` and an `AgentCore`, registering ten tools (see table).
3. `initialize_task()` sends `PLANNING_PROMPT` and parses the reply into a JSON array checklist (falls back to a single step on failure), then seeds the history with `SYSTEM_PROMPT` and the checklist.
4. Up to 25 `step()` iterations:
   - call the model with the full history (temperature 0.15);
   - `parse_action()` extracts `<thought>...</thought>` and `<action name="tool">{json}</action>`; if the tags are missing it tries raw JSON with `action`/`args`, and if the text contains "complete" or "done" it treats it as `task_complete`; anything else becomes `no_action_parsed` and the model is told the expected format;
   - run the tool (sync or coroutine) and capture the observation or the traceback;
   - send `REFLECTION_PROMPT` (last thought, action, args, observation) and append observation + reflection to history as a user turn;
   - save the step and session to SQLite and broadcast `{"type":"step", ...}` over `/ws`.
5. States: `idle, planning, executing, reflecting, paused, completed, failed`. `/pause` and `/stop` flip the state from outside.

### LLM client (`backend/core/llm.py`)

- Talks to Ollama `/api/chat` (non-streaming, `num_predict` 2048).
- Checks `/api/tags` before each call; if the requested model is not pulled it walks a fallback list (`qwen2.5-coder:1.5b`, `deepseek-r1:1.5b`, `granite3.2-vision:2b`, `llama3.2:1b`, `gemma3:1b`) and finally any available model. On a request error it retries the fallbacks.
- `truncate_context()` keeps the system message plus the newest messages that fit in 16,000 characters.
- `parse_json_from_response()` tries raw JSON, a ```json block, any ``` block, then the outermost `{...}`.
- `get_embedding()` uses `nomic-embed-text:latest` via `/api/embeddings`, falling back to `/api/embed`.

### Tools

| Tool | Module | Behaviour |
|------|--------|-----------|
| `list_directory`, `read_file`, `write_file` | `tools/system.py` | Plain filesystem helpers; `read_file` returns a numbered line range (default 1-150) |
| `run_terminal_command` | `tools/system.py` | `create_subprocess_shell` with a blocklist of destructive substrings (`rm -rf /`, `dd if=`, `mkfs`, `shutdown`, fork bomb, ...) |
| `browser_navigate`, `browser_click`, `browser_type`, `browser_scroll`, `browser_take_screenshot`, `browser_get_html` | `tools/browser.py` | One shared Playwright Chromium page (uses a system Chromium/Chrome binary if found, 1280x720, 20 s nav timeout). Every action saves `frontend/live_view.png`. `browser_get_html` strips `script/style/noscript/svg/iframe/head`, keeps only `id class href placeholder name value type` attributes and truncates to 8,000 chars |
| `task_complete` | handled in `agent.py` | Ends the run with the model's summary |

### Memory (`backend/core/memory.py`)

SQLite file `backend/sovereign.db` with three tables: `sessions` (task, checklist, current step, state), `steps` (thought/action/args/observation/reflection per step) and `vector_memory` (text + JSON-encoded embedding). `search()` embeds the query and ranks all stored vectors by cosine similarity computed in numpy; `GET /search_memory?query=...&limit=3` exposes it. Documents are added with `LocalMemory.add_document()` (no HTTP endpoint for that yet).

### API and dashboard

| Endpoint | Purpose |
|----------|---------|
| `GET /` | Serves `frontend/index.html` |
| `GET /status` | Ollama reachability, pulled models, whether an agent is running, agent status |
| `POST /run`, `POST /pause`, `POST /stop` | Control the single agent |
| `GET /screenshot` | Latest browser frame (1x1 transparent PNG if none) |
| `GET /search_memory` | Semantic search over `vector_memory` |
| `WS /ws` | Pushes `status`, `state_change`, `step`, `error` events |

The dashboard (vanilla HTML/CSS/JS) has a task box, a model selector, Launch/Abort buttons, a live checklist, a chat-style thought stream, an "Automation Stream" tab that reloads `/screenshot`, a "Semantic Memory" tab for `/search_memory`, and a console log. It polls `/status` every 10 s and reconnects the WebSocket on drop.

## Setup

Requirements: Linux, Python 3.10+, a running Ollama server on `localhost:11434`, Chromium or Google Chrome (or Playwright's bundled browser).

```bash
git clone <repo-url> ~/sovereign_agent
cd ~/sovereign_agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # fastapi uvicorn websockets playwright requests httpx beautifulsoup4 pydantic numpy
playwright install chromium               # skip if a system Chromium is installed
ollama pull qwen2.5-coder:1.5b
ollama pull nomic-embed-text
```

Configuration is in code, not environment variables. Before running from a different location update the absolute paths that currently point at `~/sovereign_agent`: the dashboard and screenshot paths in `backend/main.py`, `SCREENSHOT_DIR` in `backend/tools/browser.py`, `db_path` in `backend/core/memory.py` and `cwd` in `backend/tools/system.py`. The default model and the fallback list are set in `backend/main.py` and `backend/core/llm.py`.

## Usage

```bash
./run.sh                 # activates .venv, checks Ollama, starts uvicorn on 0.0.0.0:8000
# then open http://localhost:8000
```

Direct API examples:

```bash
curl -X POST localhost:8000/run -H 'Content-Type: application/json' \
  -d '{"task": "List the files in the project and write their names to names.txt", "model": "qwen2.5-coder:1.5b"}'
curl localhost:8000/status
curl 'localhost:8000/search_memory?query=fastapi%20routes&limit=3'
curl -X POST localhost:8000/stop
```

Tests (both need a live Ollama; the browser test fetches a public example page):

```bash
python -m unittest tests.test_core
python -m unittest tests.test_browser
```

## Project structure

```
sovereign_agent/
├── run.sh                      # one-command launcher
├── requirements.txt
├── blueprint.md                # architecture and 20-phase plan
├── progress.md                 # build log
├── backend/
│   ├── main.py                 # FastAPI app, WebSocket manager, run loop
│   ├── core/
│   │   ├── agent.py            # AgentState, AgentCore (plan / step / parse_action)
│   │   ├── agent_factory.py    # builds an agent with all tools
│   │   ├── llm.py              # Ollama client, fallbacks, JSON recovery, embeddings
│   │   ├── memory.py           # SQLite sessions/steps + numpy cosine vector search
│   │   └── prompts.py          # SYSTEM / PLANNING / REFLECTION prompts
│   └── tools/
│       ├── system.py           # filesystem + guarded shell
│       └── browser.py          # Playwright Chromium tools
├── frontend/
│   ├── index.html              # dashboard (single file)
│   └── live_view.png           # latest screenshot (generated)
└── tests/
    ├── test_core.py
    └── test_browser.py
```

## Limitations and notes

- One global agent and one browser page per process; sessions are identified by name but not isolated.
- No authentication and `allow_origins=["*"]`; intended for localhost use only.
- `run_terminal_command` runs with `shell=True`; the substring blocklist stops obvious disasters, not everything.
- The system prompt advertises nine tools; `browser_scroll` and `browser_get_html` are registered but not listed in the prompt, so the model rarely discovers them.
- Context trimming is character-based (16,000 chars), not token-based.
- Absolute paths are hard-coded to `~/sovereign_agent` (see Setup).
- The dashboard loads two Google Fonts from the network; everything else works offline.
