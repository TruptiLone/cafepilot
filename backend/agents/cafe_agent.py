
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from backend.services.menu_service import get_menu
from backend.services.catering_service import evaluate_catering_order


# --------------------------------------------------
# Configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MODEL_NAME = "muse-spark-1.3"
MODEL_BASE_URL = "https://api.meta.ai/v1"


# --------------------------------------------------
# Agent instructions
# --------------------------------------------------

SYSTEM_PROMPT = """
You are CafePilot, an AI operations assistant for independent cafe owners.

Your job is to help cafe owners understand catering readiness,
ingredient requirements, and inventory shortages.

You have access to two tools:

1. get_menu
   Retrieves the current menu, including product names and IDs.

2. check_catering_readiness
   Calculates ingredient requirements and checks inventory.

IMPORTANT RULES:

- Always retrieve the current menu before evaluating a catering order.
- Never invent product names or IDs.
- Never ask the owner to supply internal product IDs.
- Match natural language to the menu.

Examples:
"oat milk latte" means Latte with oat milk.
"large vanilla latte" means Vanilla Latte, size large.

- Use check_catering_readiness for ingredient calculations.
- Never calculate inventory quantities yourself.
- Only report numerical results returned by tools.
- Ask for clarification if the product or quantity is genuinely unclear.
- If size is unspecified, regular is the default.
- If milk is unspecified, whole milk is the default.
- Never claim inventory has been reserved or deducted.
- Never claim a supplier order has been placed.
- All current inventory data is synthetic.
- A stock check is based on the stored inventory snapshot,
  not a guarantee of availability at a future pickup time.

Respond clearly and concisely.
"""


# --------------------------------------------------
# Tool definitions
# --------------------------------------------------

MENU_TOOL = {
    "type": "function",
    "function": {
        "name": "get_menu",
        "description": (
            "Retrieve the current CafePilot menu with product IDs, "
            "names, prices and descriptions."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False
        }
    }
}


CATERING_TOOL = {
    "type": "function",
    "function": {
        "name": "check_catering_readiness",
        "description": (
            "Calculate ingredient requirements and check available "
            "inventory for a catering order. This tool is read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "size": {
                                "type": "string",
                                "enum": ["regular", "large", "each"]
                            },
                            "quantity": {
                                "type": "integer",
                                "minimum": 1
                            },
                            "milk": {
                                "type": "string",
                                "enum": [
                                    "whole", "skim", "oat", "almond"
                                ]
                            },
                            "extra_shots": {
                                "type": "integer",
                                "minimum": 0
                            }
                        },
                        "required": [
                            "product_id",
                            "size",
                            "quantity"
                        ]
                    }
                }
            },
            "required": ["items"]
        }
    }
}


# --------------------------------------------------
# Meta API client
# --------------------------------------------------

def create_meta_client():
    """Initialize Meta's Model API client."""

    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("MODEL_API_KEY")

    if not api_key:
        raise RuntimeError("MODEL_API_KEY is missing from .env")

    return OpenAI(
        api_key=api_key,
        base_url=MODEL_BASE_URL,
        timeout=30.0,
        max_retries=1
    )


# --------------------------------------------------
# Tool execution
# --------------------------------------------------

def execute_tool(tool_name, arguments):
    """
    Execute one of the approved tools.

    All business calculations happen in Python,
    not inside the language model.
    """

    if not isinstance(arguments, dict):
        return {"error": "Tool arguments must be a JSON object"}

    # TOOL 1: Read the current menu
    if tool_name == "get_menu":
        if arguments:
            return {"error": "get_menu does not accept arguments"}

        return {"products": get_menu()}

    # TOOL 2: Check catering inventory readiness
    if tool_name == "check_catering_readiness":

        try:
            # Reuse our existing FastAPI request validation.
            from backend.main import CateringRequest

            validated = CateringRequest.model_validate(arguments)

            items = [
                item.model_dump()
                for item in validated.items
            ]

            # Verify product IDs against the current menu.
            menu_products = get_menu()

            valid_product_ids = {
                product["id"]
                for product in menu_products
            }

            for item in items:
                if item["product_id"] not in valid_product_ids:
                    return {
                        "error": (
                            f"Unknown product ID: {item['product_id']}"
                        )
                    }

            return evaluate_catering_order(items)

        except (ValueError, TypeError, ValidationError) as error:
            return {"error": str(error)}

    return {"error": f"Tool not allowed: {tool_name}"}


# --------------------------------------------------
# Agent conversation
# --------------------------------------------------

def chat_with_agent(user_message, client=None):
    """
    Process a natural-language request using Muse Spark.

    The model chooses tools, Python executes them,
    and the model explains the validated results.

    A mock client can be injected during testing.
    """

    if not user_message or not user_message.strip():
        raise ValueError("Message cannot be empty")

    if client is None:
        client = create_meta_client()

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        },
        {
            "role": "user",
            "content": user_message
        }
    ]

    tool_history = []

    # Retrieve current menu deterministically before asking
    # the model to map a natural-language product name.
    menu_result = execute_tool("get_menu", {})

    tool_history.append({
        "tool": "get_menu",
        "result": menu_result
    })

    # Supply the actual catalog as context rather than
    # requiring the owner to know internal product IDs.
    messages.append({
        "role": "system",
        "content": (
            "Current CafePilot menu retrieved from get_menu:\n"
            + json.dumps(menu_result)
            + "\nUse this catalog to resolve product IDs. "
              "If the request has enough details, call "
              "check_catering_readiness. "
              "Do not ask the user for a product ID."
        )
    })

    # Limit model/tool rounds to prevent infinite execution.
    for _ in range(5):

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=[CATERING_TOOL],
            tool_choice="auto"
        )

        assistant_message = response.choices[0].message

        tool_calls = assistant_message.tool_calls or []

        # The model has completed its response.
        if not tool_calls:
            return {
                "answer": assistant_message.content or "",
                "tool_history": tool_history
            }

        # Preserve the assistant tool-call message.
        messages.append(assistant_message)

        # Execute each requested tool.
        for tool_call in tool_calls:

            try:
                arguments = json.loads(
                    tool_call.function.arguments
                )

                result = execute_tool(
                    tool_call.function.name,
                    arguments
                )

            except json.JSONDecodeError:
                result = {
                    "error": "Invalid JSON in tool arguments"
                }

            tool_history.append({
                "tool": tool_call.function.name,
                "result": result
            })

            # Send real Python results back to the model.
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result)
            })

    return {
        "answer": (
            "I couldn't complete this request within the "
            "allowed tool-call steps. Please try again."
        ),
        "tool_history": tool_history
    }
