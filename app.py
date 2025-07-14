import streamlit as st
import json
from dotenv import load_dotenv
from typing import Dict, Any
import re
import pandas as pd
import base64
import io
from pypdf import PdfReader, PdfWriter
from processors.extraction_engine import run_extraction_for_document
from processors.validator import (
    validate_documents, ValidationStatus, 
    MULTI_LINE_FIELDS, CONTAINER_FIELDS, SIMPLE_TEXT_FIELDS, 
    INTEGER_FIELDS, FLOAT_FIELDS, CURRENCY_FIELDS, PARTIAL_MATCH_FIELDS
)

def check_password():
    """Returns `True` if the user has entered the correct password."""
    def password_entered():
        """Checks whether a password entered by the user is correct."""
        # Access the password from secrets.toml to compare.
        if st.session_state["password"] == st.secrets["app_config"]["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't store the password.
        else:
            st.session_state["password_correct"] = False

    # Return `True` if the user has already entered the correct password.
    if st.session_state.get("password_correct", False):
        return True

    # Show the password input field.
    st.text_input(
        "Password", type="password", on_change=password_entered, key="password"
    )
    if "password_correct" in st.session_state and not st.session_state.password_correct:
        st.error("😕 Password incorrect. Please try again.")
    
    # Don't render the rest of the app if the password is not correct.
    return False

if check_password():

    # --- Page Setup and App Configuration ---
    st.set_page_config(
        page_title="Shipment Dossier Validator",
        page_icon="🚢",
        layout="wide"
    )

    # --- ADDED: CUSTOM CSS TO REDUCE TOP PADDING ---
    st.markdown("""
        <style>
            /* This reduces the top padding of the whole page */
            .block-container {
                padding-top: 1rem;
            }
            
            /* This is the new, targeted style for the source of truth container.
            It targets the first container, inside the first column.
            WARNING: This is layout-dependent. */
            div[data-testid="stHorizontalBlock"] > div:nth-child(1) > div[data-testid="stVerticalBlock"] > div:nth-child(1) > div[data-testid="stVerticalBlock"] {
                background-color: rgba(4, 118, 208, 0.15);
                border-radius: 0.5rem;
                padding: 1rem;
            }
        </style>
    """, unsafe_allow_html=True)

    # Load configurations from environment variables
    load_dotenv()
    PROJECT_ID = st.secrets["app_config"]["project_id"]
    LOCATION = st.secrets["app_config"]["location"]
    FORM_PROCESSOR_ID = st.secrets["app_config"]["form_processor_id"]
    LAYOUT_PROCESSOR_ID = st.secrets["app_config"]["layout_processor_id"]


    # --- HELPER FUNCTIONS FOR UI DISPLAY ---
    def format_container_numbers_for_display(value: Any) -> str:
        """
        Takes a list or a string of container numbers and formats it
        as a clean, multi-line string for display in Streamlit.
        """
        if not value:
            return ""
        if isinstance(value, list):
            return "\n".join(value)
        if isinstance(value, str):
            numbers = re.split(r'[\s,]+', value)
            cleaned_numbers = [num.strip() for num in numbers if num.strip()]
            return "\n".join(cleaned_numbers)
        return str(value)

    def format_numeric_for_display(value: Any, field_type: str) -> str:
        """
        Takes a raw value (string or number) and formats it beautifully for display.
        """
        if value is None or str(value).strip() == '':
            return ""
        try:
            numeric_value = float(str(value).replace(',', ''))
            if field_type == 'int':
                return f"{int(numeric_value):,}"
            elif field_type == 'float':
                return f"{numeric_value:,.2f}"
            elif field_type == 'currency':
                return f"${numeric_value:,.2f}"
        except (ValueError, TypeError):
            return str(value)
        return str(value)

    def get_image_as_base64(file):
        with open(file, "rb") as f:
            data = f.read()
        return base64.b64encode(data).decode()

    def trim_pdf_to_max_pages(file_bytes: bytes, max_pages: int) -> tuple[bytes, bool]:
        """
        Trims a PDF to a maximum number of pages.

        Args:
            file_bytes: The original PDF file content as bytes.
            max_pages: The maximum number of pages to keep.

        Returns:
            A tuple containing:
            - The new PDF file content as bytes (trimmed if necessary).
            - A boolean indicating if the file was actually trimmed.
        """
        try:
            pdf_stream = io.BytesIO(file_bytes)
            reader = PdfReader(pdf_stream)
            
            if len(reader.pages) <= max_pages:
                return file_bytes, False # No trimming needed

            writer = PdfWriter()
            for i in range(min(len(reader.pages), max_pages)):
                writer.add_page(reader.pages[i])

            output_stream = io.BytesIO()
            writer.write(output_stream)
            
            return output_stream.getvalue(), True
        except Exception as e:
            # If any error occurs during PDF processing, return the original bytes
            st.warning(f"Could not process PDF for trimming: {e}. Sending original file.")
            return file_bytes, False
        
    def validate_banking_details(ci_data: Dict[str, Any]) -> Dict[str, str]:
        """
        Performs an internal consistency check on the Commercial Invoice to see
        if the banking details match the currency of the total value.
        """
        # Get the raw text, defaulting to empty strings to prevent errors
        banking_details_text = ci_data.get("banking_details", "")
        total_value_text = str(ci_data.get("total_value", ""))

        # Normalize banking details for keyword search
        banking_details_lower = banking_details_text.lower()

        # 1. Determine the expected currency from the banking details
        expected_currency = None
        if "euro" in banking_details_lower:
            expected_currency = "EUR"
        elif "usd" in banking_details_lower:
            expected_currency = "USD"
        elif "uk" in banking_details_lower or "gbp" in banking_details_lower:
            # Check for 'gbp' as well for robustness
            expected_currency = "GBP"
        
        # If no keywords are found, the check is not applicable
        if not expected_currency:
            return {
                "status": "NOT_APPLICABLE",
                "notes": "Banking details do not specify a checkable currency (Euro, USD, UK/GBP)."
            }

        # 2. Determine the actual currency from the total value
        actual_currency_found = False
        if expected_currency == "EUR" and "€" in total_value_text:
            actual_currency_found = True
        elif expected_currency == "USD" and "$" in total_value_text:
            actual_currency_found = True
        elif expected_currency == "GBP" and "£" in total_value_text:
            actual_currency_found = True

        # 3. Compare and return the result
        if actual_currency_found:
            return {
                "status": "MATCH",
                "notes": f"Correct {expected_currency} banking details used for the {expected_currency} invoice total.",
                "banking_details": banking_details_text,
                "total_value": total_value_text
            }
        else:
            return {
                "status": "MISMATCH",
                "notes": f"Mismatch detected: Banking details are for {expected_currency}, but the invoice total currency does not match.",
                "banking_details": banking_details_text,
                "total_value": total_value_text
            }


    # --- UI LAYOUT ---
    img = get_image_as_base64("Logo.png") # Make sure "logo.png" is your correct filename

    # Display the image centered using HTML/CSS
    st.markdown(
        f"""
        <div style="display: flex; justify-content: center;">
            <img src="data:image/png;base64,{img}" width="400">
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown('<h1 style="text-align: center;">🚢 Shipment Dossier Validator</h1>', unsafe_allow_html=True)
    st.markdown('<div style="text-align: center; font-size: 18px;">Upload all available documents for a single shipment. The system will use the <strong>Commercial Invoice</strong> as the source of truth and validate all other uploaded documents against it.</div>', unsafe_allow_html=True)
    st.markdown("""
    <div style="text-align: center;">
        <div style="display: inline-block; text-align: left; background-color: rgba(255, 243, 205, 0.4); border: 1px solid rgba(255, 238, 186, 0.6); padding: 1rem; border-radius: 0.5rem; margin-top: 1rem; margin-bottom: 1rem;">
            <strong>ℹ️ V1.1 Notes (07/07/2025):</strong>
            <ul>
                <li>Currently only accepts CI as source of truth</li>
                <li>Is currently built for AG1 documents (CI/PL) and won't work for external CI/PL docs.</li>
                <li>Only accepts South African Phyto (Zim in development)</li>
            </ul>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    DOCUMENT_SLOTS = {
        "commercial_invoice": "Commercial Invoice (CI) - [SOURCE OF TRUTH]",
        "packing_list": "Packing List (PL)",
        "bill_of_lading": "Bill of Lading (BOL) / Sea Waybill",
        "phyto_certificate": "Phytosanitary Certificate",
        "ppecb": "PPECB Certificate",
        "certificate_of_origin": "Certificate of Origin (COO)",
        "eur1": "EUR.1 Certificate"
    }

    # Define which documents should be trimmed and to how many pages.
    DOC_PAGE_LIMITS = {
        "bill_of_lading": 3
    }

    if 'file_uploads' not in st.session_state:
        st.session_state.file_uploads = {key: None for key in DOCUMENT_SLOTS}

    st.header("1. Upload Documents")
    col1, col2 = st.columns(2)

    for i, (key, label) in enumerate(DOCUMENT_SLOTS.items()):
        # Determine which column the uploader goes into
        column_to_use = col1 if i < 3 else col2

        # Place the uploader in the correct column
        with column_to_use:
            # Check if this is the source of truth document
            if key == "commercial_invoice":
                # Use a standard container to group the label and uploader.
                # Our custom CSS will target this specific container.
                with st.container():
                    st.markdown(f"**{label}**")
                    st.session_state.file_uploads[key] = st.file_uploader(
                        label=label,
                        type=["pdf"], 
                        key=f"{key}_sot", # A unique key
                        label_visibility="collapsed"
                    )
            else:
                # For all other documents, create them normally
                st.markdown(f"**{label}**")
                st.session_state.file_uploads[key] = st.file_uploader(
                    label=label,
                    type=["pdf"], 
                    key=key,
                    label_visibility="collapsed"
                )

    st.divider()

    # --- MAIN LOGIC ON BUTTON CLICK ---
    st.header("2. Run Validation")
    if st.button("Validate All Uploaded Documents", type="primary", use_container_width=True):
        
        if not st.session_state.file_uploads["commercial_invoice"]:
            st.error("Validation requires a Commercial Invoice as the source of truth. Please upload one.")
        else:
            all_extracted_data = {}
            
            # --- EXTRACTION PHASE ---
            with st.spinner("Extracting data from all uploaded documents... This may take a moment."):
                for doc_key, uploaded_file in st.session_state.file_uploads.items():
                    if uploaded_file:
                        original_file_bytes = uploaded_file.getvalue()
                        bytes_to_process = original_file_bytes
                        
                        # Check if there's a page limit for this document type
                        page_limit = DOC_PAGE_LIMITS.get(doc_key)
                        if page_limit:
                            # Trim the PDF if a limit is defined
                            trimmed_bytes, was_trimmed = trim_pdf_to_max_pages(original_file_bytes, page_limit)
                            bytes_to_process = trimmed_bytes
                            
                            if was_trimmed:
                                # Inform the user that the document was trimmed
                                doc_label = DOCUMENT_SLOTS.get(doc_key, doc_key)
                                st.info(f"The '{doc_label}' was long and has been automatically trimmed to the first {page_limit} pages for processing.")

                        # Run extraction with the (potentially trimmed) PDF bytes
                        extracted_data = run_extraction_for_document(
                            doc_type_key=doc_key,
                            file_bytes=bytes_to_process, # Use the processed bytes
                            project_id=PROJECT_ID, location=LOCATION,
                            form_processor_id=FORM_PROCESSOR_ID, layout_processor_id=LAYOUT_PROCESSOR_ID
                        )
                        all_extracted_data[doc_key] = extracted_data
            
            st.success("Data extraction complete for all documents!")

            # --- VALIDATION AND DISPLAY PHASE ---
            source_of_truth_data = all_extracted_data.get("commercial_invoice")

            if not source_of_truth_data:
                st.error("Extraction failed for the Commercial Invoice. Cannot proceed with validation.")
            else:
                target_documents_to_validate = {
                    key: data for key, data in all_extracted_data.items() 
                    if key != "commercial_invoice" and data is not None
                }

                if not target_documents_to_validate:
                    st.warning("Only a Commercial Invoice was uploaded. No other documents to validate.")
                else:
                    # =================================================================
                    # --- 1. PRE-COMPUTE ALL REPORTS & BUILD SUMMARY DATA ---
                    # =================================================================
                    summary_results = {field: {} for field in source_of_truth_data.keys()}
                    all_detailed_reports = {}

                    for doc_key, target_data in target_documents_to_validate.items():
                        report = validate_documents(
                            source_of_truth=source_of_truth_data,
                            target_doc=target_data,
                            target_doc_type=doc_key
                        )
                        all_detailed_reports[doc_key] = report
                        for field, result in report.items():
                            if field in summary_results:
                                summary_results[field][doc_key] = result['status']

                    # =================================================================
                    # --- 2. DISPLAY THE SUMMARY TABLE ---
                    # =================================================================
                    st.divider()
                    st.header("3. Validation Summary")

                    summary_df = pd.DataFrame.from_dict(summary_results, orient='index')
                    summary_df.columns = [c.replace('_', ' ').title() for c in summary_df.columns]
                    summary_df.index = [i.replace('_', ' ').title() for i in summary_df.index]
                    summary_df.index.name = "Field"

                    # Using lighter, more accessible colors for backgrounds
                    STATUS_COLORS = {
                        ValidationStatus.MATCHED_EXACTLY: '#d4edda',        # Light Green
                        ValidationStatus.MATCHED_CONTENT_ONLY: '#d4edda',
                        ValidationStatus.MATCHED_WITH_TOLERANCE: '#d4edda',
                        ValidationStatus.MATCHED_MOSTLY: '#fff3cd',         # Light Yellow
                        ValidationStatus.DOES_NOT_MATCH: '#f8d7da',         # Light Red
                        ValidationStatus.MISSING_REQUIRED_FIELD: '#f8d7da',
                        ValidationStatus.TYPE_ERROR: '#ffeeba',             # Light Orange
                        ValidationStatus.NOT_APPLICABLE: '#e9ecef'          # Light Gray
                    }
                    def style_status_cells(status):
                        color = STATUS_COLORS.get(status, 'white')
                        return f'background-color: {color}'

                    table_height = (len(summary_df) + 1) * 35 + 3
                    
                    st.dataframe(
                        summary_df.style.applymap(style_status_cells).format(lambda s: str(s).replace('_', ' ').title() if pd.notna(s) else 'N/A'),
                        use_container_width=True,
                        height=table_height
                    )

                    # --- NEW: BANKING DETAILS VALIDATION SECTION ---
                    st.subheader("Internal Check: Banking Details")

                    # Perform the validation on the source of truth data
                    banking_check_result = validate_banking_details(source_of_truth_data)
                    status = banking_check_result.get("status")

                    # Display the results based on the status
                    if status == "NOT_APPLICable":
                        st.info(banking_check_result.get("notes"))
                    else:
                        # Use columns for a clean side-by-side layout
                        col_bank, col_val = st.columns(2)

                        with col_bank:
                            st.write("**Banking Details Provided**")
                            # Use st.text to properly display the multi-line address
                            st.text(banking_check_result.get("banking_details", "Not Found"))
                        
                        with col_val:
                            st.write("**Total Value Provided**")
                            st.text(banking_check_result.get("total_value", "Not Found"))
                            
                            st.write("**Result**")
                            if status == "MATCH":
                                st.success(f"✓ {banking_check_result.get('notes')}")
                            elif status == "MISMATCH":
                                st.error(f"✗ {banking_check_result.get('notes')}")

                    st.divider()

                    # =================================================================
                    # --- 3. DISPLAY DETAILED REPORTS IN TABS (USING STORED DATA) ---
                    # =================================================================
                    st.header("4. Detailed Reports")
                    tab_titles = [key.replace('_', ' ').title() for key in target_documents_to_validate.keys()]
                    tabs = st.tabs(tab_titles)

                    for i, doc_key in enumerate(target_documents_to_validate.keys()):
                        with tabs[i]:
                            st.subheader(f"Validation Report for: {doc_key.replace('_', ' ').title()}")
                            
                            # Retrieve the pre-computed report
                            report = all_detailed_reports[doc_key]

                            for field, result in report.items():
                                status = result['status']
                                
                                if status == ValidationStatus.NOT_APPLICABLE:
                                    continue

                                res_col1, res_col2, res_col3 = st.columns([1, 2, 2])
                                
                                with res_col1:
                                    st.write(f"**{field.replace('_', ' ').title()}**")
                                    if status == ValidationStatus.MATCHED_EXACTLY: st.success("✓ Matched Exactly")
                                    elif status == ValidationStatus.MATCHED_CONTENT_ONLY: st.success("✓ Matched Content")
                                    elif status == ValidationStatus.MATCHED_WITH_TOLERANCE: st.success("✓ Matched (In Tolerance)")
                                    elif status == ValidationStatus.MATCHED_MOSTLY: st.warning(f"~ Mostly Matched ({result.get('score', 'N/A')}%)")
                                    elif status == ValidationStatus.MISSING_REQUIRED_FIELD: st.error("✗ Missing Required Field")
                                    elif status == ValidationStatus.DOES_NOT_MATCH: st.error("✗ Mismatch")
                                    elif status == ValidationStatus.TYPE_ERROR: st.error("✗ Type Error")
                                
                                # --- All your refined display logic below is preserved ---
                                if field in MULTI_LINE_FIELDS:
                                    with res_col2:
                                        st.write("**Source of Truth Value**")
                                        st.text(result.get('source_value', ''))
                                    with res_col3:
                                        st.write("**Document Value**")
                                        st.text(result.get('target_value', ''))

                                elif field in CONTAINER_FIELDS:
                                    with res_col2:
                                        st.write("**Source of Truth Value**")
                                        st.text(format_container_numbers_for_display(result.get('source_value')))
                                    with res_col3:
                                        st.write("**Document Value**")
                                        st.text(format_container_numbers_for_display(result.get('target_value')))

                                elif field in SIMPLE_TEXT_FIELDS or field in PARTIAL_MATCH_FIELDS:
                                    with res_col2:
                                        st.write("**Source of Truth Value**")
                                        st.text(result.get('source_value', ''))
                                    with res_col3:
                                        st.write("**Document Value**")
                                        st.text(result.get('target_value', ''))
                                        
                                elif field in INTEGER_FIELDS:
                                    with res_col2:
                                        st.write("**Source of Truth Value**")
                                        st.text(format_numeric_for_display(result.get('source_value'), 'int'))
                                    with res_col3:
                                        st.write("**Document Value**")
                                        st.text(format_numeric_for_display(result.get('target_value'), 'int'))

                                elif field in FLOAT_FIELDS:
                                    with res_col2:
                                        st.write("**Source of Truth Value**")
                                        st.text(format_numeric_for_display(result.get('source_value'), 'float'))
                                    with res_col3:
                                        st.write("**Document Value**")
                                        st.text(format_numeric_for_display(result.get('target_value'), 'float'))
                                        
                                elif field in CURRENCY_FIELDS:
                                    with res_col2:
                                        st.write("**Source of Truth Value**")
                                        st.text(format_numeric_for_display(result.get('source_value'), 'currency'))
                                    with res_col3:
                                        st.write("**Document Value**")
                                        st.text(format_numeric_for_display(result.get('target_value'), 'currency'))

                                else:
                                    with res_col2:
                                        st.write("**Source of Truth Value**")
                                        st.code(json.dumps(result.get('source_value'), indent=2, ensure_ascii=False), language="json")
                                    with res_col3:
                                        st.write("**Document Value**")
                                        st.code(json.dumps(result.get('target_value'), indent=2, ensure_ascii=False), language="json")

                                if result.get("notes"):
                                    st.caption(f"Note: {result['notes']}")
                                
                                st.divider()