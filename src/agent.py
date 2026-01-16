"""
Tau2 Green Agent - Evaluates purple agents on tau-bench tasks.

This agent runs tau2-bench evaluation and returns pass_rate and time_used.
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from importlib import metadata
from typing import Any, List, Optional

import nest_asyncio
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator

from a2a.server.tasks import TaskUpdater
from a2a.types import Message, TaskState, Part, TextPart, DataPart
from a2a.utils import get_message_text, new_agent_text_message

from messenger import Messenger

from tau2.agent.base import BaseAgent, ValidAgentInputMessage
from tau2.agent.llm_agent import LLMAgentState
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.data_model.simulation import TerminationReason
from tau2.environment.tool import Tool
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.registry import registry
from tau2.run import get_tasks
from tau2.user.user_simulator import UserSimulator
from tau2.evaluator.evaluator import evaluate_simulation, EvaluationType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tau2_green_agent")

# Allow nested event loops
nest_asyncio.apply()

RESPOND_ACTION_NAME = "respond"
ALLOWED_DOMAINS = {"mock", "airline", "retail", "telecom"}
MAX_NUM_TASKS = 50
MAX_RETRIES = 5


class InvalidResponseError(ValueError):
    """Raised when a purple agent returns an invalid response payload."""


class RemoteAgentError(RuntimeError):
    """Raised when a purple agent cannot be reached or returns an error status."""


class EvalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(default="mock")
    num_tasks: int = Field(default=1, ge=1, le=MAX_NUM_TASKS)
    seed: int = Field(default=0)
    timeout_seconds: int = Field(default=300, gt=0)
    max_steps: int = Field(default=50, gt=0)
    retries: int = Field(default=2, ge=0, le=MAX_RETRIES)
    task_ids: Optional[list[str]] = None
    user_llm: str = Field(default="openai/gpt-4.1")
    user_llm_args: dict[str, Any] = Field(default_factory=lambda: {"temperature": 0.0})

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        if value not in ALLOWED_DOMAINS:
            raise ValueError(
                f"Unsupported domain '{value}'. Choose from {sorted(ALLOWED_DOMAINS)}."
            )
        return value


class EvalRequest(BaseModel):
    """Request format sent by the AgentBeats platform to green agents."""
    participants: dict[str, HttpUrl]  # role -> agent URL
    config: dict[str, Any] = Field(default_factory=dict)


@dataclass
class TaskRunData:
    reward: float
    duration_sec: float
    turns: int
    tool_calls: int
    termination_reason: Optional[str]
    tool_error: bool
    eval_error: Optional[str] = None


@dataclass
class TaskResult:
    task_id: str
    passed: bool
    reward: float
    duration_sec: float
    turns: int
    tool_calls: int
    failure_reason: Optional[str]
    error: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "passed": self.passed,
            "reward": self.reward,
            "duration_sec": self.duration_sec,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "failure_reason": self.failure_reason,
            "error": self.error,
        }


def tools_to_str(tools: List[Tool]) -> str:
    """Convert tau-bench tools to JSON schema format."""
    return json.dumps([tool.openai_schema for tool in tools], indent=2)


def extract_text_from_message(message: MultiToolMessage | UserMessage | ToolMessage) -> str | None:
    """Extract text content from tau2 messages."""
    if isinstance(message, UserMessage):
        return message.content
    elif isinstance(message, MultiToolMessage):
        tool_results = []
        for tm in message.tool_messages:
            tool_results.append(f"Tool '{tm.name}' result: {tm.content}")
        return "\n".join(tool_results)
    else:
        return str(message.content) if hasattr(message, 'content') else str(message)


def _count_turns_and_tool_calls(messages: list) -> tuple[int, int, bool]:
    turns = len(messages)
    tool_calls = 0
    tool_error = False
    for msg in messages:
        if isinstance(msg, ToolMessage):
            if msg.error:
                tool_error = True
        elif isinstance(msg, (AssistantMessage, UserMessage)) and msg.tool_calls:
            tool_calls += len(msg.tool_calls)
    return turns, tool_calls, tool_error


def _extract_json_payload(response: str) -> str:
    text = response.strip()
    if "<json>" in text:
        text = text.split("<json>", 1)[1]
        if "</json>" in text:
            text = text.split("</json>", 1)[0]
        text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text


def _get_version(package: str, fallback: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return fallback


class RemoteA2AAgent(BaseAgent):
    """
    An agent that delegates to a remote purple agent via A2A protocol.

    This implements tau2's BaseAgent interface so it can be used with
    the native Orchestrator, while delegating actual decision-making
    to the remote agent being tested.
    """

    def __init__(
        self,
        tools: List[Tool],
        domain_policy: str,
        messenger: Messenger,
        agent_url: str,
        timeout_seconds: int,
        retries: int,
    ):
        self.tools = tools
        self.domain_policy = domain_policy
        self.messenger = messenger
        self.agent_url = agent_url
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self._is_first_message = True

    @property
    def agent_prompt(self) -> str:
        """Build the system prompt with policy and tools."""
        return f"""{self.domain_policy}

Here's a list of tools you can use (you can use at most one tool at a time):
{tools_to_str(self.tools)}

and

{json.dumps({
    "type": "function",
    "function": {
        "name": RESPOND_ACTION_NAME,
        "description": "Respond directly to the user with a message instead of calling a tool.",
        "parameters": {
            "properties": {
                "content": {
                    "description": "The message content to send to the user.",
                    "title": "Content",
                    "type": "string"
                }
            },
            "required": ["content"],
            "title": "parameters",
            "type": "object"
        }
    }
}, indent=2)}


Please respond in JSON format.
The JSON should contain:
- "name": the tool call function name.
- "arguments": the arguments for the tool call.

You should only use one tool at a time!
You cannot respond to user and use a tool at the same time!

Examples of responses:
<json>
{json.dumps({"name": "echo", "arguments": {"message": "test"}}, indent=2)}
</json>

<json>
{json.dumps({"name": RESPOND_ACTION_NAME, "arguments": {"content": "Hello, how can I help you today?"}}, indent=2)}
</json>
"""

    def get_init_state(self, message_history: Optional[list] = None) -> LLMAgentState:
        """Get the initial state of the agent."""
        if message_history is None:
            message_history = []
        self._is_first_message = True
        return LLMAgentState(
            system_messages=[SystemMessage(role="system", content=self.agent_prompt)],
            messages=message_history,
        )

    def set_seed(self, seed: int):
        """Set random seed (no-op for remote agent)."""
        pass

    def stop(self, last_message=None, state=None):
        """Stop the agent (no-op for remote agent)."""
        pass

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: LLMAgentState
    ) -> tuple[AssistantMessage, LLMAgentState]:
        """
        Generate the next message by delegating to the remote purple agent.

        This method is synchronous (as required by tau2), but internally
        uses asyncio to communicate with the remote agent.
        """
        # Update state with incoming message
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

        outgoing_text = extract_text_from_message(message)

        # If first message, prepend system prompt and all messages.
        if self._is_first_message:
            outgoing_text = f"{self.agent_prompt}\n\nNow here are the user messages:\n{'\n'.join([extract_text_from_message(message) for message in state.messages])}"

        # Call remote agent via A2A
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            response = loop.run_until_complete(
                self.messenger.talk_to_agent(
                    message=outgoing_text,
                    url=str(self.agent_url),
                    new_conversation=self._is_first_message,
                    timeout=self.timeout_seconds,
                    retries=self.retries,
                )
            )
        except Exception as exc:
            raise RemoteAgentError(str(exc)) from exc
        self._is_first_message = False

        # Parse the response
        assistant_message = self._parse_response(response)
        state.messages.append(assistant_message)

        return assistant_message, state

    def _parse_response(self, response: str) -> AssistantMessage:
        """Parse the purple agent's response into an AssistantMessage."""
        try:
            payload = _extract_json_payload(response)
            action_dict = json.loads(payload)
            if "name" not in action_dict or "arguments" not in action_dict:
                raise InvalidResponseError("Missing 'name' or 'arguments' in response JSON.")

            is_tool_call = action_dict["name"] != RESPOND_ACTION_NAME

            if not is_tool_call:
                # Response to user
                return AssistantMessage(
                    role="assistant",
                    content=action_dict["arguments"]["content"],
                    tool_calls=None,
                )
            else:
                # Tool call
                tool_call = ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=action_dict["name"],
                    arguments=action_dict["arguments"],
                    requestor="assistant",
                )
                return AssistantMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[tool_call],
                )
        except (json.JSONDecodeError, KeyError, InvalidResponseError) as e:
            raise InvalidResponseError(f"Invalid response payload: {e}") from e


class Agent:
    """Green agent that evaluates purple agents using tau2's native Orchestrator."""

    required_roles: list[str] = ["agent"]  # The purple agent being tested
    required_config_keys: list[str] = []

    def __init__(self):
        self.messenger = Messenger()

    def validate_request(self, request: EvalRequest) -> tuple[bool, str]:
        missing_roles = set(self.required_roles) - set(request.participants.keys())
        if missing_roles:
            return False, f"Missing roles: {missing_roles}"
        missing_config_keys = set(self.required_config_keys) - set(request.config.keys())
        if missing_config_keys:
            return False, f"Missing config keys: {missing_config_keys}"
        return True, "ok"

    async def run(self, message: Message, updater: TaskUpdater) -> None:
        """Run tau2 evaluation on the purple agent."""
        input_text = get_message_text(message)

        try:
            request: EvalRequest = EvalRequest.model_validate_json(input_text)
            ok, msg = self.validate_request(request)
            if not ok:
                await updater.reject(new_agent_text_message(msg))
                return
        except ValidationError as e:
            await updater.reject(new_agent_text_message(f"Invalid request: {e}"))
            return

        try:
            config = EvalConfig.model_validate(request.config)
        except ValidationError as e:
            await updater.reject(new_agent_text_message(f"Invalid config: {e}"))
            return

        logger.info(
            "Starting tau2 evaluation: domain=%s num_tasks=%s seed=%s timeout_seconds=%s max_steps=%s retries=%s",
            config.domain,
            config.num_tasks,
            config.seed,
            config.timeout_seconds,
            config.max_steps,
            config.retries,
        )
        start_time = time.perf_counter()

        domain = config.domain
        task_ids = config.task_ids
        num_tasks = config.num_tasks
        max_steps = config.max_steps
        user_llm = config.user_llm
        user_llm_args = config.user_llm_args

        # Get the purple agent URL
        agent_url = str(request.participants["agent"])

        # Get task objects
        task_set_name = domain
        task_split_name = "base"
        if task_ids is None:
            tasks = get_tasks(task_set_name=task_set_name, task_split_name=task_split_name)
        else:
            tasks = get_tasks(
                task_set_name=task_set_name,
                task_split_name=task_split_name,
                task_ids=task_ids,
            )

        tasks = tasks[:num_tasks]

        logger.info("Running %s tasks for domain %s against %s", len(tasks), domain, agent_url)

        await updater.update_status(
            TaskState.working,
            new_agent_text_message(f"Starting evaluation of {len(tasks)} tasks in {domain} domain")
        )

        metrics: dict[str, Any] = {"tasks": {}}
        task_results: list[TaskResult] = []

        try:
            for idx, task in enumerate(tasks):
                task_id = task.id
                logger.info("Task start: id=%s", task_id)
                await updater.update_status(
                    TaskState.working,
                    new_agent_text_message(f"Running task {task_id}...")
                )

                task_start = time.perf_counter()
                run_data: Optional[TaskRunData] = None
                error_summary: Optional[str] = None
                try:
                    run_data = await asyncio.wait_for(
                        self._run_single_task(
                            agent_url=agent_url,
                            domain=domain,
                            task=task,
                            max_steps=max_steps,
                            user_llm=user_llm,
                            user_llm_args=user_llm_args,
                            seed=config.seed + idx,
                            timeout_seconds=config.timeout_seconds,
                            retries=config.retries,
                        ),
                        timeout=config.timeout_seconds,
                    )
                    reward = run_data.reward
                    error_summary = run_data.eval_error
                    failure_reason = self._classify_failure(
                        run_data,
                        error=error_summary,
                    )
                except asyncio.TimeoutError:
                    reward = 0.0
                    error_summary = f"Task exceeded {config.timeout_seconds}s timeout."
                    failure_reason = "timeout"
                    logger.warning("Task %s timeout after %ss", task_id, config.timeout_seconds)
                except InvalidResponseError as e:
                    reward = 0.0
                    error_summary = str(e)
                    failure_reason = "invalid_response"
                    logger.warning("Task %s invalid response: %s", task_id, e)
                except RemoteAgentError as e:
                    reward = 0.0
                    error_summary = str(e)
                    failure_reason = "agent_error"
                    logger.warning("Task %s agent error: %s", task_id, e)
                except Exception as e:
                    reward = 0.0
                    error_summary = str(e)
                    failure_reason = "unknown"
                    logger.exception("Task %s failed with unexpected error", task_id)

                duration_sec = time.perf_counter() - task_start
                turns = run_data.turns if run_data else 0
                tool_calls = run_data.tool_calls if run_data else 0
                passed = reward > 0

                metrics["tasks"][task_id] = reward
                task_results.append(
                    TaskResult(
                        task_id=task_id,
                        passed=passed,
                        reward=reward,
                        duration_sec=duration_sec,
                        turns=turns,
                        tool_calls=tool_calls,
                        failure_reason=None if passed else failure_reason,
                        error=None if passed else error_summary,
                    )
                )

                logger.info(
                    "Task end: id=%s reward=%s failure_reason=%s duration_sec=%.2f",
                    task_id,
                    reward,
                    failure_reason,
                    duration_sec,
                )

            time_used = time.perf_counter() - start_time
            total_reward = sum(metrics["tasks"].values())
            num_completed = len(metrics["tasks"])
            passed = sum(1 for result in task_results if result.passed)
            pass_rate = (total_reward / num_completed * 100) if num_completed > 0 else 0

            result_data = self._build_result_data(
                domain=domain,
                total_reward=total_reward,
                num_completed=num_completed,
                pass_rate=pass_rate,
                time_used=time_used,
                task_rewards=metrics["tasks"],
                task_results=task_results,
                config=config,
            )

            # Format task results for display
            task_results_str = "\n".join(
                f"  {task_id}: {'✓' if reward == 1.0 else '✗'} ({reward})"
                for task_id, reward in metrics["tasks"].items()
            )

            summary = f"""Tau2 Benchmark Results
Domain: {domain}
Tasks: {num_completed}
Pass Rate: {pass_rate:.1f}% ({passed}/{num_completed})
Time: {time_used:.1f}s

Task Results:
{task_results_str}"""

            await updater.add_artifact(
                parts=[
                    Part(root=TextPart(text=summary)),
                    Part(root=DataPart(data=result_data)),
                ],
                name="Result",
            )

        finally:
            self.messenger.reset()

    def _classify_failure(
        self,
        run_data: TaskRunData,
        error: Optional[str] = None,
    ) -> Optional[str]:
        if run_data.reward > 0:
            return None

        termination_reason = run_data.termination_reason
        if termination_reason == TerminationReason.MAX_STEPS.value:
            return "timeout"
        if termination_reason == TerminationReason.TOO_MANY_ERRORS.value or run_data.tool_error:
            return "tool_error"
        if termination_reason == TerminationReason.AGENT_ERROR.value:
            return "agent_error"
        if termination_reason == TerminationReason.USER_ERROR.value:
            return "policy_violation"
        if error:
            return "unknown"
        return "unknown"

    def _build_result_data(
        self,
        domain: str,
        total_reward: float,
        num_completed: int,
        pass_rate: float,
        time_used: float,
        task_rewards: dict[str, float],
        task_results: list[TaskResult],
        config: EvalConfig,
    ) -> dict[str, Any]:
        green_version = _get_version("tau2-green-agent", "0.1.0")
        tau2_version = _get_version("tau2", "unknown")

        return {
            "domain": domain,
            "score": total_reward,
            "max_score": num_completed,
            "pass_rate": pass_rate,
            "task_rewards": task_rewards,
            "time_used": time_used,
            "summary": {
                "pass_rate": pass_rate,
                "passed": sum(1 for result in task_results if result.passed),
                "total": num_completed,
                "time_used_sec": time_used,
            },
            "config": {
                "domain": config.domain,
                "num_tasks": config.num_tasks,
                "seed": config.seed,
                "timeout_seconds": config.timeout_seconds,
                "max_steps": config.max_steps,
                "retries": config.retries,
            },
            "tasks": [result.to_dict() for result in task_results],
            "system": {
                "green_agent_version": green_version,
                "tau2_bench_version": tau2_version,
            },
        }

    async def _run_single_task(
        self,
        agent_url: str,
        domain: str,
        task,
        max_steps: int,
        user_llm: str,
        user_llm_args: dict,
        seed: int,
        timeout_seconds: int,
        retries: int,
    ) -> TaskRunData:
        """Run a single tau-bench task using native Orchestrator and return reward data."""

        # Get environment from registry
        env_constructor = registry.get_env_constructor(domain)
        environment = env_constructor(solo_mode=False)

        # Create the remote agent wrapper
        agent = RemoteA2AAgent(
            tools=environment.get_tools(),
            domain_policy=environment.get_policy(),
            messenger=self.messenger,
            agent_url=agent_url,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )

        # Create user simulator
        user = UserSimulator(
            tools=environment.get_user_tools() if environment.user_tools else None,
            instructions=str(task.user_scenario),
            llm=user_llm,
            llm_args=user_llm_args,
        )

        # Create orchestrator
        orchestrator = Orchestrator(
            domain=domain,
            agent=agent,
            user=user,
            environment=environment,
            task=task,
            max_steps=max_steps,
            max_errors=10,
            seed=seed,
            solo_mode=False,
            validate_communication=False,
        )

        # Run the simulation
        simulation_run = await asyncio.to_thread(orchestrator.run)

        logger.info(f"Task {task.id} terminated: {simulation_run.termination_reason}")
        logger.debug(f"Task {task.id} messages: {len(simulation_run.messages)}")
        turns, tool_calls, tool_error = _count_turns_and_tool_calls(simulation_run.messages)

        # Evaluate the simulation
        try:
            reward_info = evaluate_simulation(
                simulation=simulation_run,
                task=task,
                evaluation_type=EvaluationType.ACTION,
                solo_mode=False,
                domain=domain,
            )
            reward = reward_info.reward
            eval_error = None
        except Exception as e:
            logger.error(f"Evaluation failed for task {task.id}: {e}")
            reward = 0.0
            eval_error = str(e)

        return TaskRunData(
            reward=reward,
            duration_sec=simulation_run.duration,
            turns=turns,
            tool_calls=tool_calls,
            termination_reason=simulation_run.termination_reason,
            tool_error=tool_error,
            eval_error=eval_error,
        )
