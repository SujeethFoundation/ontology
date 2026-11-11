"""Tests for how the chat route handles an agent run that fails without raising an exception."""
import asyncio
import json
import logging
from types import SimpleNamespace

import azure.functions as func
import pytest

import chat_blueprint as cb


def run_response(status="completed", text="hello", code=None, message=None, reason=None):
    return SimpleNamespace(
        status=status,
        output_text=text,
        error=SimpleNamespace(code=code, message=message) if code or message else None,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
    )


class FakeClient:
    """Returns the queued responses in order, one per call to responses.create."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = 0
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, input=None):
        self.calls += 1
        item = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def quiet_agent(monkeypatch):
    monkeypatch.setattr(cb.time, "sleep", lambda s: None)
    monkeypatch.setattr(cb, "_cooldown_until", 0.0)
    monkeypatch.setattr(cb, "BASE_URL", "https://agent.example.test")
    monkeypatch.setattr(cb.limiter, "allow", lambda ip: (True, 0))


def use(monkeypatch, client):
    monkeypatch.setattr(cb, "get_client", lambda: client)
    return client


def post_chat():
    body = json.dumps({"messages": [{"role": "user", "content": "Which circles are running right now?"}]}).encode()
    req = func.HttpRequest("POST", "/api/chat", headers={"origin": "https://www.sujeeth.org"}, body=body)
    return asyncio.run(cb.chat._function._func(req))


# ---- ask_agent ----

def test_a_completed_run_returns_its_text(monkeypatch):
    use(monkeypatch, FakeClient(run_response(text="  Week 3 covers resumes.  ")))
    assert cb.ask_agent([{"role": "user", "content": "x"}]) == "Week 3 covers resumes."


def test_a_failed_run_raises_with_the_service_error_code(monkeypatch):
    use(monkeypatch, FakeClient(run_response("failed", text="", code="tool_user_error", message="boom")))
    with pytest.raises(cb.AgentRunFailed) as caught:
        cb.ask_agent([{"role": "user", "content": "x"}])
    assert caught.value.status == "failed" and caught.value.code == "tool_user_error"


def test_an_incomplete_run_reports_its_reason(monkeypatch):
    use(monkeypatch, FakeClient(run_response("incomplete", text="", reason="max_output_tokens")))
    with pytest.raises(cb.AgentRunFailed) as caught:
        cb.ask_agent([{"role": "user", "content": "x"}])
    assert caught.value.status == "incomplete" and caught.value.code == "max_output_tokens"


def test_an_error_given_as_a_dict_is_read_too(monkeypatch):
    response = SimpleNamespace(status="failed", output_text="", error={"code": "server_error", "message": "m"})
    use(monkeypatch, FakeClient(response))
    with pytest.raises(cb.AgentRunFailed) as caught:
        cb.ask_agent([{"role": "user", "content": "x"}])
    assert caught.value.code == "server_error"


def test_a_failed_run_with_no_error_object_still_raises(monkeypatch):
    use(monkeypatch, FakeClient(SimpleNamespace(status="failed", output_text="")))
    with pytest.raises(cb.AgentRunFailed) as caught:
        cb.ask_agent([{"role": "user", "content": "x"}])
    assert caught.value.code is None


def test_a_rate_limit_reported_as_a_failed_run_is_retried_like_a_429(monkeypatch):
    client = use(monkeypatch, FakeClient(
        run_response("failed", text="", code="rate_limit_exceeded"),
        run_response(text="Here you go."),
    ))
    assert cb.ask_agent([{"role": "user", "content": "x"}]) == "Here you go."
    assert client.calls == 2


def test_other_failed_runs_are_not_retried(monkeypatch):
    client = use(monkeypatch, FakeClient(run_response("failed", text="", code="tool_user_error")))
    with pytest.raises(cb.AgentRunFailed):
        cb.ask_agent([{"role": "user", "content": "x"}])
    assert client.calls == 1


# ---- explain ----

def test_explain_names_the_code_and_never_the_message():
    exc = cb.AgentRunFailed("failed", "tool_user_error", "text that might hold anything")
    status, key, note = cb.explain(exc)
    assert (status, key) == (502, "unavailable")
    assert "tool_user_error" in note and "text that might hold" not in note


def test_explain_says_when_there_is_no_code():
    assert "no error code" in cb.explain(cb.AgentRunFailed("failed"))[2]


def test_explain_treats_a_reported_rate_limit_as_busy():
    assert cb.explain(cb.AgentRunFailed("failed", "rate_limit_exceeded"))[:2] == (503, "busy")


def test_explain_still_maps_the_older_errors():
    assert cb.explain(TimeoutError())[:2] == (504, "slow")
    assert cb.explain(RuntimeError("x"))[0] == 502


def test_a_code_cannot_inject_log_lines():
    exc = cb.AgentRunFailed("failed", "bad\nchat fake failed: ok\r")
    assert "\n" not in cb.explain(exc)[2] and "\r" not in cb.explain(exc)[2]


# ---- the route ----

def test_route_logs_the_code_when_the_run_fails(monkeypatch, caplog):
    use(monkeypatch, FakeClient(run_response("failed", text="", code="tool_user_error")))
    with caplog.at_level(logging.ERROR, logger="sujeeth-chat"):
        response = post_chat()
    assert response.status_code == 502
    lines = [r.getMessage() for r in caplog.records]
    assert any("failed: agent run failed (tool_user_error)" in line for line in lines)


def test_route_logs_an_empty_reply_instead_of_staying_silent(monkeypatch, caplog):
    use(monkeypatch, FakeClient(run_response("completed", text="")))
    with caplog.at_level(logging.ERROR, logger="sujeeth-chat"):
        response = post_chat()
    assert response.status_code == 502
    assert any("agent returned no text" in r.getMessage() for r in caplog.records)


def test_route_answers_normally_when_the_run_completes(monkeypatch):
    use(monkeypatch, FakeClient(run_response(text="Four circles are running.")))
    response = post_chat()
    assert response.status_code == 200
    assert json.loads(response.get_body())["reply"] == "Four circles are running."


def test_route_never_logs_the_visitor_message_or_the_service_message(monkeypatch, caplog):
    use(monkeypatch, FakeClient(run_response("failed", text="", code="tool_user_error", message="SECRET-SERVICE-TEXT")))
    with caplog.at_level(logging.DEBUG, logger="sujeeth-chat"):
        post_chat()
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "Which circles are running" not in joined and "SECRET-SERVICE-TEXT" not in joined
