"""Research-only Codex app-server protocol spike.

This path was closed by the user on 2026-07-20 because it consumes separate
Codex usage limits rather than ordinary ChatGPT-chat capacity. It is not an
Alexandria provider, runtime dependency, release requirement, or active plan.
The module is retained temporarily as historical implementation evidence and
should be excluded or discarded during final clean-commit/release cleanup
unless the user explicitly reopens the direction.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from llm_schemas import get_schema, validate_contract


DEFAULT_COMMAND = (
    "codex",
    "app-server",
    "--stdio",
)

SAFE_TURN_ITEM_TYPES = frozenset(
    {
        "agentMessage",
        "reasoning",
        "plan",
        "userMessage",
    }
)


class CodexAppServerError(RuntimeError):
    """Base error for the Alexandria Codex app-server boundary."""


class CodexAppServerUnavailable(CodexAppServerError):
    """The local app-server process is missing, stopped, or timed out."""


class CodexAppServerProtocolError(CodexAppServerError):
    """The app-server returned an invalid or rejected protocol message."""


class CodexAppServerRateLimited(CodexAppServerError):
    """The connected ChatGPT account cannot currently run Codex work."""


class CodexAppServerToolAttempt(CodexAppServerError):
    """A structured-generation turn attempted to use an agent tool."""


class JsonRpcTransport(Protocol):
    def start(self) -> None:
        ...

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        ...

    def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        ...

    def next_notification(
        self,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class CodexAccountStatus:
    authenticated: bool
    auth_mode: str | None
    plan_type: str | None
    requires_openai_auth: bool
    email_present: bool
    rate_limit_reached: bool
    rate_limit_reached_type: str | None
    primary_window: dict[str, Any] | None
    secondary_window: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "auth_mode": self.auth_mode,
            "plan_type": self.plan_type,
            "requires_openai_auth": self.requires_openai_auth,
            "email_present": self.email_present,
            "rate_limit_reached": self.rate_limit_reached,
            "rate_limit_reached_type": self.rate_limit_reached_type,
            "primary_window": self.primary_window,
            "secondary_window": self.secondary_window,
        }


@dataclass(frozen=True)
class CodexStructuredResult:
    data: Any
    content: str
    contract: str
    backend: str
    metrics: dict[str, Any]


class StdioJsonRpcTransport:
    """Supervise one Codex app-server process over newline-delimited JSON."""

    def __init__(
        self,
        *,
        command: Sequence[str] = DEFAULT_COMMAND,
        cwd: Path | str | None = None,
        environment: Mapping[str, str] | None = None,
        default_timeout: float = 30.0,
        stderr_tail_lines: int = 40,
    ) -> None:
        normalized_command = tuple(
            str(part).strip()
            for part in command
            if str(part).strip()
        )
        if not normalized_command:
            raise ValueError("Codex app-server command cannot be empty")

        self.command = normalized_command
        self.cwd = Path(cwd).resolve() if cwd is not None else None
        self.environment = (
            {
                **os.environ,
                **dict(environment),
            }
            if environment is not None
            else None
        )
        self.default_timeout = float(default_timeout)
        self.stderr_tail = deque(maxlen=max(1, int(stderr_tail_lines)))

        self._process: subprocess.Popen[str] | None = None
        self._next_request_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._closed = False

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise CodexAppServerUnavailable(
                    "Codex app-server transport is closed"
                )
            if self._process is not None:
                return

            try:
                process = subprocess.Popen(
                    list(self.command),
                    cwd=str(self.cwd) if self.cwd is not None else None,
                    env=self.environment,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except (FileNotFoundError, OSError) as exc:
                raise CodexAppServerUnavailable(
                    f"Unable to start Codex app-server: {exc}"
                ) from exc

            if (
                process.stdin is None
                or process.stdout is None
                or process.stderr is None
            ):
                process.kill()
                raise CodexAppServerUnavailable(
                    "Codex app-server did not expose stdio pipes"
                )

            self._process = process

        threading.Thread(
            target=self._read_stdout,
            name="alexandria-codex-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            name="alexandria-codex-stderr",
            daemon=True,
        ).start()

    def _process_or_raise(self) -> subprocess.Popen[str]:
        process = self._process
        if process is None:
            raise CodexAppServerUnavailable(
                "Codex app-server transport has not started"
            )
        if process.poll() is not None:
            detail = "\n".join(self.stderr_tail)
            suffix = f": {detail}" if detail else ""
            raise CodexAppServerUnavailable(
                f"Codex app-server exited with code {process.returncode}{suffix}"
            )
        return process

    def _send(self, message: Mapping[str, Any]) -> None:
        process = self._process_or_raise()
        payload = json.dumps(
            dict(message),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._write_lock:
            try:
                assert process.stdin is not None
                process.stdin.write(payload + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise CodexAppServerUnavailable(
                    f"Codex app-server input failed: {exc}"
                ) from exc

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        self.start()
        normalized_method = str(method).strip()
        if not normalized_method:
            raise ValueError("JSON-RPC method cannot be empty")

        with self._state_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(
                maxsize=1
            )
            self._pending[request_id] = response_queue

        try:
            message: dict[str, Any] = {
                "method": normalized_method,
                "id": request_id,
            }
            if params is not None:
                message["params"] = dict(params)
            self._send(message)

            wait_seconds = (
                self.default_timeout
                if timeout is None
                else float(timeout)
            )
            try:
                response = response_queue.get(timeout=wait_seconds)
            except queue.Empty as exc:
                raise CodexAppServerUnavailable(
                    f"Codex app-server request timed out: {normalized_method}"
                ) from exc
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

        transport_error = response.get("_transport_error")
        if transport_error:
            raise CodexAppServerUnavailable(str(transport_error))

        error = response.get("error")
        if error is not None:
            if isinstance(error, Mapping):
                message = error.get("message") or json.dumps(error)
            else:
                message = str(error)
            raise CodexAppServerProtocolError(
                f"{normalized_method} failed: {message}"
            )

        return response.get("result")

    def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        self.start()
        message: dict[str, Any] = {
            "method": str(method).strip(),
        }
        if params is not None:
            message["params"] = dict(params)
        self._send(message)

    def next_notification(
        self,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        wait_seconds = (
            self.default_timeout
            if timeout is None
            else float(timeout)
        )
        try:
            notification = self._notifications.get(timeout=wait_seconds)
        except queue.Empty as exc:
            raise CodexAppServerUnavailable(
                "Timed out waiting for a Codex app-server notification"
            ) from exc

        transport_error = notification.get("_transport_error")
        if transport_error:
            raise CodexAppServerUnavailable(str(transport_error))
        return notification

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._fail_pending(
                        f"Codex app-server emitted invalid JSON: {exc}"
                    )
                    continue
                if not isinstance(message, dict):
                    self._fail_pending(
                        "Codex app-server emitted a non-object message"
                    )
                    continue

                request_id = message.get("id")
                method = message.get("method")
                if isinstance(request_id, int) and isinstance(method, str):
                    self._deny_server_request(message)
                    continue
                if isinstance(request_id, int):
                    with self._state_lock:
                        response_queue = self._pending.get(request_id)
                    if response_queue is not None:
                        response_queue.put(message)
                    continue
                if isinstance(method, str):
                    self._notifications.put(message)
        finally:
            return_code = process.poll()
            self._fail_pending(
                f"Codex app-server output closed"
                + (
                    f" with code {return_code}"
                    if return_code is not None
                    else ""
                )
            )

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw_line in process.stderr:
            line = raw_line.strip()
            if line:
                self.stderr_tail.append(line)

    def _deny_server_request(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if not isinstance(request_id, int):
            return

        if method == "item/permissions/requestApproval":
            result: dict[str, Any] = {
                "scope": "turn",
                "permissions": {},
            }
        elif method.endswith("/requestApproval"):
            result = {"decision": "decline"}
        else:
            self._send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": (
                            "Alexandria structured generation does not "
                            "permit interactive server requests"
                        ),
                    },
                }
            )
            return

        self._send(
            {
                "id": request_id,
                "result": result,
            }
        )

    def _fail_pending(self, message: str) -> None:
        with self._state_lock:
            pending = tuple(self._pending.values())
        envelope = {"_transport_error": message}
        for response_queue in pending:
            try:
                response_queue.put_nowait(envelope)
            except queue.Full:
                pass
        self._notifications.put(envelope)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            self._process = None

        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def __enter__(self) -> StdioJsonRpcTransport:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class CodexAppServerClient:
    """Stable Alexandria-facing subset of the Codex app-server protocol.

    The client intentionally omits token and email values from status output,
    uses ephemeral threads, denies approval requests, and rejects any tool item
    during a structured turn. Product integration must still run turns inside
    a dedicated isolated worker rather than exposing the user project as cwd.
    """

    def __init__(
        self,
        *,
        transport: JsonRpcTransport | None = None,
        request_timeout: float = 30.0,
        turn_timeout: float = 1800.0,
        client_version: str = "0.1.0",
    ) -> None:
        self.transport = transport or StdioJsonRpcTransport(
            default_timeout=request_timeout
        )
        self.request_timeout = float(request_timeout)
        self.turn_timeout = float(turn_timeout)
        self.client_version = str(client_version).strip() or "0.1.0"
        self._initialized = False
        self._initialize_result: dict[str, Any] = {}

    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return dict(self._initialize_result)

        self.transport.start()
        result = self.transport.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "alexandria_audiobook",
                    "title": "Alexandria Audiobook Generator",
                    "version": self.client_version,
                },
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [
                        "item/agentMessage/delta",
                    ],
                },
            },
            timeout=self.request_timeout,
        )
        if not isinstance(result, Mapping):
            raise CodexAppServerProtocolError(
                "initialize returned a non-object result"
            )
        self.transport.notify("initialized", {})
        self._initialize_result = dict(result)
        self._initialized = True
        return dict(self._initialize_result)

    def account_status(
        self,
        *,
        refresh_token: bool = False,
    ) -> CodexAccountStatus:
        self.initialize()
        account_result = self.transport.request(
            "account/read",
            {"refreshToken": bool(refresh_token)},
            timeout=self.request_timeout,
        )
        limits_result = self.transport.request(
            "account/rateLimits/read",
            {},
            timeout=self.request_timeout,
        )

        account_payload = (
            dict(account_result)
            if isinstance(account_result, Mapping)
            else {}
        )
        account = account_payload.get("account")
        account = dict(account) if isinstance(account, Mapping) else {}

        limits_payload = (
            dict(limits_result)
            if isinstance(limits_result, Mapping)
            else {}
        )
        rate_limits = limits_payload.get("rateLimits")
        rate_limits = (
            dict(rate_limits)
            if isinstance(rate_limits, Mapping)
            else {}
        )
        primary = rate_limits.get("primary")
        secondary = rate_limits.get("secondary")
        primary_window = (
            dict(primary)
            if isinstance(primary, Mapping)
            else None
        )
        secondary_window = (
            dict(secondary)
            if isinstance(secondary, Mapping)
            else None
        )
        reached_type = rate_limits.get("rateLimitReachedType")
        reached_type = (
            str(reached_type)
            if reached_type is not None
            else None
        )
        primary_used = (
            primary_window.get("usedPercent")
            if primary_window is not None
            else None
        )
        secondary_used = (
            secondary_window.get("usedPercent")
            if secondary_window is not None
            else None
        )
        rate_limit_reached = bool(reached_type)
        if (
            isinstance(primary_used, (int, float))
            and primary_used >= 100
        ) or (
            isinstance(secondary_used, (int, float))
            and secondary_used >= 100
        ):
            rate_limit_reached = True

        auth_mode = account.get("type")
        auth_mode = str(auth_mode) if auth_mode is not None else None
        plan_type = account.get("planType")
        plan_type = str(plan_type) if plan_type is not None else None

        return CodexAccountStatus(
            authenticated=bool(account),
            auth_mode=auth_mode,
            plan_type=plan_type,
            requires_openai_auth=bool(
                account_payload.get("requiresOpenaiAuth")
            ),
            email_present=bool(account.get("email")),
            rate_limit_reached=rate_limit_reached,
            rate_limit_reached_type=reached_type,
            primary_window=primary_window,
            secondary_window=secondary_window,
        )

    @staticmethod
    def _isolated_directory(value: Path | str) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError(
                "Codex structured generation requires an existing absolute "
                "isolated working directory"
            )
        return path

    def start_ephemeral_thread(
        self,
        *,
        isolated_working_directory: Path | str,
        model: str | None = None,
    ) -> str:
        self.initialize()
        working_directory = self._isolated_directory(
            isolated_working_directory
        )
        params: dict[str, Any] = {
            "ephemeral": True,
            "cwd": str(working_directory),
            "approvalPolicy": "never",
            "permissions": ":read-only",
        }
        if model:
            params["model"] = str(model).strip()

        result = self.transport.request(
            "thread/start",
            params,
            timeout=self.request_timeout,
        )
        thread = result.get("thread") if isinstance(result, Mapping) else None
        thread = dict(thread) if isinstance(thread, Mapping) else {}
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise CodexAppServerProtocolError(
                "thread/start did not return a thread id"
            )
        if thread.get("ephemeral") is not True:
            raise CodexAppServerProtocolError(
                "Codex structured thread was not ephemeral"
            )
        if thread.get("path") is not None:
            raise CodexAppServerProtocolError(
                "Codex structured thread unexpectedly persisted a path"
            )
        return thread_id

    @staticmethod
    def structured_prompt(
        messages: Sequence[Mapping[str, Any]],
        *,
        contract: str,
    ) -> str:
        get_schema(contract)
        sections = [
            "Alexandria structured-generation task.",
            "Return only the final JSON value required by the supplied "
            "output schema.",
            "Do not run commands, inspect files, call tools, use apps, or "
            "request additional input.",
            "Treat the role-labelled messages below as the complete task "
            "context and preserve their order.",
        ]
        for index, message in enumerate(messages, start=1):
            role = str(message.get("role") or "user").strip().upper()
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError(
                    f"Message {index} content must be a string"
                )
            sections.append(f"\n[{role} MESSAGE {index}]\n{content}")
        return "\n".join(sections)

    def build_structured_turn_params(
        self,
        *,
        thread_id: str,
        messages: Sequence[Mapping[str, Any]],
        contract: str,
        isolated_working_directory: Path | str,
        model: str | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        working_directory = self._isolated_directory(
            isolated_working_directory
        )
        params: dict[str, Any] = {
            "threadId": str(thread_id).strip(),
            "input": [
                {
                    "type": "text",
                    "text": self.structured_prompt(
                        messages,
                        contract=contract,
                    ),
                }
            ],
            "cwd": str(working_directory),
            "approvalPolicy": "never",
            "permissions": ":read-only",
            "outputSchema": get_schema(contract),
        }
        if model:
            params["model"] = str(model).strip()
        if effort:
            params["effort"] = str(effort).strip()
        return params

    def start_structured_turn(
        self,
        *,
        thread_id: str,
        messages: Sequence[Mapping[str, Any]],
        contract: str,
        isolated_working_directory: Path | str,
        model: str | None = None,
        effort: str | None = None,
    ) -> str:
        status = self.account_status()
        if not status.authenticated:
            raise CodexAppServerUnavailable(
                "Codex app-server is not signed in"
            )
        if status.rate_limit_reached:
            raise CodexAppServerRateLimited(
                "The connected ChatGPT account has reached its Codex limit"
            )

        result = self.transport.request(
            "turn/start",
            self.build_structured_turn_params(
                thread_id=thread_id,
                messages=messages,
                contract=contract,
                isolated_working_directory=isolated_working_directory,
                model=model,
                effort=effort,
            ),
            timeout=self.request_timeout,
        )
        turn = result.get("turn") if isinstance(result, Mapping) else None
        turn = dict(turn) if isinstance(turn, Mapping) else {}
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise CodexAppServerProtocolError(
                "turn/start did not return a turn id"
            )
        return turn_id

    @staticmethod
    def _status_name(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            nested = value.get("type")
            return str(nested) if nested is not None else ""
        return ""

    @staticmethod
    def _parse_json_content(content: str) -> Any:
        text = content.strip()
        if not text:
            raise CodexAppServerProtocolError(
                "Codex structured turn returned an empty final message"
            )
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            starts = [
                index
                for index in (text.find("{"), text.find("["))
                if index >= 0
            ]
            if not starts:
                raise CodexAppServerProtocolError(
                    "Codex structured turn did not contain JSON"
                )
            try:
                value, _ = json.JSONDecoder().raw_decode(text[min(starts):])
            except json.JSONDecodeError as exc:
                raise CodexAppServerProtocolError(
                    f"Codex structured turn returned invalid JSON: {exc}"
                ) from exc
            return value

    def collect_structured_turn(
        self,
        *,
        thread_id: str,
        turn_id: str,
        contract: str,
        timeout: float | None = None,
    ) -> CodexStructuredResult:
        started = time.perf_counter()
        deadline = time.monotonic() + (
            self.turn_timeout if timeout is None else float(timeout)
        )
        content: str | None = None
        attempted_item_type: str | None = None
        token_usage: dict[str, Any] | None = None

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    self.transport.request(
                        "turn/interrupt",
                        {
                            "threadId": thread_id,
                            "turnId": turn_id,
                        },
                        timeout=self.request_timeout,
                    )
                finally:
                    raise CodexAppServerUnavailable(
                        "Codex structured turn timed out"
                    )

            event = self.transport.next_notification(timeout=remaining)
            method = event.get("method")
            params = event.get("params")
            params = dict(params) if isinstance(params, Mapping) else {}

            if method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage")
                if isinstance(usage, Mapping):
                    token_usage = dict(usage)
                continue

            if method in {"item/started", "item/completed"}:
                item = params.get("item")
                item = dict(item) if isinstance(item, Mapping) else {}
                item_type = str(item.get("type") or "")
                if item_type and item_type not in SAFE_TURN_ITEM_TYPES:
                    attempted_item_type = item_type
                    self.transport.request(
                        "turn/interrupt",
                        {
                            "threadId": thread_id,
                            "turnId": turn_id,
                        },
                        timeout=self.request_timeout,
                    )
                    raise CodexAppServerToolAttempt(
                        "Codex structured generation attempted disallowed "
                        f"item type: {item_type}"
                    )
                if (
                    method == "item/completed"
                    and item_type == "agentMessage"
                    and isinstance(item.get("text"), str)
                ):
                    content = item["text"]
                continue

            if method != "turn/completed":
                continue

            turn = params.get("turn")
            turn = dict(turn) if isinstance(turn, Mapping) else {}
            event_turn_id = turn.get("id")
            if event_turn_id not in {None, turn_id}:
                continue
            status = self._status_name(turn.get("status"))
            if status != "completed":
                error = turn.get("error")
                if isinstance(error, Mapping):
                    detail = error.get("message") or json.dumps(error)
                else:
                    detail = str(error or status or "unknown failure")
                raise CodexAppServerProtocolError(
                    f"Codex structured turn ended as {status}: {detail}"
                )
            break

        if attempted_item_type is not None:
            raise CodexAppServerToolAttempt(
                f"Codex attempted disallowed item type: {attempted_item_type}"
            )
        if content is None:
            raise CodexAppServerProtocolError(
                "Codex structured turn completed without an agent message"
            )

        parsed = self._parse_json_content(content)
        validated = validate_contract(contract, parsed)
        elapsed = time.perf_counter() - started
        return CodexStructuredResult(
            data=validated,
            content=content,
            contract=contract,
            backend="codex-app-server",
            metrics={
                "request_wall_seconds": elapsed,
                "token_usage": token_usage,
                "tool_attempted": False,
                "ephemeral_thread": True,
                "native_schema_validation": True,
            },
        )

    def complete_json(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        contract: str,
        isolated_working_directory: Path | str,
        model: str | None = None,
        effort: str | None = None,
        timeout: float | None = None,
    ) -> CodexStructuredResult:
        thread_id = self.start_ephemeral_thread(
            isolated_working_directory=isolated_working_directory,
            model=model,
        )
        turn_id = self.start_structured_turn(
            thread_id=thread_id,
            messages=messages,
            contract=contract,
            isolated_working_directory=isolated_working_directory,
            model=model,
            effort=effort,
        )
        return self.collect_structured_turn(
            thread_id=thread_id,
            turn_id=turn_id,
            contract=contract,
            timeout=timeout,
        )

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> CodexAppServerClient:
        self.initialize()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
