
import vertexai
import streamlit as st
from typing import List, Optional
from dotenv import load_dotenv
from processors.google_helper import create_keyfile_dict
from google.oauth2 import service_account

from vertexai.generative_models import (
    GenerativeModel,
    Tool,
    FunctionDeclaration,
    HarmCategory, # Used to check security filters when gemini 2.5 stopped workign randomly
    HarmBlockThreshold, # Used to check security filters when gemini 2.5 stopped workign randomly
    ToolConfig,
)

# --- Configuration ---
load_dotenv()
LOCATION = "us-central1"            
MODEL_NAME="gemini-2.0-flash-001"


# --- 1. Define the Function for the AI to Call (The Tool) ---
# This function defines the structure of the desired JSON output.
# The docstrings and type hints are CRITICAL for the AI to understand its job.
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


def run_bol_extraction_agent(
    ocr_text: str, 
    project_id: str, 
    location: str, 
    creds: service_account.Credentials
) -> Optional[dict]:
    """
    Initializes the AI agent and runs the data extraction process.
    """
    creds = None
    project_id = None

    # --- 2. Initialize Vertex AI with service account credentials ---
    try:
        vertexai.init(project=project_id, location=location, credentials=creds)
        print("Agent: Vertex AI initialized successfully with provided credentials.")
    except Exception as e:
        print(f"Agent ERROR: Failed to initialize Vertex AI client: {e}")
        return None

    # --- 3. Set up the Model with the Tool ---
    # Declare the function as a tool the model can use
    extraction_tool = Tool(
        function_declarations=[
            FunctionDeclaration.from_func(submit_extracted_bol_data)
        ]
    )

    # This tells the model not to block content for any of the main safety categories.
    # This was used when gemini 2.5 pro randomly stopped working
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }

    # Initialize the Gemini model, telling it about the available tool
    model = GenerativeModel(MODEL_NAME, tools=[extraction_tool])

    tool_config = ToolConfig(
        function_calling_config=ToolConfig.FunctionCallingConfig(
            # Mode.ANY means the model MUST call one of the allowed functions.
            mode=ToolConfig.FunctionCallingConfig.Mode.ANY,
            # Specify the exact function name it is allowed to call.
            allowed_function_names=["submit_extracted_bol_data"],
        )
    )

    # --- 4. Craft the Master Prompt ---
    # This prompt tells the AI its role, its goal, and how to use the tool.
    prompt = f"""
    You are an expert data extraction agent specializing in logistics and shipping documents.
    Analyze the following document text and call the `submit_extracted_bol_data` function with the extracted values.

    **Extraction Rules:**
    - container_number: Find all unique 11-character alphanumeric container numbers (format: 4 letters, 7 numbers). Return a list of strings.
    - total_cartons: Find the total number of cartons or packages. Return an integer.
    - total_gross_mass_kg: Find the total gross weight in Kilograms (KGS). Return a float.
    - total_nett_mass_kg: Find the total net weight in Kilograms (KGS). Return a float.
    - Use null for any field that cannot be found.

    --- DOCUMENT TEXT TO ANALYZE ---
    {ocr_text}
    --- END OF DOCUMENT ---
    """

    print("\nSending prompt and document text to Gemini...")

    # --- 5. Generate Content and Trigger Function Call ---
    try:
        response = model.generate_content(
            prompt, 
            safety_settings=safety_settings,
            tool_config=tool_config 
        )

        # --- 7. Process the Response ---
        function_call = response.candidates[0].content.parts[0].function_call
        
        if function_call.name == "submit_extracted_bol_data":
            print(f"SUCCESS: Gemini successfully called '{function_call.name}'.\n")
            final_data = dict(function_call.args)
            return final_data
        else:
            print(f"ERROR: Unexpected function called: {function_call.name}")
            return None

    except Exception as e:
        # Catch all errors related to accessing the response structure
        print("ERROR: Failed to get a valid function call from the model.")
        print(f"Details: {e}")
        print("--- Full Response Object for Debugging ---")
        print(repr(response))
        print("------------------------------------------")
        return None