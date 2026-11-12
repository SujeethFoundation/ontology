"""Tests for choosing the answer out of an agent run that wrote more than one message."""
from types import SimpleNamespace

import chat_blueprint as cb


def message(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def test_text_written_before_a_tool_call_is_not_part_of_the_answer():
    response = SimpleNamespace(
        output=[
            message("I'm checking the Foundation's weekly curriculum so I can give you the agenda accurately."),
            {"type": "mcp_call"},
            message("Week 3 covers interviews."),
        ],
        output_text="I'm checking the Foundation's weekly curriculum so I can give you the agenda accurately."
                    "Week 3 covers interviews.",
    )
    assert cb.final_answer_text(response) == "Week 3 covers interviews."


def test_a_single_message_is_returned_trimmed():
    response = SimpleNamespace(output=[message("  Week 3 covers resumes.  ")], output_text="  Week 3 covers resumes.  ")
    assert cb.final_answer_text(response) == "Week 3 covers resumes."


def test_a_message_split_into_parts_is_joined():
    item = {"type": "message", "content": [{"type": "output_text", "text": "Week 3 "},
                                           {"type": "output_text", "text": "covers resumes."}]}
    assert cb.final_answer_text(SimpleNamespace(output=[item], output_text="")) == "Week 3 covers resumes."


def test_objects_with_attributes_are_read_like_dicts():
    item = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="Week 3 covers resumes.")])
    assert cb.final_answer_text(SimpleNamespace(output=[item], output_text="")) == "Week 3 covers resumes."


def test_a_last_message_with_no_text_falls_back_to_the_one_before():
    response = SimpleNamespace(output=[message("Week 3 covers resumes."), message("   ")], output_text="")
    assert cb.final_answer_text(response) == "Week 3 covers resumes."


def test_a_response_with_no_output_list_uses_output_text():
    assert cb.final_answer_text(SimpleNamespace(output_text="  Week 3 covers resumes.  ")) == "Week 3 covers resumes."


def test_no_messages_and_no_text_returns_empty():
    assert cb.final_answer_text(SimpleNamespace(output=[{"type": "mcp_call"}], output_text="")) == ""
