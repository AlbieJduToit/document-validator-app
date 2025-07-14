import logging
from typing import Optional
from google.api_core.client_options import ClientOptions
from google.cloud import documentai
#from processors.google_helper import create_keyfile_dict
from google.oauth2 import service_account
from google.cloud.documentai_v1.types import Document
import streamlit as st



logger = logging.getLogger(__name__)

def get_google_creds():
    """Creates Google credentials from Streamlit's secrets."""
    # Access the [google_credentials] section of your secrets.toml
    creds_dict = st.secrets["google_credentials"] 
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, 
        scopes=['https://www.googleapis.com/auth/cloud-platform']
    )
    return creds


def process_cleaned_image_bytes(
    project_id: str,
    location: str,
    processor_id: str,
    image_bytes: bytes,  # Takes bytes directly
    mime_type: str = 'image/png', # Default to PNG as that's what our pre-processor outputs
    processor_version_id: Optional[str] = None,
) -> Optional[Document]:
    logger.info("Starting document processing for pre-cleaned image bytes.")

    try:
        opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
        creds = get_google_creds()
        client = documentai.DocumentProcessorServiceClient(credentials=creds, client_options=opts)

        if processor_version_id and processor_version_id.strip():
            name = client.processor_version_path(project_id, location, processor_id, processor_version_id)
        else:
            name = client.processor_path(project_id, location, processor_id)
        
        logger.debug(f"Using processor resource name: {name}")

        # The key difference: we use the bytes and mime_type passed directly to the function
        raw_document = documentai.RawDocument(content=image_bytes, mime_type=mime_type)

        request = documentai.ProcessRequest(
            name=name,
            raw_document=raw_document,
        )

        result = client.process_document(request=request)

    except Exception as e:
        logger.error(f"Error during cleaned image processing: {e}", exc_info=True)
        return None

    document = result.document
    logger.info("Document processing completed for cleaned image bytes.")
    return document


      
def process_document_sample(
    project_id: str,
    location: str,
    processor_id: str,
    content_bytes: bytes, 
    mime_type: str = "application/pdf"
) -> Optional[documentai.Document]:
    """
    Processes a document using the Document AI Layout Parser.
    This version takes bytes directly and makes a robust request.
    """
    logger.info("Starting robust document processing...")

    try:
        # Use a longer timeout to handle large documents
        client_options = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
        creds = get_google_creds()
        client = documentai.DocumentProcessorServiceClient(credentials=creds, client_options=client_options)

        name = client.processor_path(project_id, location, processor_id)
        raw_document = documentai.RawDocument(content=content_bytes, mime_type=mime_type)

        request = documentai.ProcessRequest(
            name=name,
            raw_document=raw_document,
        )

        logger.info(f"Sending request to processor: {name}")
        result = client.process_document(request=request, timeout=120.0)
        logger.info("Document processing completed successfully.")

        # The returned object should now be complete.
        return result.document

    except Exception as e:
        logger.error(f"An exception occurred during Document AI processing: {e}", exc_info=True)
        return None

    


