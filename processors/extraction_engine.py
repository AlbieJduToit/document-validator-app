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
from extractors.PL_extractor import extract_pl_data
from extractors.PPECB_extractor import extract_ppecb_data
from typing import Dict, Any, Optional
import streamlit as st
from google.oauth2 import service_account


load_dotenv()

project_id = st.secrets["app_config"]["project_id"]
location = st.secrets["app_config"]["location"]
form_processor_id = st.secrets["app_config"]["form_processor_id"]
layout_processor_id = st.secrets["app_config"]["layout_processor_id"]


def run_extraction_for_document(
    doc_type_key: str,
    file_bytes: bytes,
    project_id: str,
    location: str,
    form_processor_id: str,
    layout_processor_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Selects and runs the correct extraction workflow based on the document type.
    This is the single entry point for the Streamlit UI.
    """
    print(f"[ENGINE] Received request to extract document type: '{doc_type_key}'")
    
    if doc_type_key == "commercial_invoice":
        document_object = process_document_sample(
            project_id=project_id,
            location=location,
            processor_id=form_processor_id, 
            content_bytes=file_bytes,
            mime_type="application/pdf"
        )
        return extract_invoice_data(document_object) 

    elif doc_type_key == "bill_of_lading":
        document_object = process_document_sample(
            project_id=project_id,
            location=location,
            processor_id=form_processor_id, 
            content_bytes=file_bytes,
            mime_type="application/pdf"
        )
        initial_extracted = extract_bol_data(document_object)
        agent_document = process_document_sample(
            project_id=project_id,
            location=location,
            processor_id=layout_processor_id,
            content_bytes=file_bytes,  
            mime_type="application/pdf"
        )
        text_doc = build_text_from_raw_layout(agent_document)
        agent_extraction = run_bol_extraction_agent(
            ocr_text=text_doc,
            project_id=project_id
        )
        final_result = consolidate_extractions(initial_extracted, agent_extraction)
        return final_result 

    elif doc_type_key == "phyto_certificate":
        cleaned_pages_as_bytes = preprocess_pdf_for_ocr(file_bytes, threshold=100)
        first_cleaned_page_bytes = cleaned_pages_as_bytes[0]
        document_object = process_cleaned_image_bytes(
            project_id=project_id,
            location=location,
            processor_id=form_processor_id, 
            image_bytes=first_cleaned_page_bytes,
            mime_type ='image/png'
        )
        return extract_phyto_data(document_object)
    
    elif doc_type_key == "ppecb":
        document_object = process_document_sample(
            project_id=project_id,
            location=location,
            processor_id=form_processor_id,
            content_bytes=file_bytes,
            mime_type="application/pdf"
        )
        return extract_ppecb_data(document_object)
    
    elif doc_type_key == "eur1":
        document_object = process_document_sample(
            project_id=project_id,
            location=location,
            processor_id=form_processor_id,
            content_bytes=file_bytes,
            mime_type="application/pdf"
        )
        return extract_eur1_data(document_object)
    
    elif doc_type_key == "certificate_of_origin":
        document_object = process_document_sample(
            project_id=project_id,
            location=location,
            processor_id=form_processor_id,
            content_bytes=file_bytes,
            mime_type="application/pdf"
        )
        return extract_coo_data(document_object)
    
    elif doc_type_key == "packing_list":
        document_object = process_document_sample(
            project_id=project_id,
            location=location,
            processor_id=form_processor_id,
            content_bytes=file_bytes,
            mime_type="application/pdf"
        )
        return extract_pl_data(document_object)

    else:
        print(f"[ENGINE] Error: No defined extraction workflow for document type '{doc_type_key}'")
        return None