
import pytest
from pydantic import ValidationError

from backend.services.order_validator import validate_order


def make_order(product_id="COF003", size="large",
               quantity=10, milk="oat", extra_shots=0):
    return {
        "status": "ready",
        "items": [{
            "product_id": product_id,
            "size": size,
            "quantity": quantity,
            "milk": milk,
            "extra_shots": extra_shots
        }]
    }


def test_valid_oat_latte():
    result = validate_order(make_order())

    assert result["status"] == "ready"
    assert result["items"][0]["product_id"] == "COF003"
    assert result["items"][0]["quantity"] == 10


def test_unknown_product():
    with pytest.raises(ValueError, match="Unknown product"):
        validate_order(make_order(product_id="INVALID"))


def test_negative_quantity():
    with pytest.raises(ValidationError):
        validate_order(make_order(quantity=-5))


def test_reject_boolean_quantity():
    # In Python, bool is a subclass of int, so strict checking matters.
    with pytest.raises(ValidationError):
        validate_order(make_order(quantity=True))


def test_invalid_size():
    with pytest.raises(ValueError, match="Unsupported size"):
        validate_order(make_order(size="each"))


def test_milk_substitution_on_americano():
    with pytest.raises(ValueError, match="Milk substitution"):
        validate_order(make_order(product_id="COF002"))


def test_extra_shot_on_muffin():
    with pytest.raises(ValueError, match="Extra espresso"):
        validate_order(make_order(
            product_id="BAK001",
            size="each",
            milk="whole",
            extra_shots=1
        ))


def test_valid_clarification():
    result = validate_order({
        "status": "needs_clarification",
        "question": "How many lattes would you like?",
        "items": []
    })

    assert result["status"] == "needs_clarification"
    assert result["items"] == []


def test_missing_clarification_question():
    with pytest.raises(ValueError, match="clarification question"):
        validate_order({
            "status": "needs_clarification",
            "items": []
        })

def test_out_of_scope_request():
    result = validate_order({
        "status": "out_of_scope",
        "items": []
    })

    assert result["status"] == "out_of_scope"
    assert result["items"] == []


def test_out_of_scope_rejects_order_items():
    with pytest.raises(ValueError, match="Out-of-scope"):
        validate_order({
            "status": "out_of_scope",
            "items": [{
                "product_id": "COF003",
                "size": "large",
                "quantity": 10
            }]
        })

