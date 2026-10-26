
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def get_menu():
    """Return the current cafe menu from JSON."""
    with open(DATA_DIR / "menu.json", encoding="utf-8") as file:
        data = json.load(file)

    return data["products"]


def find_product(product_name):
    """Find a menu product by its name."""
    products = get_menu()

    for product in products:
        if product["name"].casefold() == product_name.strip().casefold():
            return product

    return None
