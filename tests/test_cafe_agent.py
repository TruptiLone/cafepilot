
from unittest.mock import Mock, patch

import pytest

from backend.agents.cafe_agent import chat_with_agent


@patch("backend.agents.cafe_agent.extract_order")
def test_valid_order_runs_readiness_automatically(mock_extract):
    mock_extract.return_value = {
        "status": "ready",
        "items": [{
            "product_id": "COF003",
            "size": "large",
            "quantity": 10,
            "milk": "oat",
            "extra_shots": 0
        }]
    }

    result = chat_with_agent(
        "Can we prepare 10 large oat-milk lattes?",
        client=Mock()
    )

    assert result["status"] == "shortage"
    assert len(result["tool_history"]) == 1

    tool = result["tool_history"][0]

    assert tool["tool"] == "check_catering_readiness"
    assert tool["executed_by"] == "python_orchestrator"

    inventory = {
        item["ingredient_id"]: item
        for item in tool["result"]["inventory_report"]["items"]
    }

    assert inventory["OAT_MILK"]["required_quantity"] == 3200
    assert inventory["OAT_MILK"]["shortage_quantity"] == 1200
    assert "Oat Milk:" in result["answer"]
    assert "shortage" in result["answer"]


@patch("backend.agents.cafe_agent.extract_order")
def test_clarification_does_not_calculate(mock_extract):
    mock_extract.return_value = {
        "status": "needs_clarification",
        "question": "How many lattes would you like?",
        "items": []
    }

    with patch(
        "backend.agents.cafe_agent.evaluate_catering_order"
    ) as mock_evaluate:

        result = chat_with_agent(
            "Can we prepare some lattes?",
            client=Mock()
        )

        assert result["status"] == "needs_clarification"
        assert result["tool_history"] == []
        mock_evaluate.assert_not_called()


@patch("backend.agents.cafe_agent.extract_order")
def test_invalid_product_never_runs_readiness(mock_extract):
    mock_extract.return_value = {
        "status": "ready",
        "items": [{
            "product_id": "FAKE_PRODUCT",
            "size": "regular",
            "quantity": 10
        }]
    }

    with patch(
        "backend.agents.cafe_agent.evaluate_catering_order"
    ) as mock_evaluate:

        result = chat_with_agent(
            "10 mystery drinks",
            client=Mock()
        )

        assert result["status"] == "validation_failed"
        mock_evaluate.assert_not_called()


@patch("backend.agents.cafe_agent.extract_order")
def test_invalid_model_output_is_handled(mock_extract):
    mock_extract.side_effect = ValueError("Invalid JSON")

    result = chat_with_agent(
        "10 lattes",
        client=Mock()
    )

    assert result["status"] == "extraction_failed"
    assert result["tool_history"] == []


@patch("backend.agents.cafe_agent.extract_order")
def test_greeting_does_not_execute_tools(mock_extract):
    mock_extract.return_value = {
        "status": "out_of_scope",
        "items": []
    }

    with patch(
        "backend.agents.cafe_agent.evaluate_catering_order"
    ) as mock_evaluate:

        result = chat_with_agent(
            "Hi, how are you?",
            client=Mock()
        )

        assert result["status"] == "out_of_scope"
        assert "CaféPilot" in result["answer"]
        assert result["tool_history"] == []

        mock_evaluate.assert_not_called()



def test_agent_rejects_empty_message():
    with pytest.raises(ValueError, match="empty"):
        chat_with_agent("   ", client=Mock())
