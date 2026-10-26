
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.agents.order_extractor import extract_order


def make_client(model_output):
    """Simulate a model response without calling Meta."""
    client = Mock()

    message = SimpleNamespace(
        content=json.dumps(model_output)
    )

    client.chat.completions.create.return_value = (
        SimpleNamespace(
            choices=[SimpleNamespace(message=message)]
        )
    )

    return client


def test_extract_complete_order():
    client = make_client({
        "status": "ready",
        "items": [{
            "product_id": "COF003",
            "size": "large",
            "quantity": 10,
            "milk": "oat",
            "extra_shots": 0
        }]
    })

    result = extract_order(
        "Can we prepare 10 large oat-milk lattes?",
        client
    )

    assert result["status"] == "ready"
    assert result["items"][0]["product_id"] == "COF003"
    assert result["items"][0]["quantity"] == 10
    assert result["items"][0]["milk"] == "oat"


def test_extract_missing_quantity():
    client = make_client({
        "status": "needs_clarification",
        "question": "How many lattes would you like?",
        "items": []
    })

    result = extract_order(
        "Can we prepare some lattes?",
        client
    )

    assert result["status"] == "needs_clarification"
    assert result["items"] == []


def test_reject_empty_ready_order():
    client = make_client({
        "status": "ready",
        "items": []
    })

    with pytest.raises(ValueError, match="cannot be empty"):
        extract_order("Prepare some lattes", client)

def test_extract_out_of_scope_request():
    client = make_client({
        "status": "out_of_scope",
        "items": []
    })

    result = extract_order("Hi, how are you?", client)

    assert result["status"] == "out_of_scope"
    assert result["items"] == []