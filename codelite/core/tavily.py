from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TavilySearchResult:
    title: str
    url: str
    content: str
    score: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TavilySearchResult:
        score = payload.get("score")
        return cls(
            title=str(payload.get("title", "")),
            url=str(payload.get("url", "")),
            content=str(payload.get("content", "")),
            score=float(score) if isinstance(score, (int, float)) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "title": self.title,
            "url": self.url,
            "content": self.content,
        }
        if self.score is not None:
            payload["score"] = self.score
        return payload


class TavilySearchClient:
    BASE_URL = "https://api.tavily.com/search"
    DEFAULT_HEADERS = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0 Safari/537.36"
        ),
    }

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        *,
        query: str,
        max_results: int = 5,
        topic: str = "general",
        search_depth: str = "basic",
        include_answer: bool = True,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Tavily API key 未配置，请设置 TAVILY_API_KEY。")

        payload = {
            "query": query,
            "topic": topic,
            "search_depth": search_depth,
            "max_results": max(1, min(int(max_results), 10)),
            "include_answer": include_answer,
            "include_raw_content": False,
        }
        request = urllib.request.Request(
            url=self.BASE_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                **self.DEFAULT_HEADERS,
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Tavily 请求失败: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"Tavily 请求失败: {exc.reason}") from exc

        data = json.loads(body)
        results = [TavilySearchResult.from_dict(item).to_dict() for item in data.get("results") or []]
        return {
            "query": query,
            "answer": str(data.get("answer", "") or ""),
            "topic": topic,
            "search_depth": search_depth,
            "results": results,
        }
