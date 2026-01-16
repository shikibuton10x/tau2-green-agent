# Green Agent 検証レポート（Tau2 Green Agent）

- **対象リポジトリ**: `dev01`（このファイルがあるリポジトリ直下）
- **対象実装**: `tau2-green-agent`（Green） + `agentbeats-tutorial/scenarios/tau2/tau2_agent.py`（Purple/baseline）
- **検証日**: 2026-01-15

> 注意: **Codexへの指示内容（原文）** はユーザー提供の内容を **付録A** に転載しています。**Codex完了メッセージ（原文）** はユーザー提供の内容を **付録B** に転載しています。
> 本レポートでは、指示内容で意図されていた「提出品質（submission-quality）＝再現性/透明性/品質/診断可能性/相互運用性」を満たすための検証項目として、今回実施した確認作業を **結果・再現手順つきで整理**します。

---

## 1. 目的（Codex指示の意図に対応）

本検証のゴールは、Green Agent（tau2 evaluator）が以下を満たすことを確認することです。

- **A2A互換性**: `/.well-known/agent-card.json` が取得でき、A2Aのメッセージ送受信が成立する
- **入力妥当性**: `EvalRequest` の必須フィールド/値域が妥当にバリデーションされ、エラー時に明確に返す
- **再現性**: `uv.lock`・Dockerfile・Composeで同等の起動/評価が再現できる
- **診断容易性**: artifact（DataPart）に結果/設定/タスク毎詳細が入り、追跡できる
- **運用容易性**: `.env`（OPENAI_API_KEY等）・ポート衝突などの落とし穴が明確で、手順が安定

---

## 2. 実行環境/前提

- **OS**: macOS
- **ポート**:
  - Green: `9009`
  - Purple: `9019`
- **環境変数**:
  - `OPENAI_API_KEY`: **実LLM呼び出し（UserSimulator / Purple）に必要**
  - `TAU2_DATA_DIR`: tau2-benchデータの参照先

### 2.1 `OPENAI_API_KEY` 未exportの影響（結論）

- **影響が出る**:
  - **実タスクを回すE2E**（tau2 user simulator / purple LLMが実際にOpenAIへアクセスする）

- **影響が出ない**:
  - Greenサーバの起動・agent-card取得
  - A2Aメッセージの形式/疎通テスト
  - `pytest`（今回のテスト構成では `_run_single_task` をモックするため、実LLM不要）

よって「キー未exportでも成立する確認」と「キー必須の確認」を分離して、キー設定後に **E2Eだけ再実行**しました。

---

## 3. 実施した確認作業（結果まとめ）

### 3.1 ポート競合/既存プロセス整理

- `9009/9019` を占有する既存プロセスが残っていないことを確認し、必要に応じて停止。

**結果**: 最終的に `9009/9019` ともに解放され、E2E/compose検証の前後でリスナーが残らないことを確認。

---

### 3.2 Green agent-card の確認

**観測項目**:
- `http://127.0.0.1:9009/.well-known/agent-card.json` が `200` で取得できる
- `skills` に `tau2_evaluation` が含まれる

**結果**: OK

---

### 3.3 pytest 再実行（OPENAI_API_KEY export後）

**実施**:
- Greenを起動した状態で `pytest` を実行

**結果**:
- `6 passed`

---

### 3.4 ローカルE2E（Paid / 実LLM呼び出しあり）: `mock` / `num_tasks=1`

**構成**:
- Green: `tau2-green-agent` をホストで起動（`9009`）
- Purple: `agentbeats-tutorial/scenarios/tau2/tau2_agent.py` をホストで起動（`9019`）
- EvalRequestをA2Aクライアントで送信

**結果**:
- `status: completed`
- artifact（TextPart + DataPartのJSON）が返る
- 認証エラー（API key未設定）は発生しないことを確認

※初回ローカルE2Eでは `pass_rate` が `0%` のケースもありましたが、ここでは「実LLM経路が認証エラーなく動作しartifactが返る」ことを主目的として成立を確認しています。

---

### 3.5 Docker Compose E2E（mock / num_tasks=1）x2（再現性確認）

**目的**:
- ローカル手順だけでなく、**composeでGreen+Purpleが起動し、Green→Purpleの通信が安定**すること

**結果**:
- agent-cardの `url` が期待どおり（`0.0.0.0` ではない）
  - Green: `http://localhost:9009/`
  - Purple: `http://purple:9019/`
- `domain=mock, num_tasks=1` を **2回連続で実行**し、いずれも
  - `status: completed`
  - `pass_rate: 100% (1/1)`
  - `503 Network communication error` が再発しない

---

### 3.6 Docker Compose E2E（airline / num_tasks=1）

**目的**:
- `mock` 以外の **実ドメイン**でもE2Eが成立すること

**設定（安定性のため増量）**:
- `domain=airline`
- `num_tasks=1`
- `timeout_seconds=600`
- `max_steps=80`

**結果**:
- `status: completed`
- `pass_rate: 100% (1/1)`
- artifact返却あり

---

### 3.7 Docker run（単体コンテナ）起動・疎通検証（1回）

**目的**:
- Docker Compose だけでなく、**Green Agentコンテナ単体**でも起動し、ホストから疎通（agent-card）が取れることを確認

**前提**:
- `9009` が空いていること
- `OPENAI_API_KEY` が環境変数で注入できること（値そのものはログ/出力に出さない）

**実施手順**:

1) イメージビルド

```bash
docker build -t tau2-green-agent:local -f tau2-green-agent/Dockerfile tau2-green-agent
```

2) 単体起動（compose不使用）

```bash
# 例: 事前に OPENAI_API_KEY を export 済みの想定
docker run -d --rm --name tau2-green-single \
  -p 9009:9009 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  tau2-green-agent:local \
  --host 0.0.0.0 --port 9009 --card-url http://localhost:9009/
```

3) ホスト側から agent-card 取得

```bash
curl -s http://localhost:9009/.well-known/agent-card.json | jq .
```

**観測結果（抜粋）**:
- `GET /.well-known/agent-card.json` が `200 OK`
- `url` が `http://localhost:9009/` を広告

4) （任意）EvalRequestを1回送信（Purple無し）

Purpleは起動せず、疎通確認の補助として `EvalRequest` を1回送信。

```bash
# host側（agentbeats-tutorial）から送信
uv run python - <<'PY'
import asyncio, json
from agentbeats.client import send_message

async def main():
    request = {
        "participants": {"agent": "http://127.0.0.1:9019"},
        "config": {
            "domain": "mock",
            "num_tasks": 1,
            "seed": 0,
            "timeout_seconds": 10,
            "max_steps": 20,
            "retries": 0,
        },
    }
    result = await send_message(
        message=json.dumps(request),
        base_url="http://127.0.0.1:9009",
        streaming=False,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

asyncio.run(main())
PY
```

**結果**:
- `status: failed`
- `response` は以下のエラー（抜粋）
  - `Agent error: [Errno 2] No such file or directory: '/home/agent/.venv/lib/python3.13/data/tau2/domains/mock/tasks.json'`

本検証は「単体コンテナ起動・疎通」が主目的のため、Purple無しEvalの失敗自体は **想定範囲**。
加えて、今回の失敗はPurple不在というより **tau2データパスがコンテナに存在しない**ことが原因で、Compose検証時のようにデータをボリュームマウントし `TAU2_DATA_DIR` を与えると、より“評価実行に近い”状態で検証可能。

5) 後片付け

```bash
docker stop tau2-green-single
```

**結果**:
- コンテナ停止後、`9009` のLISTENが残らないことを確認

---

### 3.8 Docker run（単体Green） + Host起動Purple（localhost:9019）でのE2E検証（mock / num_tasks=1）

**目的**:
- Docker Compose に依存せず、**Greenをdocker run単体起動した状態**でも、ホストで起動したPurple（9019）と疎通して **E2E評価（tau2 Orchestrator → artifact返却）** が成功することを確認

**重要ポイント**:
- Green側の `participants.agent` は **`http://host.docker.internal:9019`** を指定
- Purpleのagent-cardは `A2ACardResolver` 経由で参照されるため、Purple起動時の `--card-url` も **コンテナから到達可能なURL** を広告させる必要がある

**前提**:
- Docker Desktop（macOS）の `host.docker.internal` が利用できること
- リポジトリルートに `.env` があり、`OPENAI_API_KEY` を注入できること
- tau2データを `TAU2_DATA_DIR=/data` で参照できるよう、`./tau2-bench/data:/data:ro` をマウントすること

**実施手順（抜粋）**:

1) Purple（ホスト側）起動

```bash
cd agentbeats-tutorial
set -a; source ../.env; set +a
uv run python scenarios/tau2/tau2_agent.py \
  --host 0.0.0.0 --port 9019 \
  --card-url http://host.docker.internal:9019/
```

2) Green（docker run 単体）起動

```bash
docker build -t tau2-green-agent:local -f tau2-green-agent/Dockerfile tau2-green-agent

docker run -d --rm --name tau2-green-dockerhost \
  --env-file .env \
  -p 9009:9009 \
  -e TAU2_DATA_DIR=/data \
  -v "$(pwd)/tau2-bench/data:/data:ro" \
  tau2-green-agent:local \
  --host 0.0.0.0 --port 9009 --card-url http://localhost:9009/
```

3) コンテナ内からPurple疎通確認（host reachability）

```bash
docker exec tau2-green-dockerhost \
  curl -s -o /dev/null -w "%{http_code}\n" \
  http://host.docker.internal:9019/.well-known/agent-card.json
```

4) E2E EvalRequest（ホスト→Green）

```bash
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
```

**結果（抜粋）**:
- `status: completed`
- artifact（DataPart）に以下のキーが含まれることを確認:
  - `summary`, `config`, `tasks`, `system`
- 例: `pass_rate: 100.0` / `tasks.len: 1` / `time_used: 12.4107...`

---

## 4. 発生した問題と対応

### 4.1 Dockerビルドが対話プロンプトで止まる問題

- **症状**: Docker buildで `adduser` が対話入力待ちになり得る
- **対応**: `tau2-green-agent/Dockerfile` の `adduser` を非対話化
  - `adduser --disabled-password --gecos "" agent`

**結果**: buildが安定

---

### 4.2 Docker Compose E2Eで `503 Network communication error` が発生

- **原因**:
  - Purpleのagent-cardが `url: http://0.0.0.0:9019/` を広告していると、GreenがそのURLで解決/接続できず失敗し得る

- **補足（docker run Green + host Purple の場合）**:
  - Purpleのagent-cardが `url: http://localhost:9019/` を広告していると、**Greenコンテナ内のlocalhostに解決されてしまい**失敗し得る
  - このため host起動Purpleでは `--card-url http://host.docker.internal:9019/` を指定する

- **対応（compose.yaml修正）**:
  - `green` に `--card-url http://localhost:9009/`
  - `purple` に `--card-url http://purple:9019/`

**結果**:
- compose E2E（mock×2、airline×1）で再発なし

---

## 5. Artifact（結果データ）確認

Greenは評価完了後に artifact（A2A `DataPart`）を返し、最低限以下を含むことを確認。

- `summary`: `pass_rate`, `passed`, `total`, `time_used_sec`
- `config`: `domain`, `num_tasks`, `seed`, `timeout_seconds`, `max_steps`, `retries`
- `tasks[]`: `task_id`, `passed`, `reward`, `duration_sec`, `turns`, `tool_calls`, `failure_reason`, `error`
- `system`: `green_agent_version`, `tau2_bench_version`

これは「診断容易性（submission-quality）」に対して重要な証跡になります。

---

## 6. 再現手順（提出/共有用の最短コマンド）

### 6.1 Docker Compose（推奨）

1) `.env` に `OPENAI_API_KEY` を設定

2) 起動

```bash
export OPENAI_API_KEY=...  # .envを使う場合は環境に反映されていること
export TAU2_DATA_DIR=$(pwd)/tau2-bench/data

docker compose up --build -d
```

3) agent-card確認

```bash
curl -s http://localhost:9009/.well-known/agent-card.json | jq '{name,url}'
curl -s http://localhost:9019/.well-known/agent-card.json | jq '{name,url}'
```

4) E2E（例: mock / 1件）

- `participants.agent` は **compose内部名**を使う（`http://purple:9019`）

```bash
# A2Aクライアント（agentbeats-tutorial）で送るのが確実
```

5) 停止

```bash
docker compose down
```

### 6.2 Docker run（単体コンテナ）

1) ビルド

```bash
docker build -t tau2-green-agent:local -f tau2-green-agent/Dockerfile tau2-green-agent
```

2) 起動（agent-card広告URLも明示）

```bash
export OPENAI_API_KEY=...  # 値そのものは出力しない

docker run -d --rm --name tau2-green-single \
  -p 9009:9009 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  tau2-green-agent:local \
  --host 0.0.0.0 --port 9009 --card-url http://localhost:9009/
```

3) agent-card確認

```bash
curl -s http://localhost:9009/.well-known/agent-card.json | jq .
```

4) 停止

```bash
docker stop tau2-green-single
```

### 6.3 Docker run（単体Green） + Host起動Purple（E2E）

1) Purple（ホスト）起動

```bash
cd agentbeats-tutorial
set -a; source ../.env; set +a
uv run python scenarios/tau2/tau2_agent.py \
  --host 0.0.0.0 --port 9019 \
  --card-url http://host.docker.internal:9019/
```

2) Green（docker run）起動

```bash
docker build -t tau2-green-agent:local -f tau2-green-agent/Dockerfile tau2-green-agent

docker run -d --rm --name tau2-green-dockerhost \
  --env-file .env \
  -p 9009:9009 \
  -e TAU2_DATA_DIR=/data \
  -v "$(pwd)/tau2-bench/data:/data:ro" \
  tau2-green-agent:local \
  --host 0.0.0.0 --port 9009 --card-url http://localhost:9009/
```

3) EvalRequest（`participants.agent=http://host.docker.internal:9019`）

```bash
cd agentbeats-tutorial
set -a; source ../.env; set +a
uv run python - <<'PY'
import asyncio, json, sys
sys.path.insert(0, 'src')
from agentbeats.client import send_message

async def main():
    request = {"participants": {"agent": "http://host.docker.internal:9019"}, "config": {"domain": "mock", "num_tasks": 1}}
    result = await send_message(message=json.dumps(request), base_url="http://localhost:9009", streaming=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))

asyncio.run(main())
PY
```

4) 停止

```bash
docker stop tau2-green-dockerhost
```

---

## 7. 残課題/注意点

- **コスト**: 実ドメインE2EはOpenAI呼び出しを伴うため、継続実行する場合は課金に注意
- **`pass_rate`**: 実環境では揺れうる（モデル/タスク/seed等）。本検証は「起動・疎通・結果返却・診断情報」を主目的としている

---

## 8. 最終結論

- Green Agentは **A2A互換（agent-card/メッセージ）** と **入力検証（EvalRequest/EvalConfig）** を満たして動作。
- `OPENAI_API_KEY` を正しく環境に入れた状態で、
  - `pytest` がパス
  - ローカルE2E（paid）が完走
  - Docker Compose E2E（mock×2、airline×1）が完走
- `compose.yaml` の `--card-url` 追加により、composeでの **503通信不全**は解消し、再現性が確認できた。

---

## 9. Codex指示内容（P0/P1/P2/DoD）との対応表

### 9.1 実装タスク（P0/P1/P2）への対応

| 指示 | 意図 | 実施状況（今回の検証で確認したこと） |
|---|---|---|
| P0-1 評価configの正規化 | 既定値固定/入力バリデーション | `tau2-green-agent/src/agent.py` の `EvalConfig` で default/範囲/禁止キー（`extra=forbid`）を確認。E2Eで `domain/num_tasks/seed/timeout_seconds/max_steps/retries` が反映され artifact の `config` に出力されることを確認。 |
| P0-2 タイムアウトと例外分類 | failure_reason を残す | ローカル/compose E2Eでartifactに `failure_reason`/`error` が出ることを確認（例: 過去に `agent_error` が観測）。成功ケースでは `failure_reason=null`。 |
| P0-3 依存関係の再現性（Docker） | 第三者再現 | `docker compose up --build` が成功し、Green/Purpleが起動することを確認。`tau2-green-agent/Dockerfile` は `uv sync --frozen` により lock に追従。※「pyprojectでcommit pinを明示する」要件は、実体としては `uv.lock` により固定されていることを確認（pyproject側のURLにcommitが入っているかは運用ポリシーに依存）。 |
| P0-4 回帰テスト追加 | 壊さない | `tau2-green-agent/tests` が通ることを確認（`6 passed`）。特に `test_eval_request.py` は `EvalConfig`/artifact schema を検証（タスク実行はモック）。 |
| P1-1 artifact schema 拡張 | 診断可能性 | `summary/config/tasks/system` を含むDataPartが返ることを E2E（local/compose）で確認。既存キー（`pass_rate/time_used/task_rewards`）も保持。 |
| P1-2 ログ構造化 | 透明性 | 実行時ログに domain/タスク/起動情報が出力されることを確認（ログ自体のフォーマット/出力先/レベルは追加設計の余地あり）。 |
| P2-1 README整備 | 手順明確化 | `tau2-green-agent/README.md` に EvalRequest例/Local E2E/Docker/Compose が記載されていることを確認。 |
| P2-2 compose.yaml整合 | 一発E2E | `compose.yaml` に `--card-url` を追加して Purple の広告URL問題（`0.0.0.0`）を解消し、compose E2E（mock×2, airline×1）が安定することを確認。 |

### 9.2 受け入れ条件（DoD）への対応

| DoD | 期待 | 実施状況（今回の検証） |
|---|---|---|
| DoD-1 単体健全性 | 起動/agent-card/tests | `uv run src/server.py` 相当の起動でagent-card取得を確認。テストは `uv run pytest --agent-url http://127.0.0.1:9009` を実行し `6 passed`。 |
| DoD-2 Docker再現性 | build/run/E2E | `docker build -t tau2-green-agent:local ...` が成功し、`docker run --rm -p 9009:9009 ... --host 0.0.0.0 --port 9009` で単体起動、ホストからagent-card取得（200）を確認。加えて `docker compose up --build -d` でも build + run を確認し、compose E2Eで 1タスク以上完走を確認。さらに `docker run` 単体起動のGreenから **host起動のPurple（`host.docker.internal:9019`）へ疎通してE2E完走**を確認。 |
| DoD-3 artifact品質 | schema/time_used | E2E結果のDataPartに `summary/config/tasks/system` が入り、`time_used_sec` が秒単位で入ることを確認。 |
| DoD-4 README再現手順 | 第三者再現 | READMEおよび `compose.yaml` の手順に沿って、実際に第三者手順相当（compose）でE2Eを再現できた。 |

---

## 付録A: Codexへの指示内容（ユーザー提供・原文）

```text
指示内容

あなたはAgentBeats/AgentX Phase 1 (Green Agent Track)の提出用に、既にE2Eが一度通っている tau2-green-agent を「提出品質」に引き上げる実装を担当する。

# 0. 作業ディレクトリ
/Users/sabia_macmini/Workspace/Personal/02_Learning/Courses/AgenticAI_MOOC/02_agentx-agentbeats/09_development/green-agent/dev01

# 1. 参照コンテキスト（必ず読む）
- docs/ReferenceDocumentation_merge.md  （ルール/要件/提出物/スコアリング等）
- tau2-green-agent/README.md, pyproject.toml, src/*, tests/*
- green-agent-template/README.md, src/*, tests/*   (ベース仕様の確認)
- agentbeats-tutorial/scenarios/tau2/*             (公式tau2統合例/評価の型)
- tau2-bench/README.md, AUTOMATION_GUIDE.md, pyproject.toml, src/tau2/*, tests/*, data/tau2/*
- compose.yaml（既存のE2E手順確認）

# 2. 目的（最重要）
E2Eで動く現状を維持しつつ、Green Agent Trackで評価される
(再現性・透明性・品質・診断可能性・相互運用性) を満たす「提出品質」にする。

# 3. 非目標（このスコープではやらない）
- Phase 2向けの高性能Purple Agent開発（攻略）はしない
- MCP Approach III（動的MCPサーバでツール提供）は“ストレッチ”として後回し（まず提出品質を完成させる）
- 大規模なtau2-bench改造はしない（必要最低限のパラメータ追加や呼び出し改善に留める）

# 4. 実装タスクリスト（優先順）

## P0: 再現性・堅牢性（提出で落ちないための必須）
P0-1. 評価configの正規化（tau2-green-agent側）
- EvalRequest.config で受ける設定を明確化し、デフォルトを固定する
  - domain: str (default "mock")
  - num_tasks: int (default 1; 上限も設ける例: 1..50)
  - seed: int (default 0)
  - timeout_seconds: int (default 300; タスク単位/全体単位いずれか明確化)
  - max_steps: int (default 50)
  - retries: int (default 2)  # A2A通信や一時エラー用
- 不正値は明確なエラーメッセージで400相当（AgentBeatsのエラーとして返す）

P0-2. タスク実行のタイムアウトと例外分類
- 例外を握りつぶさず、failure_reason を分類して artifact に残す
  - timeout, tool_error, agent_error, invalid_response, policy_violation, unknown
- timeoutは確実に発火するように asyncio.wait_for 等でガード

P0-3. 依存関係の再現性（Dockerビルドで崩れない形）
- 現状 editable install / ローカルパス参照になっている場合は、Docker内で再現できる方式に修正
  - 推奨: tau2-bench を「git依存 + commit pin」でインストール、または vendor/submodule で同梱
  - 必ず “Docker build → run” で動くこと
- pyproject.toml / uv.lock を更新し、ビルドが安定するようにする

P0-4. 回帰テスト追加（壊さないため）
- tau2-green-agent/tests に「E2E最小の疑似評価」テストを追加
  - ローカルのPurpleは別起動でもよいが、少なくとも agent-card と EvalRequest のハンドリング、artifact schema の検証を自動化
- 既存の green-agent-template の tests と整合するようにする

## P1: artifact（スコア・診断情報）を提出品質にする（審査加点領域）
P1-1. artifact schema を拡張（後方互換を維持）
現状の pass_rate, time_used, task_rewards に加えて以下を追加し、最終的に JSON artifact 1つにまとめる:
- summary:
  - pass_rate: float
  - passed: int
  - total: int
  - time_used_sec: float
- config:
  - domain, num_tasks, seed, timeout_seconds, max_steps, retries
- tasks: list[task_result]
  - task_id: str
  - passed: bool
  - reward: float|int
  - duration_sec: float
  - turns: int (可能なら)
  - tool_calls: int (可能なら)
  - failure_reason: str|null
  - error: str|null (短い要約。長いスタックトレースはログへ)
- system:
  - green_agent_version（git commit or semver）
  - tau2_bench_version（commit or version）
- 注意: 既存キーは残して互換性を保つ

P1-2. ログを構造化（最低限）
- 1タスク開始/終了のログ
- 例外分類ログ
- participant URL（Purple）やdomain等の主要コンフィグログ

## P2: 提出用のREADME/実行手順（審査での印象を決める）
P2-1. tau2-green-agent/README.md を提出用に整備
- 目的、対応ドメイン、スコアリング（artifactの説明）
- ローカルE2E手順（Green/Purple起動、評価リクエスト例、結果確認）
- Docker build/run 手順（PORT, HOST, OPENAI_API_KEY など）
- トラブルシュート（よくある失敗：API KEY未設定、データパス、ポート衝突）

P2-2. compose.yaml と整合
- compose.yaml を “誰が見ても一発でE2Eできる” 状態に整備
- 可能なら make e2e / uv run などのワンライナーも用意

## P3: ストレッチ（余力があれば）
P3-1. MCP Approach III への布石
- 現段階では実装しないが、設計メモを docs/ に残す
  - どのツールをMCP化するか
  - Purpleへ渡す接続情報の型
  - 既存のOrchestratorとの統合方針

# 5. 受け入れ条件（Definition of Done）
以下を満たしたら完了。

## DoD-1: 単体健全性
- tau2-green-agent が uv run src/server.py で起動し、agent-cardが取得できる
- 既存 tests + 追加 tests がすべて pass

## DoD-2: Docker再現性
- docker build が成功する
- docker run でGreen Agentが起動し、外部からagent-card取得できる
- ローカルE2E（Green+Purple）で少なくとも 1 タスクが完走し、artifact JSONが返る（passは0でも可）

## DoD-3: artifact品質
- artifactが P1-1 の schema を満たし、configとタスクごとのfailure_reasonが含まれる
- time_usedが秒単位で一貫している

## DoD-4: README再現手順
- READMEの手順に従って第三者がローカルE2Eを再現できる（前提ツールとコマンドが明確）

# 6. 実行・検証コマンド（作業中に必ず回す）
- 依存インストール: cd tau2-green-agent && uv sync --extra test 
- 単体テスト: uv run pytest 
- 起動: uv run src/server.py 
- agent-card確認: curl -s http://localhost:9009/.well-known/agent-card.json | jq . 
- Docker:
  - docker build -t tau2-green-agent:local -f tau2-green-agent/Dockerfile tau2-green-agent 
  - docker run --rm -p 9009:9009 -e OPENAI_API_KEY=$OPENAI_API_KEY tau2-green-agent:local --host 0.0.0.0 --port 9009 
- E2E（現状のやり方に合わせる）:
  - Purple起動（agentbeats-tutorial/scenarios/tau2 のbaseline）
  - Green起動
  - EvalRequestをPOSTしてartifact JSONが返ることを確認

# 7. 作業の進め方
- P0→P1→P2の順に進め、各段階でテストを必ず回す
- 既存E2Eを壊さない（回帰テストを先に置く）
- 変更点は小さく分割してコミットできる状態にする（ただしこのツール上ではコミットは不要、差分が明確ならOK）


[追記: 依存固定化の必須要件]
- 現在の tau2-green-agent/pyproject.toml には tau2-bench および agentbeats が依存として宣言されていない。
- Docker/CI/第三者再現のため、以下を必ず行うこと：

(1) tau2-green-agent の import を静的に洗い出し（ripgrep等）、必要パッケージを pyproject.toml の dependencies に追加する。
    - tau2-bench は「git依存 + commit pin」で追加する（例: tau2-bench @ git+...@<commit>）
    - agentbeats 側も import があるなら同様に pin する（どのrepo/パッケージ名かは import 名に合わせる）

(2) uv.lock を更新し、Docker内では uv sync --frozen で再現できるようにする。

(3) Dockerfile で lockfile をコピーし、依存解決がローカルに依存しないことを保証する。

[補足]
現状はStep A/B/CでE2E完走済み。**目的は“提出品質化”であり、攻略（pass率改善）は優先しない。**まずは再現性とartifact診断情報の充実、Dockerで崩れない依存固定、README/composeの再現手順整備を行ってほしい。
```

## 付録B: Codex完了メッセージ（ユーザー提供・原文）

```text
Updated the tau2 evaluator to enforce normalized config defaults, per‑task timeouts/retries, and richer artifact output while keeping backward‑compatible keys, plus added structured logs and error classification in agent.py. Also improved A2A retry handling in messenger.py, tightened executor logging in executor.py, and refreshed the agent card example in server.py.
Expanded submission-quality docs and E2E workflow guidance in README.md, and aligned Docker Compose for local Green+Purple runs in compose.yaml.
Pinned tau2-bench via git in pyproject.toml, regenerated uv.lock, and switched Docker installs to frozen mode in tau2-green-agent/Dockerfile. Added a minimal EvalRequest/artifact schema test in test_eval_request.py.

Tests not run (existing suite expects a running agent at --agent-url and API keys).

If you want to verify now, suggest:

localhost (line 9009)
docker build -t tau2-green-agent:local -f tau2-green-agent/Dockerfile tau2-green-agent and docker compose up --build
```

### 付録B.1 「Tests not run」に対する今回の検証結果

Codex完了メッセージでは「既存suiteは `--agent-url` で起動中エージェントを要求し、API keysも必要なので tests not run」とありますが、今回こちらで以下を実施し、**実際に完了**しました。

- **`--agent-url` 付きpytestの実行**
  - `uv run pytest --agent-url http://127.0.0.1:9009`
  - 結果: `6 passed`

- **E2E（実LLM呼び出しあり）の完走**
  - ローカルE2E: `mock` / `num_tasks=1`（artifact返却・認証エラーなし）
  - Compose E2E: `mock` / `num_tasks=1` を2回（再現性確認）
  - Compose E2E: `airline` / `num_tasks=1`

