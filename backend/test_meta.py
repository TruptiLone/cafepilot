
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Load API key from project .env
project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")

api_key = os.getenv("MODEL_API_KEY")

if not api_key:
    raise ValueError("MODEL_API_KEY is missing from .env")

# Meta's Model API works with the OpenAI SDK
client = OpenAI(
    api_key=api_key,
    base_url="https://api.meta.ai/v1"
)

response = client.chat.completions.create(
    model="muse-spark-1.3",
    messages=[
        {
            "role": "user",
            "content": (
                "You are CafePilot, an assistant for "
                "independent cafe owners. "
                "Introduce yourself in two sentences."
            )
        }
    ]
)

print(response.choices[0].message.content)
