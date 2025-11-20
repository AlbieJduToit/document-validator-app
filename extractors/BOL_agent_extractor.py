import json
import streamlit as st
from typing import List, Optional

from openai import OpenAI

# --- Configuration ---
OPENAI_MODEL = "gpt-4.1-mini"  # or "gpt-4.1", etc.


# --- 1. Define the Function Schema (Tool) & Optional Local Impl ---

def submit_extracted_bol_data(
    container_number: Optional[List[str]] = None,
    total_cartons: Optional[int] = None,
    total_gross_mass_kg: Optional[float] = None,
    total_nett_mass_kg: Optional[float] = None,
):
    """
    Submits the extracted data from a Bill of Lading document.

    Args:
        container_number (Optional[List[str]]): A list of all unique container numbers found.
        total_cartons (Optional[int]): The total number of cartons or packages.
        total_gross_mass_kg (Optional[float]): The total gross weight in kilograms (KGS).
        total_nett_mass_kg (Optional[float]): The total nett (net) weight in kilograms (KG).
    
    Returns:
        A dictionary confirming the data that was received.
    """
    extracted_data = {
        "container_number": container_number,
        "total_cartons": total_cartons,
        "total_gross_mass_kg": total_gross_mass_kg,
        "total_nett_mass_kg": total_nett_mass_kg,
    }
    print("--- Python Function 'submit_extracted_bol_data' was successfully called! ---")
    return extracted_data


# OpenAI tool (function) schema corresponding to submit_extracted_bol_data
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "submit_extracted_bol_data",
            "description": "Submits the extracted data from a Bill of Lading document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "container_number": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "A list of all unique container numbers found.",
                    },
                    "total_cartons": {
                        "type": "integer",
                        "description": "The total number of cartons or packages.",
                    },
                    "total_gross_mass_kg": {
                        "type": "number",
                        "description": "The total gross weight in kilograms (KGS).",
                    },
                    "total_nett_mass_kg": {
                        "type": "number",
                        "description": "The total net weight in kilograms (KGS).",
                    },
                },
                "required": [],
            },
        },
    }
]


def run_bol_extraction_agent(
    ocr_text: str,
) -> Optional[dict]:
    """
    Initializes the AI agent (ChatGPT) and runs the data extraction process.
    """

    print("\n--- Inside run_bol_extraction_agent (OpenAI / ChatGPT version) ---")

    # --- Init OpenAI client ---
    # You can also rely on OPENAI_API_KEY env var instead of st.secrets if you prefer.
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

    # --- Prompt (role & instructions) ---
    system_prompt = (
        "You are an expert data extraction agent specializing in logistics and "
        "shipping documents. Your job is to carefully extract structured data "
        "from Bill of Lading (BOL) text."
    )

    user_prompt = f"""
Analyze the following document text and call the `submit_extracted_bol_data` function
with the extracted values.

Extraction Rules:
- container_number: Find all unique 11-character alphanumeric container numbers
  (format: 4 letters, 7 numbers). Return a list of strings.
- total_cartons: Find the total number of cartons or packages. Return an integer.
- total_gross_mass_kg: Find the total gross weight in Kilograms (KGS). Return a float.
- total_nett_mass_kg: Find the total net weight in Kilograms (KGS). Return a float.
- Use null for any field that cannot be found.

--- DOCUMENT TEXT TO ANALYZE ---
{ocr_text}
--- END OF DOCUMENT ---
    """.strip()

    print("\nSending prompt and document text to ChatGPT...")

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=TOOLS,
            # Force the model to call this specific function
            tool_choice={
                "type": "function",
                "function": {"name": "submit_extracted_bol_data"},
            },
        )

        message = response.choices[0].message

        if not message.tool_calls:
            print("ERROR: Model did not call any tool.")
            return None

        tool_call = message.tool_calls[0]
        func_name = tool_call.function.name
        args_json = tool_call.function.arguments

        if func_name != "submit_extracted_bol_data":
            print(f"ERROR: Unexpected function called: {func_name}")
            return None

        print(f"SUCCESS: ChatGPT successfully called '{func_name}'.\n")

        # Parse JSON arguments from the tool call
        final_data = json.loads(args_json)

        # Optional: actually invoke the local Python function as well
        submit_extracted_bol_data(**final_data)

        return final_data

    except Exception as e:
        print("ERROR: Failed to get a valid function call from the model.")
        print(f"Details: {e}")
        return None
