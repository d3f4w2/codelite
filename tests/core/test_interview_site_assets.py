from __future__ import annotations

import re
from pathlib import Path


def test_interview_site_pages_and_assets_exist() -> None:
    repo = Path(__file__).resolve().parents[2]
    site_root = repo / "docs" / "interview-site"
    assert site_root.exists()

    pages = {
        "index.html": "home",
        "architecture.html": "architecture",
        "mechanisms.html": "mechanisms",
        "interview.html": "interview",
        "resume.html": "resume",
        "graph.html": "graph",
    }

    for page_name, page_id in pages.items():
        path = site_root / page_name
        text = path.read_text(encoding="utf-8")
        assert f'data-page="{page_id}"' in text
        assert 'href="assets/site.css"' in text
        assert 'src="assets/knowledge.js"' in text
        assert 'src="assets/app.js"' in text

    assert (site_root / "assets" / "knowledge.js").exists()
    assert (site_root / "assets" / "app.js").exists()
    assert (site_root / "assets" / "site.css").exists()
    assert (site_root / "README.md").exists()


def test_interview_site_knowledge_source_has_required_sections() -> None:
    repo = Path(__file__).resolve().parents[2]
    knowledge_path = repo / "docs" / "interview-site" / "assets" / "knowledge.js"
    text = knowledge_path.read_text(encoding="utf-8")

    required_markers = [
        "knowledge.projectMeta = {",
        "knowledge.highlights = [",
        "knowledge.timeline = [",
        "knowledge.architecture = {",
        "knowledge.mechanisms = [",
        "knowledge.interview = {",
        "knowledge.resume = {",
        "knowledge.graph = {",
        "window.CODELITE_KNOWLEDGE = knowledge;",
        'id: "task-worktree"',
        'id: "policy-hooks"',
        'id: "validate-pipeline"',
        'id: "agent-team-mcp-skills"',
    ]
    for marker in required_markers:
        assert marker in text

    question_count = len(re.findall(r"\bquestion:\s*\"", text))
    assert question_count >= 12


def test_interview_site_app_has_all_renderers() -> None:
    repo = Path(__file__).resolve().parents[2]
    app_path = repo / "docs" / "interview-site" / "assets" / "app.js"
    text = app_path.read_text(encoding="utf-8")

    required_renderers = [
        "function renderHome",
        "function renderArchitecture",
        "function renderMechanisms",
        "function renderInterview",
        "function renderResume",
        "function renderGraph",
        "function setupGraph",
    ]
    for marker in required_renderers:
        assert marker in text

    assert 'const page = document.body.dataset.page || "home";' in text
