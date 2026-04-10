from __future__ import annotations

import json
from typing import Any

from codelite.config import AppConfig
from codelite.core.context import ContextCompact
from codelite.core.heartbeat import HeartService
from codelite.core.llm import ModelClient, OpenAICompatibleClient
from codelite.core.memory_runtime import MemoryRuntime
from codelite.core.model_router import ModelRouter
from codelite.core.resilience import ResilienceRunner
from codelite.core.retrieval import RetrievalRouter
from codelite.core.skills_runtime import SkillRuntime
from codelite.core.system_prompt import build_system_prompt
from codelite.core.todo import TodoManager
from codelite.core.tools import ToolRouter
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
        retrieval_router: RetrievalRouter | None = None,
        model_router: ModelRouter | None = None,
        resilience_runner: ResilienceRunner | None = None,
        skill_runtime: SkillRuntime | None = None,
        memory_runtime: MemoryRuntime | None = None,
    ) -> None:
        self.config = config
        self.session_store = session_store
        self.tool_router = tool_router
        self.model_client = model_client or OpenAICompatibleClient(config.llm)
        self.todo_manager = todo_manager
        self.context_manager = context_manager
        self.heart_service = heart_service
        self.retrieval_router = retrieval_router
        self.model_router = model_router
        self.resilience_runner = resilience_runner
        self.skill_runtime = skill_runtime
        self.memory_runtime = memory_runtime

    def run_turn(
        self,
        session_id: str,
        user_input: str,
        *,
        require_plan: bool = False,
        tool_router_override: ToolRouter | None = None,
        extra_system_messages: list[str] | None = None,
    ) -> str:
        active_tool_router = tool_router_override or self.tool_router
        scoped_tool_router = active_tool_router.for_session(session_id)
        self.session_store.ensure_session(session_id)
        if self.todo_manager is not None:
            self.todo_manager.ensure_seeded(session_id, user_input)
        if self.heart_service is not None:
            self.heart_service.beat("agent_loop", active_task_count=1)
        if self.memory_runtime is not None:
            self.memory_runtime.remember(
                kind="prompt",
                text=user_input,
                metadata={"session_id": session_id},
            )

        self.session_store.append_event(session_id, "turn_started", {"prompt": user_input})
        self.session_store.append_message(session_id, role="user", content=user_input)

        retrieval_payload: dict[str, Any] | None = None
        if self.retrieval_router is not None:
            retrieval_payload = self.retrieval_router.run(user_input)
            self.session_store.append_event(session_id, "retrieval_decision", retrieval_payload)

        selected_profile = self.model_router.select_profile(user_input) if self.model_router is not None else None

        prompt_parts = build_system_prompt(
            base_prompt=self.config.agent.system_prompt,
            workspace_root=self.config.workspace_root,
            session_id=session_id,
            profile_name=selected_profile.name if selected_profile is not None else "fast",
            enable_dynamic_boundary=self.config.runtime.prompt_dynamic_boundary_enabled,
        )
        messages = [{"role": "system", "content": prompt_parts.full_prompt}]
        if extra_system_messages:
            for content in extra_system_messages:
                text = str(content).strip()
                if text:
                    messages.append({"role": "system", "content": text})
        if retrieval_payload is not None:
            decision = retrieval_payload["decision"]
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Retrieval router decision: "
                        f"route={decision['route']}, enough={decision['enough']}, reason={decision['reason']}"
                    ),
                }
            )
            retrieval_results = retrieval_payload.get("results") or []
            if retrieval_results:
                messages.append(
                    {
                        "role": "system",
                        "content": "Retrieved context:\n" + self._format_retrieval_results(retrieval_results),
                    }
                )
        if selected_profile is not None:
            messages.append(
                {
                    "role": "system",
                    "content": f"Model router selected profile `{selected_profile.name}` because {selected_profile.reason}.",
                }
            )
        if self.memory_runtime is not None:
            memory_bundle = self.memory_runtime.assemble_context(
                budget_chars=max(800, int(self.config.runtime.context_auto_compact_char_count * 0.35)),
            )
            memory_report = dict(memory_bundle.get("report") or {})
            if memory_report:
                self.session_store.append_event(
                    session_id,
                    "memory_context_assembled",
                    memory_report,
                )
            memory_message = str(memory_bundle.get("system_message_text") or "").strip()
            if memory_message:
                messages.append({"role": "system", "content": memory_message})
        messages.extend(self.session_store.load_messages(session_id))
        if self.context_manager is not None:
            messages = self.context_manager.prepare(session_id, messages)
        tools = scoped_tool_router.tool_schemas()

        plan_gate_active = bool(require_plan and self.config.runtime.auto_plan_enabled)

        for step in range(1, self.config.runtime.max_steps + 1):
            if plan_gate_active:
                if self._has_agent_todo_update(session_id):
                    plan_gate_active = False
                else:
                    plan_gate_message = self._planning_gate_message()
                    messages.append({"role": "system", "content": plan_gate_message})
                    self.session_store.append_event(
                        session_id,
                        "auto_plan_gate_injected",
                        {"step": step, "message": plan_gate_message},
                    )
            nag_message = self.skill_runtime.maybe_todo_nag(session_id, step) if self.skill_runtime is not None else None
            if nag_message:
                messages.append({"role": "system", "content": nag_message})
                self.session_store.append_event(session_id, "todo_nag", {"step": step, "message": nag_message})
            self.session_store.append_event(
                session_id,
                "model_request",
                {"step": step, "message_count": len(messages)},
            )

            try:
                if self.resilience_runner is not None:
                    resilience_result = self.resilience_runner.complete(
                        messages=messages,
                        tools=tools,
                        preferred_profile=selected_profile.name if selected_profile is not None else "fast",
                        primary_client=self.model_client,
                        session_id=session_id,
                    )
                    result = resilience_result.result
                    self.session_store.append_event(
                        session_id,
                        "resilience_result",
                        resilience_result.to_dict(),
                    )
                else:
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
                        {"step": step, "tool_name": call.name, "arguments": call.arguments, "tool_call_id": call.id},
                    )

                tool_results = scoped_tool_router.execute_tool_calls(result.tool_calls)
                call_lookup = {call.id: call for call in result.tool_calls}

                for item in tool_results:
                    call = call_lookup.get(item.call_id)
                    call_id = item.call_id or (call.id if call is not None else "")
                    tool_name = item.name or (call.name if call is not None else "")
                    status = "ok" if item.ok else "error"
                    payload = {
                        "step": step,
                        "tool_name": tool_name,
                        "status": status,
                        "tool_call_id": call_id,
                        "duration_ms": item.duration_ms,
                    }
                    if item.error:
                        payload["error"] = item.error
                    if item.metadata:
                        payload["metadata"] = item.metadata
                    self.session_store.append_event(session_id, "tool_finished", payload)
                    tool_message: dict[str, Any] = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": tool_name,
                        "content": item.output,
                    }
                    messages.append(tool_message)
                    self.session_store.append_message(
                        session_id,
                        role="tool",
                        name=tool_name,
                        tool_call_id=call_id,
                        content=item.output,
                    )
                continue

            answer = (result.text or "").strip() or "Model returned no text result."
            if self.todo_manager is not None:
                self.todo_manager.mark_auto_seeded_done(session_id)
            if self.heart_service is not None:
                self.heart_service.beat("agent_loop", active_task_count=0)
            if self.memory_runtime is not None:
                self.memory_runtime.remember(
                    kind="answer",
                    text=answer,
                    metadata={"session_id": session_id, "profile": selected_profile.name if selected_profile else "fast"},
                    evidence=[{"prompt": user_input[:120]}],
                )
            self.session_store.append_message(session_id, role="assistant", content=answer)
            self.session_store.append_event(
                session_id,
                "turn_finished",
                {"step": step, "answer_preview": answer[:200]},
            )
            return answer

        raise RuntimeError(f"Reached max steps: {self.config.runtime.max_steps}")

    def _has_agent_todo_update(self, session_id: str) -> bool:
        events = self.session_store.replay(session_id)
        return any(
            event.get("event_type") == "todo_updated"
            and str((event.get("payload") or {}).get("source", "")) == "agent"
            for event in events
        )

    @staticmethod
    def _planning_gate_message() -> str:
        return (
            "Planning gate: before mutating actions, call todo_write with 3-7 concrete steps, "
            "set exactly one item to in_progress, and mark completed items as done as you proceed."
        )

    @staticmethod
    def _format_retrieval_results(results: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for index, item in enumerate(results[:5], start=1):
            if item.get("type") == "answer":
                lines.append(f"{index}. answer: {str(item.get('text', ''))[:240]}")
                continue
            if item.get("type") == "web":
                title = str(item.get("title", "")).strip()
                url = str(item.get("url", "")).strip()
                text = str(item.get("text", "")).strip()
                lines.append(f"{index}. web: {title} | {url} | {text[:180]}")
                continue
            path = str(item.get("path", "")).strip()
            line = item.get("line", "")
            text = str(item.get("text", "")).strip()
            lines.append(f"{index}. local: {path}:{line} | {text[:180]}")
        return "\n".join(lines)
