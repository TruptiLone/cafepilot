
import json
from types import SimpleNamespace
from unittest.mock import Mock

from backend.agents.cafe_agent import chat_with_agent


def mock_response(content=None, tool_calls=None):
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)]
    )


def test_agent_calls_catering_tool():
    # Simulate Muse Spark asking to check 10 oat lattes.
    arguments = {
        "items": [{
            "product_id": "COF003",
            "size": "large",
            "quantity": 10,
            "milk": "oat"
        }]
    }

    tool_call = SimpleNamespace(
        id="call_001",
        function=SimpleNamespace(
            name="check_catering_readiness",
            arguments=json.dumps(arguments)
        )
    )

    fake_client = Mock()

    # First response: model requests our tool.
    # Second response: model provides an explanation.
    fake_client.chat.completions.create.side_effect = [
        mock_response(tool_calls=[tool_call]),
        mock_response(
            content="There is a 1,200 mL oat-milk shortage."
        )
    ]

    result = chat_with_agent(
        "Can we prepare 10 large oat-milk lattes?",
        client=fake_client
    )

    assert len(result["tool_history"]) == 2

    assert result["tool_history"][0]["tool"] == "get_menu"
    assert result["tool_history"][1]["tool"] == "check_catering_readiness"

    tool_result = result["tool_history"][1]["result"]

    assert tool_result["status"] == "shortage"

    items = {
        item["ingredient_id"]: item
        for item in tool_result["inventory_report"]["items"]
    }

    assert items["OAT_MILK"]["required_quantity"] == 3200
    assert items["OAT_MILK"]["shortage_quantity"] == 1200

    assert "1,200 mL" in result["answer"]

    # Verify two calls to the mocked model.
    assert fake_client.chat.completions.create.call_count == 2

    # The second request must include the tool's actual result.
    second_call = (
        fake_client.chat.completions.create.call_args_list[1]
    )
    messages = second_call.kwargs["messages"]

    tool_messages = [
        message for message in messages
        if isinstance(message, dict)
        and message.get("role") == "tool"
    ]

    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_001"


def test_agent_can_ask_for_clarification():
    fake_client = Mock()

    fake_client.chat.completions.create.return_value = (
        mock_response(
            content="How many lattes would you like?"
        )
    )

    result = chat_with_agent(
        "Can we prepare some lattes?",
        client=fake_client
    )

    assert len(result["tool_history"]) == 1
    assert result["tool_history"][0]["tool"] == "get_menu"
    assert "How many" in result["answer"]
    


def test_agent_rejects_empty_message():
    import pytest

    with pytest.raises(ValueError, match="empty"):
        chat_with_agent("   ", client=Mock())
