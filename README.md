# Tau2 Green Agent (AgentBeats)

Tau2 Green Agent evaluates purple agents on the tau2-bench customer service tasks and returns a structured artifact compatible with AgentBeats.

## GHCR Image

- `ghcr.io/shikibuton10x/tau2-green-agent:latest`
- `ghcr.io/shikibuton10x/tau2-green-agent:v0.1.0`

```bash
docker pull ghcr.io/shikibuton10x/tau2-green-agent:latest
```

## Quickstart (Docker Compose E2E)

From repo root (where `compose.yaml` is):

```bash
echo 'OPENAI_API_KEY=your-key' > .env
git clone --depth 1 https://github.com/sierra-research/tau2-bench.git tau2-bench
docker compose up --build -d
curl -sf http://localhost:9009/.well-known/agent-card.json > /dev/null
python3 - <<'PY' | curl -s http://localhost:9009/ -H 'Content-Type: application/json' -d @-
import json
rpc = {
  "jsonrpc": "2.0",
  "id": "1",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "role": "user",
      "messageId": "m1",
      "contextId": "c1",
      "parts": [
        {"kind": "text", "text": json.dumps({"participants": {"agent": "http://purple:9019"}, "config": {"domain": "mock", "num_tasks": 1}})}
      ],
    }
  }
}
print(json.dumps(rpc))
PY
docker compose down
```

## Expected Output

The `Result` artifact should include keys like `pass_rate`, `time_used`, `summary`, and `tasks` (`pass_rate` may be `0`; this is an infra/E2E wiring check):

```json
{
  "pass_rate": 0.0,
  "time_used": 12.34,
  "summary": {
    "pass_rate": 0.0,
    "passed": 0,
    "total": 1,
    "time_used_sec": 12.34
  },
  "tasks": [
    {
      "task_id": "mock-...",
      "passed": false,
      "reward": 0,
      "duration_sec": 12.34
    }
  ]
}
```

## Ports

- Green: `9009` (host)
- Purple: `9019` (host)

Host check:

```bash
curl -s http://localhost:9009/.well-known/agent-card.json
```

## Dependency pinning / Reproducibility

- Python: `>=3.13` (see `pyproject.toml`)
- Package manager: `uv`
- Pinning strategy:
  - This project uses `uv.lock` as the source of truth for exact versions.
  - Local installs should prefer frozen sync: `uv sync --frozen` (and `--extra test` if needed).
  - Docker builds copy `pyproject.toml` + `uv.lock` and run `uv sync --frozen` (see `Dockerfile`).
- tau2-bench dependency:
  - The `tau2` package is installed from the `sierra-research/tau2-bench` repo pinned to a specific git commit via `pyproject.toml`:
    - `337326e62d8e0ca74c353b004a9c5d748e0ba914`
  - The same pin is recorded in `uv.lock`.

If you change dependencies, regenerate the lockfile (`uv lock`) before building/testing.

## Supported Domains

- mock
- airline
- retail
- telecom

## EvalRequest Format

Send a JSON message to the agent with participants and config:

```json
{
  "participants": {"agent": "http://localhost:9019"},
  "config": {
    "domain": "mock",
    "num_tasks": 1,
    "seed": 0,
    "timeout_seconds": 300,
    "max_steps": 50,
    "retries": 2
  }
}
```

### Config Defaults and Validation

- domain: "mock"
- num_tasks: 1 (range 1..50)
- seed: 0
- timeout_seconds: 300 (per task)
- max_steps: 50
- retries: 2 (range 0..5)

Optional:
- task_ids: list of task ids
- user_llm: default "openai/gpt-4.1"
- user_llm_args: default `{ "temperature": 0.0 }`

Invalid values return an A2A rejection with a clear error message.

## Artifact Schema (DataPart)

The artifact keeps backward-compatible keys and adds structured diagnostics:

- pass_rate (float)
- time_used (float, seconds)
- task_rewards (dict task_id -> reward)
- summary: { pass_rate, passed, total, time_used_sec }
- config: { domain, num_tasks, seed, timeout_seconds, max_steps, retries }
- tasks: list of { task_id, passed, reward, duration_sec, turns, tool_calls, failure_reason, error }
- system: { green_agent_version, tau2_bench_version }

## Local Run

Prerequisites:
- `uv`
- `OPENAI_API_KEY`
- tau2-bench data directory (set `TAU2_DATA_DIR`)

```bash
# install deps
uv sync --frozen --extra test

# run the server
export TAU2_DATA_DIR="$(pwd)/tau2-bench/data"
export OPENAI_API_KEY=your-key
uv run src/server.py --host 127.0.0.1 --port 9009 --card-url http://localhost:9009/

# agent card
curl -s http://localhost:9009/.well-known/agent-card.json
```

## Local E2E (Purple + Green)

Start the purple agent (baseline from agentbeats-tutorial):

```bash
export OPENAI_API_KEY=your-key
uv run agentbeats-tutorial/scenarios/tau2/tau2_agent.py --host 127.0.0.1 --port 9019
```

Send an EvalRequest:

```bash
python3 - <<'PY' | curl -s http://localhost:9009/ -H 'Content-Type: application/json' -d @-
import json
rpc = {
  "jsonrpc": "2.0",
  "id": "1",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "role": "user",
      "messageId": "m1",
      "contextId": "c1",
      "parts": [
        {"kind": "text", "text": json.dumps({"participants": {"agent": "http://localhost:9019"}, "config": {"domain": "mock", "num_tasks": 1}})}
      ],
    }
  }
}
print(json.dumps(rpc))
PY
```

## Docker

Build and run the green agent with data mounted:

```bash
docker build -t tau2-green-agent:local -f Dockerfile .

# mount tau2-bench data and provide OPENAI_API_KEY
docker run --rm -p 9009:9009 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e TAU2_DATA_DIR=/data \
  -v $(pwd)/tau2-bench/data:/data:ro \
  tau2-green-agent:local --host 0.0.0.0 --port 9009 --card-url http://localhost:9009/
```

## Docker run Green + Host Purple (E2E)

When Green runs in Docker and Purple runs on the host, use `host.docker.internal`.
Also, Purple must advertise a container-reachable `agent-card.url` via `--card-url`.

```bash
# 1) Purple (host)
git clone --depth 1 https://github.com/RDI-Foundation/agentbeats-tutorial.git agentbeats-tutorial
cd agentbeats-tutorial
export OPENAI_API_KEY=your-key
uv run python scenarios/tau2/tau2_agent.py \
  --host 0.0.0.0 --port 9019 \
  --card-url http://host.docker.internal:9019/

# 2) Green (docker)
cd ..
git clone --depth 1 https://github.com/sierra-research/tau2-bench.git tau2-bench
docker build -t tau2-green-agent:local -f Dockerfile .
docker run -d --rm --name tau2-green-dockerhost \
  -p 9009:9009 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e TAU2_DATA_DIR=/data \
  -v "$(pwd)/tau2-bench/data:/data:ro" \
  tau2-green-agent:local \
  --host 0.0.0.0 --port 9009 --card-url http://localhost:9009/

# 3) E2E EvalRequest (host -> green)
python3 - <<'PY'
import asyncio, json
import httpx

rpc = {
  "jsonrpc": "2.0",
  "id": "1",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "role": "user",
      "messageId": "m1",
      "contextId": "c1",
      "parts": [
        {"kind": "text", "text": json.dumps({"participants": {"agent": "http://host.docker.internal:9019"}, "config": {"domain": "mock", "num_tasks": 1}})}
      ],
    }
  }
}

async def main():
  async with httpx.AsyncClient(timeout=300) as c:
    r = await c.post("http://localhost:9009/", json=rpc)
    r.raise_for_status()
    print(r.text[:2000])

asyncio.run(main())
PY

docker stop tau2-green-dockerhost
```

## Docker Compose (Local E2E)

From repo root:

```bash
export OPENAI_API_KEY=your-key
export TAU2_DATA_DIR=$(pwd)/tau2-bench/data

docker compose up --build
```

## Testing

```bash
uv sync --frozen --extra test
uv run pytest
```

## Troubleshooting

- Missing API key: set `OPENAI_API_KEY` (Docker Compose uses `.env` in repo root).
- Port conflict: stop other processes or change ports (`9009` / `9019`).
- 503 / cannot reach Purple: when running via Docker Compose, the EvalRequest must use `"participants":{"agent":"http://purple:9019"}` (service name), and the agent-card URLs must be reachable from the caller (host vs compose network).
