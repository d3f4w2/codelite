from __future__ import annotations

import json
from typing import Any

from codelite.config import AppConfig
from codelite.core.context import ContextCompact
from codelite.core.heartbeat import HeartService
from codelite.core.llm import ModelClient, OpenAICompatibleClient
from codelite.core.todo import TodoManager
from codelite.core.tools import ToolError, ToolRouter
from codelite.storage.sessions import SessionStore


class AgentLoop:
    def __init__(
        self,
        config: AppConfig,
        session_store: SessionStore,
        tool_router: ToolRouter,
        model_client: ModelClient | None = None,
        *,
        todo_manager: TodoManager | None = None,
        context_manager: ContextCompact | None = None,
        heart_service: HeartService | None = None,
    ) -> None:
        self.config = config
        self.session_store = session_store
        self.tool_router = tool_router
        self.model_client = model_client or OpenAICompatibleClient(config.llm)
        self.todo_manager = todo_manager
        self.context_manager = context_manager
        self.heart_service = heart_service

    def run_turn(self, session_id: str, user_input: str) -> str:
        scoped_tool_router = self.tool_router.for_session(session_id)
        self.session_store.ensure_session(session_id)
        if self.todo_manager is not None:
            self.todo_manager.ensure_seeded(session_id, user_input)
        if self.heart_service is not None:
            self.heart_service.beat("agent_loop", active_task_count=1)

        self.session_store.append_event(session_id, "turn_started", {"prompt": user_input})
        self.session_store.append_message(session_id, role="user", content=user_input)

        messages = [{"role": "system", "content": self.config.agent.system_prompt}]
        messages.extend(self.session_store.load_messages(session_id))
        if self.context_manager is not None:
            messages = self.context_manager.prepare(session_id, messages)
        tools = scoped_tool_router.tool_schemas()

        for step in range(1, self.config.runtime.max_steps + 1):
            self.session_store.append_event(
                session_id,
                "model_request",
                {"step": step, "message_count": len(messages)},
            )

            try:
                result = self.model_client.complete(messages, tools)
            except Exception as exc:
                if self.heart_service is not None:
                    self.heart_service.beat("agent_loop", status="yellow", last_error=str(exc))
                self.session_store.append_event(
                    session_id,
                    "turn_failed",
                    {"step": step, "error": str(exc)},
                )
                raise

            self.session_store.append_event(
                session_id,
                "model_response",
                {
                    "step": step,
                    "tool_call_count": len(result.tool_calls),
                    "usage": result.usage or {},
                },
            )

            if result.tool_calls:
                tool_calls_payload = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in result.tool_calls
                ]
                messages.append(
                    {
                        "role": "assistant",
                        "content": result.text or "",
                        "tool_calls": tool_calls_payload,
                    }
                )
                self.session_store.append_message(
                    session_id,
                    role="assistant",
                    content=result.text or "",
                    tool_calls=tool_calls_payload,
                )

                for call in result.tool_calls:
                    self.session_store.append_event(
                        session_id,
                        "tool_started",
                        {"step": step, "tool_name": call.name, "arguments": call.arguments},
                    )
                    try:
                        tool_result = scoped_tool_router.dispatch(call.name, call.arguments)
                        tool_content = tool_result.output
                        self.session_store.append_event(
                            session_id,
                            "tool_finished",
                            {"step": step, "tool_name": call.name, "status": "ok"},
                        )
                    except ToolError as exc:
                        tool_content = f"TOOL_ERROR: {exc}"
                        self.session_store.append_event(
                            session_id,
                            "tool_finished",
                            {
                                "step": step,
                                "tool_name": call.name,
                                "status": "error",
                                "error": str(exc),
                            },
                        )

                    tool_message: dict[str, Any] = {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": tool_content,
                    }
                    messages.append(tool_message)
                    self.session_store.append_message(
                        session_id,
                        role="tool",
                        name=call.name,
                        tool_call_id=call.id,
                        content=tool_content,
                    )
                continue

            answer = (result.text or "").strip() or "Model returned no text result."
            if self.todo_manager is not None:
                self.todo_manager.mark_auto_seeded_done(session_id)
            if self.heart_service is not None:
                self.heart_service.beat("agent_loop", active_task_count=0)
            self.session_store.append_message(session_id, role="assistant", content=answer)
            self.session_store.append_event(
                session_id,
                "turn_finished",
                {"step": step, "answer_preview": answer[:200]},
            )
            return answer

        raise RuntimeError(f"Reached max steps: {self.config.runtime.max_steps}")
