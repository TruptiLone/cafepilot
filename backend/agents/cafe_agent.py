
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from backend.agents.order_extractor import extract_order
from backend.services.order_validator import validate_order
from backend.services.catering_service import evaluate_catering_order


PROJECT_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


def create_meta_client():
    """Create a client for the Meta Model API."""
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("MODEL_API_KEY")
    if not api_key:
        raise RuntimeError("MODEL_API_KEY is missing")

    return OpenAI(
        api_key=api_key,
        base_url="https://api.meta.ai/v1",
        timeout=30.0,
        max_retries=1,
    )


def format_quantity(value):
    """Display whole numbers without unnecessary decimals."""
    return f"{value:g}"


def build_readiness_summary(report):
    """
    Generate an explanation using verified inventory calculations.

    This function does not call an LLM or invent quantities.
    """

    status = report["status"]

    if status == "insufficient_data":
        introduction = (
            "I cannot determine readiness because some "
            "inventory information is missing or invalid."
        )
    elif status == "shortage":
        introduction = (
            "The order has ingredient shortages "
            "against the current inventory snapshot."
        )
    elif status == "sufficient":
        introduction = (
            "The listed ingredients are sufficient "
            "against the current inventory snapshot."
        )
    else:
        raise ValueError(f"Unexpected readiness status: {status}")

    lines = [introduction]

    for item in report["inventory_report"]["items"]:
        ingredient = item.get("name", item["ingredient_id"])
        required = item["required_quantity"]
        unit = item["unit"]

        if item["status"] == "unknown":
            lines.append(
                f"{ingredient}: "
                f"{format_quantity(required)} {unit} required; "
                "availability unknown."
            )
        else:
            available = item["available_quantity"]
            shortage = item["shortage_quantity"]

            lines.append(
                f"{ingredient}: "
                f"{format_quantity(required)} {unit} required, "
                f"{format_quantity(available)} {unit} available, "
                f"{format_quantity(shortage)} {unit} shortage."
            )

    lines.append(
        "This assessment uses synthetic inventory data "
        "and does not reserve or deduct stock."
    )

    return "\n".join(lines)


def chat_with_agent(user_message, client=None):
    """
    Run the controlled CafePilot agent workflow.

    Steps:
    1. Extract the order using Muse Spark.
    2. Validate products, quantities and modifiers in Python.
    3. Automatically calculate catering readiness.
    4. Generate an explanation from the calculated results.

    No inventory is modified and no supplier orders are placed.
    """

    if not isinstance(user_message, str) or not user_message.strip():
        raise ValueError("Message cannot be empty")

    if client is None:
        client = create_meta_client()

    # STEP 1: Extract the owner's request with Muse Spark.
    try:
        extracted = extract_order(user_message, client)

    except (ValueError, KeyError, IndexError, TypeError) as error:
        logger.warning("Order extraction failed: %s", error)

        return {
            "status": "extraction_failed",
            "answer": (
                "I couldn't reliably interpret your request. "
                "Please rephrase it."
            ),
            "tool_history": [],
        }

    # STEP 2: Validate the extracted data using Python.
    try:
        validated = validate_order(extracted)

    except (ValidationError, ValueError, TypeError) as error:
        logger.warning("Order validation failed: %s", error)

        return {
            "status": "validation_failed",
            "answer": (
                "I couldn't validate the requested products "
                "or options. Please check the order details."
            ),
            "tool_history": [],
        }

    # Missing order information requires clarification.
    # Do not calculate inventory until the request is complete.
    if validated["status"] == "needs_clarification":
        return {
            "status": "needs_clarification",
            "answer": validated["question"],
            "tool_history": [],
        }
    # Greetings and unsupported requests must not execute tools.
    if validated["status"] == "out_of_scope":
        return {
            "status": "out_of_scope",
            "answer": (
                "Hi! I'm CaféPilot, your café operations assistant. "
                "I can help check ingredient availability for catering orders. "
                "What would you like to prepare?"
                ),
            "tool_history": [],
        }

    # STEP 3: Python automatically executes the catering calculation.
    try:
        report = evaluate_catering_order(validated["items"])
        answer = build_readiness_summary(report)

    except (ValueError, KeyError, TypeError, OSError) as error:
        logger.exception("Catering readiness calculation failed")

        return {
            "status": "calculation_failed",
            "answer": (
                "I couldn't complete the inventory assessment. "
                "Please try again or check the inventory data."
            ),
            "tool_history": [],
        }

    # Record the tool execution for transparency and debugging.
    tool_history = [
        {
            "tool": "check_catering_readiness",
            "executed_by": "python_orchestrator",
            "result": report,
        }
    ]

    # STEP 4: Return the validated result and explanation.
    return {
        "status": report["status"],
        "answer": answer,
        "validated_order": validated["items"],
        "tool_history": tool_history,
    }
