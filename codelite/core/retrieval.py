from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codelite.config import RuntimeConfig
from codelite.core.memory_runtime import MemoryRuntime
from codelite.core.tavily import TavilySearchClient
from codelite.storage.events import RuntimeLayout, utc_now


@dataclass(frozen=True)
class RetrievalDecision:
    prompt: str
    route: str
    retrieve: bool
    enough: bool
    reason: str
    query_terms: list[str]
    result_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetrievalRouter:
    def __init__(
        self,
        workspace_root: Path,
        layout: RuntimeLayout,
        runtime_config: RuntimeConfig,
        memory_runtime: MemoryRuntime | None = None,
        tavily_api_key: str = "",
        web_search_client: TavilySearchClient | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.layout = layout
        self.layout.ensure()
        self.max_results = runtime_config.retrieval_max_results
        self.memory_runtime = memory_runtime
        self.web_search_client = web_search_client or (TavilySearchClient(tavily_api_key) if tavily_api_key else None)

    def decide(self, prompt: str) -> RetrievalDecision:
        lowered = prompt.lower()
        query_terms = self._query_terms(prompt)
        web_requested = any(
            token in lowered
            for token in ("latest", "search", "look up", "google", "internet", "web", "互联网", "网上", "联网", "上网", "搜索", "查一下", "查一查", "最新")
        )
        if web_requested and self.web_search_client is not None and self.web_search_client.configured:
            route = "web"
            retrieve = True
            reason = "prompt requests internet or latest information and Tavily is configured"
        elif any(token in lowered for token in ("latest", "search", "look up", "查", "检索", "检索一下")):
            route = "local_docs"
            retrieve = True
            reason = "prompt explicitly requests retrieval but only local retrieval is available"
        elif any(token in lowered for token in (".py", "function", "class", "module", "代码", "实现")):
            route = "local_code"
            retrieve = True
            reason = "prompt points at code symbols or source files"
        elif any(token in lowered for token in ("readme", "docs", "文档", "计划")):
            route = "local_docs"
            retrieve = True
            reason = "prompt points at local documentation"
        else:
            route = "none"
            retrieve = False
            reason = "prompt can be handled from current context"

        decision = RetrievalDecision(
            prompt=prompt,
            route=route,
            retrieve=retrieve,
            enough=not retrieve,
            reason=reason,
            query_terms=query_terms,
        )
        self._audit({"event": "retrieval_decision", **decision.to_dict()})
        return decision

    def run(self, prompt: str) -> dict[str, Any]:
        decision = self.decide(prompt)
        results: list[dict[str, Any]] = []
        if decision.retrieve:
            results = self._search(decision.route, decision.query_terms, prompt=prompt)
        enough = bool(results) if decision.retrieve else True
        finalized = RetrievalDecision(
            **{
                **decision.to_dict(),
                "enough": enough,
                "result_count": len(results),
            }
        )
        payload = {
            "decision": finalized.to_dict(),
            "results": results,
        }
        self._audit({"event": "retrieval_run", **payload})
        if self.memory_runtime is not None:
            self.memory_runtime.remember(
                kind="retrieval",
                text=f"{finalized.route}: {prompt}",
                metadata={"route": finalized.route, "result_count": len(results)},
                evidence=results[:3],
            )
        return payload

    def _search(self, route: str, query_terms: list[str], *, prompt: str) -> list[dict[str, Any]]:
        if not query_terms:
            if route != "web":
                return []
        patterns = [term.lower() for term in query_terms]
        if route == "web":
            if self.web_search_client is None or not self.web_search_client.configured:
                return []
            payload = self.web_search_client.search(
                query=prompt,
                max_results=self.max_results,
                topic="news" if any(token in prompt.lower() for token in ("latest", "news", "最新", "新闻")) else "general",
                search_depth="advanced",
                include_answer=True,
            )
            answer = payload.get("answer")
            results: list[dict[str, Any]] = []
            if isinstance(answer, str) and answer.strip():
                results.append({"type": "answer", "text": answer.strip()[:400]})
            for item in payload.get("results") or []:
                results.append(
                    {
                        "type": "web",
                        "title": str(item.get("title", "")),
                        "url": str(item.get("url", "")),
                        "text": str(item.get("content", ""))[:400],
                    }
                )
            return results[: self.max_results + 1]
        if route == "local_docs":
            candidates = list(self.workspace_root.glob("README.md")) + list((self.workspace_root / "docs").rglob("*.md"))
        elif route == "local_code":
            candidates = list((self.workspace_root / "codelite").rglob("*.py")) + list((self.workspace_root / "tests").rglob("*.py"))
        else:
            return []

        results: list[dict[str, Any]] = []
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                lowered = line.lower()
                if not any(pattern in lowered for pattern in patterns):
                    continue
                results.append(
                    {
                        "path": str(path.relative_to(self.workspace_root)),
                        "line": line_number,
                        "text": line.strip()[:240],
                    }
                )
                if len(results) >= self.max_results:
                    return results
        return results

    def _audit(self, payload: dict[str, Any]) -> None:
        with self.layout.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp_utc": utc_now(), **payload}, ensure_ascii=False))
            handle.write("\n")

    @staticmethod
    def _query_terms(prompt: str) -> list[str]:
        return sorted(
            {
                token.lower()
                for token in re.findall(r"[A-Za-z0-9_.-]{3,}", prompt)
                if token.lower() not in {"the", "and", "with", "from", "this", "that", "please"}
            }
        )[:6]
