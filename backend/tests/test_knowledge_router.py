"""Tests for the keyword section router."""

from __future__ import annotations

from typing import Any

from app.knowledge_router import select_sections


def user(content: str) -> dict[str, str]:
    """Build a user message."""
    return {"role": "user", "content": content}


def assistant(content: str) -> dict[str, str]:
    """Build an assistant message."""
    return {"role": "assistant", "content": content}


def test_razorflow_payments_selects_razorflow_ai() -> None:
    """The project name and the topic word both point at RazorFlow."""
    sections = select_sections([user("tell me about RazorFlow payments")])

    assert sections[:1] == ["razorflow_ai"]


def test_incf_pull_requests_selects_open_source() -> None:
    """INCF and PR wording point at the open-source deep dive."""
    sections = select_sections([user("what PRs did she make to INCF")])

    assert sections[:1] == ["open_source_incf"]


def test_general_question_returns_no_sections() -> None:
    """A general academic question stays on the core knowledge base."""
    assert select_sections([user("what is her CGPA")]) == []


def test_follow_up_uses_previous_user_message() -> None:
    """A short follow-up inherits the topic of the previous user message."""
    messages = [
        user("tell me about RazorFlow payments"),
        assistant("RazorFlow is an agentic commerce platform."),
        user("how did you test it?"),
    ]

    assert "razorflow_ai" in select_sections(messages)


def test_assistant_messages_are_never_used_for_routing() -> None:
    """Only a user message can trigger a section."""
    messages = [
        user("hello"),
        assistant("RazorFlow payments are handled by the razorflow_ai section."),
    ]

    assert select_sections(messages) == []


def test_no_user_messages_returns_no_sections() -> None:
    """A conversation with no user turn routes nowhere."""
    assert select_sections([]) == []
    assert select_sections([assistant("Tell me about RazorFlow payments.")]) == []


def test_latest_user_hit_takes_priority_over_previous() -> None:
    """A keyword hit in the latest message wins over the earlier topic."""
    messages = [
        user("tell me about Workchain"),
        user("tell me about RazorFlow payments"),
    ]

    sections = select_sections(messages)

    assert sections[0] == "razorflow_ai"
    assert "workchain" not in sections


def test_max_sections_caps_the_result() -> None:
    """At most max_sections ids are returned, best score first."""
    messages = [user("RazorFlow payments, a resume review and blockchain escrow")]

    sections = select_sections(messages, max_sections=2)

    assert len(sections) == 2
    assert sections[0] == "razorflow_ai"


def test_zero_max_sections_returns_empty() -> None:
    """A non-positive cap is a no-op."""
    assert select_sections([user("RazorFlow payments")], max_sections=0) == []


def test_keywords_do_not_match_inside_other_words() -> None:
    """Word-boundary matching avoids 'rag' in 'storage' and 'pr' in 'profile'."""
    messages: list[dict[str, Any]] = [
        user("How does her storage strategy look in her profile?")
    ]

    assert select_sections(messages) == []
