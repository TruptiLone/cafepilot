
from backend.services.recipe_service import calculate_ingredients


def test_catering_order():
    # Example catering order with 40 drinks
    order = [
        {
            "product_id": "COF003",
            "size": "regular",
            "quantity": 20,
            "milk": "whole"
        },
        {
            "product_id": "COF003",
            "size": "regular",
            "quantity": 10,
            "milk": "oat"
        },
        {
            "product_id": "COF002",
            "size": "regular",
            "quantity": 10
        }
    ]

    result = calculate_ingredients(order)

    assert result["COFFEE_BEANS"]["required_quantity"] == 720
    assert result["WHOLE_MILK"]["required_quantity"] == 4800
    assert result["OAT_MILK"]["required_quantity"] == 2400


def test_large_oat_latte_with_extra_shot():
    order = [
        {
            "product_id": "COF003",
            "size": "large",
            "quantity": 2,
            "milk": "oat",
            "extra_shots": 1
        }
    ]

    result = calculate_ingredients(order)

    # 2 drinks * (18g beans + 9g extra shot)
    assert result["COFFEE_BEANS"]["required_quantity"] == 54

    # 2 large oat lattes * 320 mL
    assert result["OAT_MILK"]["required_quantity"] == 640

    # No whole milk should be consumed
    assert "WHOLE_MILK" not in result


def test_invalid_product():
    import pytest

    with pytest.raises(ValueError, match="Unknown product"):
        calculate_ingredients([
            {
                "product_id": "INVALID",
                "size": "regular",
                "quantity": 1
            }
        ])
