# Green Agent Phase 1 — Additional Verification / Documentation Report

作業ディレクトリ:
`/Users/sabia_macmini/Workspace/Personal/02_Learning/Courses/AgenticAI_MOOC/02_agentx-agentbeats/09_development/green-agent/dev01`

目的:
Phase 1 提出品質（第三者再現性・落ちにくさ・透明性）を上げるために、既存の E2E 動作を維持したまま追加した検証/追記内容（計4点）をまとめる。

---

## 0. 対象リポジトリ/コンポーネント

- `tau2-green-agent/`（Green Agent本体）
- `compose.yaml`（Green + Purple のローカルE2E）
- `GREEN_AGENT_VERIFICATION_REPORT.md`（検証レポート）

---

## 1. 追加検証(1): Docker run（単体コンテナ）での起動・疎通

### 1.1 背景 / 目的

- Docker Compose だけでなく、**Green Agent コンテナ単体**でも起動できることを示すことで、審査環境や第三者環境での「落ちにくさ」を上げる。
- 具体的には、ホストから `agent-card` 取得（疎通）できることを確認する。

### 1.2 変更/追記したファイル

- `GREEN_AGENT_VERIFICATION_REPORT.md`
  - 単体コンテナ検証の記録を追記（例: `3.7`, `6.2` 等）

### 1.3 再現コマンド（例）

リポジトリルートで実行:

```bash
# build
docker build -t tau2-green-agent:local -f tau2-green-agent/Dockerfile tau2-green-agent

# run (data mount + API key)
docker run --rm -p 9009:9009 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e TAU2_DATA_DIR=/data \
  -v $(pwd)/tau2-bench/data:/data:ro \
  tau2-green-agent:local --host 0.0.0.0 --port 9009

# host check
curl -s http://localhost:9009/.well-known/agent-card.json
```

### 1.4 期待結果

- `GET /.well-known/agent-card.json` が `200` 相当で返り、JSONが取得できる。

### 1.5 追加検証: docker run（単体Green） + ホスト起動Purple（host.docker.internal）でE2E完走

背景:

- Composeでは `participants.agent=http://purple:9019`（サービスDNS）でE2Eが成立している
- `docker run` 単体ではPurpleが別プロセスになるため、Greenコンテナからホストへ到達する必要がある
- Green→Purple送信は `A2ACardResolver` 経由で **Purpleのagent-cardの `url` を参照**するため、Purpleの `--card-url` はコンテナから到達可能なURLにする必要がある

前提:

- macOS Docker Desktop で `host.docker.internal` が利用できること
- リポジトリルートに `.env` があり `OPENAI_API_KEY` を注入できること

再現コマンド（実測で成功）:

```bash
# 1) Purple (host)
cd agentbeats-tutorial
set -a; source ../.env; set +a
uv run python scenarios/tau2/tau2_agent.py \
  --host 0.0.0.0 --port 9019 \
  --card-url http://host.docker.internal:9019/

# 2) Green (docker)
cd ..
docker build -t tau2-green-agent:local -f tau2-green-agent/Dockerfile tau2-green-agent

docker run -d --rm --name tau2-green-dockerhost \
  --env-file .env \
  -p 9009:9009 \
  -e TAU2_DATA_DIR=/data \
  -v "$(pwd)/tau2-bench/data:/data:ro" \
  tau2-green-agent:local \
  --host 0.0.0.0 --port 9009 --card-url http://localhost:9009/

# 3) host reachability (from container)
docker exec tau2-green-dockerhost \
  curl -s -o /dev/null -w "%{http_code}\n" \
  http://host.docker.internal:9019/.well-known/agent-card.json

# 4) E2E request (host -> green)
cd agentbeats-tutorial
set -a; source ../.env; set +a
uv run python - <<'PY'
import asyncio, json, sys
sys.path.insert(0, 'src')
from agentbeats.client import send_message

async def main():
    request = {
        "participants": {"agent": "http://host.docker.internal:9019"},
        "config": {
            "domain": "mock",
            "num_tasks": 1,
            "seed": 0,
            "timeout_seconds": 300,
            "max_steps": 50,
            "retries": 2,
        },
    }
    result = await send_message(message=json.dumps(request), base_url="http://localhost:9009", streaming=False)
    print("status:", result.get("status", "completed"))
    print(result.get("response", "")[:1000])

asyncio.run(main())
PY

# 5) cleanup
docker stop tau2-green-dockerhost
```

観測結果（抜粋）:

- `status: completed`
- artifact（DataPart）に以下のキーが含まれることを確認:
  - `summary`, `config`, `tasks`, `system`
- 例:
  - `pass_rate: 100.0`
  - `tasks.len: 1`
  - `time_used: 12.4107...`

---

## 2. 追加追記(2): README Quickstart / Ports / Troubleshooting の整備

### 2.1 背景 / 目的

- 審査（Phase 1）では、第三者が最短で再現できる手順が重要。
- コマンド主体の Quickstart（5〜10行程度）により、**「起動 → 疎通 → 1回だけEvalRequest」**までをコピペで実行できる状態にする。

### 2.2 変更/追記したファイル

- `tau2-green-agent/README.md`
  - `Quickstart (Docker Compose E2E)`
  - `Expected Output`
  - `Ports`
  - `Troubleshooting`

### 2.3 再現コマンド（README抜粋）

リポジトリルート（`compose.yaml` がある場所）で実行:

```bash
echo 'OPENAI_API_KEY=your-key' > .env
docker compose up --build -d
curl -s http://localhost:9009/.well-known/agent-card.json
curl -s http://localhost:9009/ -H 'Content-Type: application/json' -d '{"participants":{"agent":"http://purple:9019"},"config":{"domain":"mock","num_tasks":1}}'
docker compose down
```

ポイント:

- `jq` 依存を Quickstart から外し、第三者が追加ツール無しで実行しやすい形にした。
- Compose 実行時は `participants.agent` を `http://purple:9019`（サービス名）にすることで、ネットワーク到達性の齟齬を避ける。

---

## 3. 追加追記(3): 依存固定（Dependency pinning / Reproducibility）の明文化

### 3.1 背景 / 目的

- Phase 1 では「第三者が同じ依存で動かせる」ことを説明できる必要がある。
- `tau2-green-agent` は `tau2-bench`（Python package: `tau2`）に依存しているため、どこで固定されるかを README に明記する。

### 3.2 棚卸し結果（import と依存宣言の整合）

- `tau2-green-agent/src/*.py` で `tau2.*` を import しており、`pyproject.toml` にて `tau2` を git + commit pin で宣言済み。
- `agentbeats` パッケージの import は `tau2-green-agent` 内には存在せず、依存としての追加は不要。

### 3.3 依存固定戦略

- **固定の唯一の基準**: `uv.lock`
  - ローカル再現: `uv sync --frozen`
  - Docker: `uv sync --frozen`（`tau2-green-agent/Dockerfile`）
- 追加の強固定（git pin）:
  - `tau2` は `sierra-research/tau2-bench` を **commit pin** で指定
  - `tau2-green-agent/pyproject.toml` の pin:
    - `337326e62d8e0ca74c353b004a9c5d748e0ba914`
  - 同じ pin は `tau2-green-agent/uv.lock` にも反映

### 3.4 変更/追記したファイル

- `tau2-green-agent/README.md`
  - `Dependency pinning / Reproducibility` セクションを追加
  - `uv sync --frozen` 推奨に統一

---

## 4. 追加検証(4): 入力バリデーションの異常系テスト（invalid request/config）

### 4.1 背景 / 目的

- 審査では「落ちない」ことと同じくらい「明確なエラーで返る」ことが重要。
- 異常入力で例外を握りつぶしたり、スタックトレースを返したりせず、短いメッセージで拒否できることをテストで保証する。

### 4.2 追加したテストケース（2件）

- invalid domain:
  - `config.domain = "not_a_domain"`
  - 期待: `Invalid config` / `Unsupported domain` を含むエラー応答

- empty participants:
  - `participants = {}`
  - 期待: `Missing roles` を含むエラー応答

### 4.3 実装上のポイント（A2A/HTTPの仕様に合わせた）

- Green Agent の `/` は A2A の **JSON-RPC** を受けるため、テストは `SendMessageRequest(method="message/send")` 形式で POST する。
- そのため、HTTP status は `200` でも JSON-RPC の `error` フィールドにエラーが入るケースがある。

### 4.4 変更/追記したファイル

- `tau2-green-agent/tests/test_agent.py`
  - JSON-RPC `message/send` で invalid request を POST し、本文にエラーメッセージが含まれることを検証
  - `Traceback` が含まれないことを検証

- `tau2-green-agent/tests/conftest.py`
  - `--agent-url` が疎通できない場合、テストセッション中だけローカルで `src/server.py` を自動起動
  - 第三者環境で `uv run pytest` が落ちにくい構成に改善

### 4.5 実行コマンド（ローカル）

```bash
cd tau2-green-agent
uv run pytest
```

確認結果:

- `8 passed`

---

## 5. 変更点一覧（主要ファイル）

- `GREEN_AGENT_VERIFICATION_REPORT.md`
  - 単体 Docker run 検証の追記（第三者再現の根拠を増強）

- `tau2-green-agent/README.md`
  - Quickstart/Ports/Troubleshooting の追記
  - Dependency pinning / Reproducibility の追記

- `tau2-green-agent/tests/conftest.py`
  - agentが未起動でもテストが走るよう自動起動（落ちにくさ）

- `tau2-green-agent/tests/test_agent.py`
  - invalid domain / participants空 の拒否テスト追加（透明性・堅牢性）

---

## 6. 結論（Phase 1 提出品質への寄与）

- **再現性**:
  - READMEの Quickstart と依存固定方針を明文化し、第三者が同じ条件で動かしやすい。

- **落ちにくさ**:
  - 単体 Docker run の検証を明示。
  - テストが agent 未起動でも自己完結し、CI/第三者実行で失敗しにくい。

- **透明性**:
  - 異常入力に対して明確なエラーで拒否する挙動をテストで保証。
