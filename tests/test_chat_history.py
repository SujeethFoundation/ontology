import chat_blueprint as cb


def convo(*pairs):
    return [{"role": r, "content": c} for r, c in pairs]


def test_a_long_assistant_reply_no_longer_blocks_the_next_turn():
    raw = convo(("user", "Review my resume"), ("assistant", "x" * 9000), ("user", "How do I volunteer"))
    messages, problem = cb.parse_messages(raw)
    assert problem is None
    assert messages[-1]["content"] == "How do I volunteer"
    assert len(messages[1]["content"]) <= cb.MAX_MESSAGE_CHARS + 20


def test_a_visitors_own_message_is_still_limited():
    raw = convo(("user", "x" * (cb.MAX_MESSAGE_CHARS + 1)))
    messages, problem = cb.parse_messages(raw)
    assert messages is None and "characters" in problem


def test_a_long_conversation_is_shortened_oldest_first_and_keeps_the_newest_message_whole(monkeypatch):
    monkeypatch.setattr(cb, "MAX_HISTORY_CHARS", 3000)
    monkeypatch.setattr(cb, "OLD_MESSAGE_CHARS", 200)
    raw = []
    for i in range(5):
        raw += convo(("user", f"question {i}"), ("assistant", f"answer {i} " + "y" * 1500))
    raw += convo(("user", "z" * 1900))
    messages, problem = cb.parse_messages(raw[-cb.MAX_HISTORY_MESSAGES:])
    assert problem is None
    assert messages[-1]["content"] == "z" * 1900
    assert sum(len(m["content"]) for m in messages) <= 3000
    assert messages[0]["role"] == "user"


def test_the_oldest_turns_are_dropped_when_shortening_is_not_enough(monkeypatch):
    monkeypatch.setattr(cb, "MAX_HISTORY_CHARS", 1000)
    monkeypatch.setattr(cb, "OLD_MESSAGE_CHARS", 300)
    raw = convo(("user", "a" * 400), ("assistant", "b" * 400), ("user", "c" * 400), ("assistant", "d" * 400), ("user", "e" * 500))
    messages, problem = cb.parse_messages(raw)
    assert problem is None
    assert messages[0]["role"] == "user" and messages[-1]["content"] == "e" * 500
    assert sum(len(m["content"]) for m in messages) <= 1000


# ---- summarizing ----
import asyncio
import json
from types import SimpleNamespace

import azure.functions as func


class FakeModel:
    """Answers the agent's call with one text and the summary call with another; records what each was sent."""

    def __init__(self, reply="Here is the answer.", notes="NOTES", fail_summary=False):
        self.reply, self.notes, self.fail_summary, self.sent = reply, notes, fail_summary, []
        self.responses = SimpleNamespace(create=self._create)

    def with_options(self, **kw):
        return self

    def _create(self, input=None, **kw):
        self.sent.append(input)
        is_summary = input[0]["content"].startswith(cb.SUMMARY_INSTRUCTIONS[:30])
        if is_summary and self.fail_summary:
            raise RuntimeError("boom")
        return SimpleNamespace(status="completed", output_text=self.notes if is_summary else self.reply,
                               error=None, incomplete_details=None)


def post(messages, summary=None):
    payload = {"messages": messages, **({"summary": summary} if summary else {})}
    req = func.HttpRequest("POST", "/api/chat", headers={"origin": "https://www.sujeeth.org"},
                           body=json.dumps(payload).encode())
    return asyncio.run(cb.chat._function._func(req))


def setup(monkeypatch, model):
    monkeypatch.setattr(cb, "BASE_URL", "https://agent.example.test")
    monkeypatch.setattr(cb.limiter, "allow", lambda ip: (True, 0))
    monkeypatch.setattr(cb, "get_client", lambda: model)
    monkeypatch.setattr(cb, "SUMMARY_BASE_URL", "")


def long_convo():
    raw = []
    for i in range(3):
        raw += convo(("user", f"question {i} " + "q" * 1800), ("assistant", f"answer {i} " + "a" * 2000))
    return raw + convo(("user", "How do I volunteer"))


def test_a_short_chat_gets_no_summary(monkeypatch):
    model = FakeModel()
    setup(monkeypatch, model)
    body = json.loads(post(convo(("user", "hi"))).get_body())
    assert body == {"reply": "Here is the answer."}
    assert len(model.sent) == 1


def test_a_long_chat_returns_notes_and_keep_last(monkeypatch):
    model = FakeModel()
    setup(monkeypatch, model)
    monkeypatch.setattr(cb, "SUMMARIZE_AFTER_CHARS", 5000)
    body = json.loads(post(long_convo()).get_body())
    assert body["reply"] == "Here is the answer."
    assert body["summary"] == "NOTES"
    assert body["keep_last"] == cb.KEEP_RECENT_MESSAGES
    assert len(model.sent) == 2  # the answer, then the summary


def test_notes_from_the_page_open_the_conversation_the_agent_sees(monkeypatch):
    model = FakeModel()
    setup(monkeypatch, model)
    post(convo(("user", "How do I volunteer")), summary="Visitor is a Mizzou junior.")
    sent = model.sent[0]
    assert "Mizzou junior" in sent[0]["content"] and sent[0]["role"] == "user"
    assert sent[1]["role"] == "assistant" and sent[-1]["content"] == "How do I volunteer"


def test_a_failed_summary_still_returns_the_answer(monkeypatch):
    model = FakeModel(fail_summary=True)
    setup(monkeypatch, model)
    monkeypatch.setattr(cb, "SUMMARIZE_AFTER_CHARS", 5000)
    response = post(long_convo())
    assert response.status_code == 200
    assert json.loads(response.get_body()) == {"reply": "Here is the answer."}


def test_the_page_can_not_send_oversized_notes(monkeypatch):
    model = FakeModel()
    setup(monkeypatch, model)
    post(convo(("user", "hi")), summary="n" * 50000)
    assert len(model.sent[0][0]["content"]) < cb.SUMMARY_MAX_CHARS + 200
