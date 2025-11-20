#from processors.google_helper import write_to_tempfile
from processors.parsers import process_document_sample, process_cleaned_image_bytes
from dotenv import load_dotenv
from extractors.CI_extractor import extract_invoice_data
from extractors.PL_extractor import extract_pl_data
from extractors.phyto_extractor import extract_phyto_data
from extractors.COO_extractor import extract_coo_data
from extractors.EUR1_extractor import extract_eur1_data
from extractors.BOL_extractor import extract_bol_data
from processors.pdf_pre_processor import preprocess_pdf_for_ocr
from processors.json_formatter import build_text_from_raw_layout, consolidate_extractions
from extractors.BOL_agent_extractor import run_bol_extraction_agent
from extractors.PPECB_extractor import extract_ppecb_data
import os
from google.protobuf import json_format
from google.cloud.documentai_v1.types import Document
import json
#from extractors.PL_extractor import get_form_key_value_pairs
import logging
import sys

load_dotenv()

project_id = os.getenv("GOOGLE_PROJECT_ID")
location = os.getenv("GOOGLE_LOCATION")
form_processor_id = os.getenv("GOOGLE_FORM_PROCESSOR_ID")
layout_processor_id = os.getenv("GOOGLE_LAYOUT_PROCESSOR_ID")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)

local_pdf = "Draft SWB (12).pdf"

logger.info(f"Reading file bytes from: {local_pdf}")
with open(local_pdf, "rb") as f:
    pdf_bytes = f.read()
logger.info(f"Read {len(pdf_bytes)} bytes.")

agent_document = process_document_sample(
    project_id=project_id,
    location=location,
    processor_id=form_processor_id,
    content_bytes=pdf_bytes,  
    mime_type="application/pdf"
)

extracted = extract_bol_data(agent_document)
extracted_agent = run_bol_extraction_agent(agent_document, project_id)

print(json.dumps(extracted, indent=2))
print(json.dumps(extracted_agent, indent=2))