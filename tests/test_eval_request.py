import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from a2a.types import Message, Part, Role, TextPart, DataPart

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from agent import Agent, EvalConfig, TaskRunData  # noqa: E402


class FakeUpdater:
    def __init__(self):
        self.rejections: list = []
        self.status_updates: list = []
        self.artifacts: list = []

    async def reject(self, message):
        self.rejections.append(message)

    async def update_status(self, state, message):
        self.status_updates.append((state, message))

    async def add_artifact(self, parts, name):
        self.artifacts.append({"name": name, "parts": parts})


def _make_message(payload: dict) -> Message:
    return Message(
        kind="message",
        role=Role.user,
        parts=[Part(TextPart(text=json.dumps(payload)))],
        message_id="test-message",
        context_id="test-context",
    )


def test_eval_config_defaults():
    config = EvalConfig.model_validate({})
    assert config.domain == "mock"
    assert config.num_tasks == 1
    assert config.seed == 0
    assert config.timeout_seconds == 300
    assert config.max_steps == 50
    assert config.retries == 2


def test_eval_config_invalid_domain():
    with pytest.raises(Exception):
        EvalConfig.model_validate({"domain": "unknown"})


@pytest.mark.asyncio
async def test_eval_request_artifact_schema(monkeypatch):
    agent = Agent()
    updater = FakeUpdater()

    monkeypatch.setattr(
        "agent.get_tasks",
        lambda task_set_name, task_split_name, task_ids=None: [SimpleNamespace(id="task-1")],
    )

    async def fake_run_single_task(**_kwargs):
        return TaskRunData(
            reward=1.0,
            duration_sec=0.1,
            turns=3,
            tool_calls=1,
            termination_reason=None,
            tool_error=False,
        )

    monkeypatch.setattr(agent, "_run_single_task", fake_run_single_task)

    request_payload = {
        "participants": {"agent": "http://localhost:9019"},
        "config": {"domain": "mock", "num_tasks": 1},
    }

    await agent.run(_make_message(request_payload), updater)

    assert not updater.rejections
    assert updater.artifacts, "Expected artifact output from evaluation"

    data_parts = []
    for artifact in updater.artifacts:
        for part in artifact["parts"]:
            if isinstance(part.root, DataPart):
                data_parts.append(part.root.data)

    assert data_parts, "Expected DataPart artifact payload"
    result = data_parts[0]

    assert "summary" in result
    assert "config" in result
    assert "tasks" in result
    assert "system" in result
    assert "pass_rate" in result
    assert "time_used" in result
    assert "task_rewards" in result

    assert result["config"]["domain"] == "mock"
    assert result["tasks"][0]["task_id"] == "task-1"
    assert result["tasks"][0]["failure_reason"] is None
