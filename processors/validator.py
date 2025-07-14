from typing import Dict, Any
from thefuzz import fuzz # Levenshtein distance library
import re

# ==============================================================================
# CONFIGURATION AND STATUSES
# ==============================================================================

class ValidationStatus:
    # All existing statuses
    MATCHED_EXACTLY = "MATCHED_EXACTLY"
    MATCHED_WITH_TOLERANCE = "MATCHED_WITH_TOLERANCE"
    MATCHED_CONTENT_ONLY = "MATCHED_CONTENT_ONLY"
    MATCHED_MOSTLY = "MATCHED_MOSTLY"
    DOES_NOT_MATCH = "DOES_NOT_MATCH"
    TYPE_ERROR = "TYPE_ERROR"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD" 
    NOT_APPLICABLE = "NOT_APPLICABLE"                

# Define which fields get special handling
MULTI_LINE_FIELDS = ["exporter_address", "consignee_details", "notify_party_details", "invoice_party_details", "banking_details"]
INTEGER_FIELDS = ["total_cartons"]
FLOAT_FIELDS = ["total_gross_mass_kg", "total_net_mass_kg"]
CURRENCY_FIELDS = ["total_value"]
CONTAINER_FIELDS = ["container_number"]
SIMPLE_TEXT_FIELDS = ["vessel_name", "voyage", "port_of_destination"]
PARTIAL_MATCH_FIELDS = ["port_of_destination"]

# --- NEW: VALIDATION PROFILES ---
# Define which fields are expected for each document type.
VALIDATION_PROFILES = {
    "bill_of_lading": {
        "required_fields": [
            "exporter_address", "consignee_details", "notify_party_details",
            "container_number", "vessel_name", "voyage", "port_of_destination",
            "total_cartons", "total_gross_mass_kg", "total_net_mass_kg"
        ]
    },
    "phyto_certificate": {
        "required_fields": [
            "exporter_address", "consignee_details", "container_number", "port_of_destination",
            "total_cartons", "total_net_mass_kg", "total_gross_mass_kg"
        ]
    },
    "eur1": {
        "required_fields": [
            "exporter_address", "consignee_details", "vessel_name", "voyage", "port_of_destination",
            "container_number", "total_cartons", "total_gross_mass_kg", "total_net_mass_kg"
        ]
    },
    "certificate_of_origin": {
        "required_fields": [
            "exporter_address", "consignee_details", "container_number",
             "total_gross_mass_kg", "total_cartons"
        ]
    },
    "packing_list": {
        "required_fields": [
            "exporter_address", "consignee_details", "notify_party_details", 
            "invoice_party_details", "container_number", "vessel_name", "port_of_destination",
            "total_cartons", "total_gross_mass_kg", "total_net_mass_kg"
        ]
    },
    "ppecb": {
        "required_fields": [
            "exporter_address", "container_number", "voyage", "vessel_name", "port_of_destination",
            "total_cartons", "total_gross_mass_kg", "total_net_mass_kg"
        ]
    }
}

    


# ==============================================================================
# SPECIALIZED VALIDATION HELPER FUNCTIONS
# (These functions do not need to be changed. They are already correct.)
# ==============================================================================

def normalize_string_for_fuzzy(text: Any) -> str:
    """
    Normalizes text for FUZZY MATCHING (addresses, names).
    Converts to lowercase and replaces punctuation with spaces to create a clean 'bag of words'.
    """
    if not isinstance(text, str): text = str(text)
    text = text.lower()
    text = re.sub(r'[\n\t,.:;()]', ' ', text) # Replace punctuation with space
    text = re.sub(r'[^a-z0-9\s]', '', text)    # Remove non-alphanumeric/space chars
    text = re.sub(r'\s+', ' ', text).strip()  # Collapse spaces
    return text

# --- NEW: HELPER FOR NUMBERS ---
def _normalize_for_numeric(text: Any) -> str:
    """
    Normalizes text for NUMERIC PARSING.
    Aggressively strips everything except digits, one decimal point, and a potential minus sign.
    """
    if not isinstance(text, str): text = str(text)
    # First, remove thousands separators (commas)
    text = text.replace(',', '')
    # Now, keep only digits, one decimal point, and a leading minus sign
    text = re.sub(r'[^\d.-]', '', text)
    return text

def validate_integer_field(source_value: Any, target_value: Any) -> Dict:
    """Validates integer fields using the dedicated numeric normalizer."""
    try:
        source_str = _normalize_for_numeric(source_value)
        target_str = _normalize_for_numeric(target_value)
        
        source_int = int(float(source_str))
        target_int = int(float(target_str))

        if source_int == target_int:
            return {"status": ValidationStatus.MATCHED_EXACTLY, "score": 100}
        else:
            return {"status": ValidationStatus.DOES_NOT_MATCH, "score": 0, "notes": f"Expected {source_int}, but found {target_int}."}
    except (ValueError, TypeError, AttributeError):
        return {"status": ValidationStatus.TYPE_ERROR, "notes": "Could not parse one or both values as a whole number."}

def validate_float_field(source_value: Any, target_value: Any, tolerance: float = 0.01) -> Dict:
    """Validates float fields using the dedicated numeric normalizer."""
    try:
        source_str = _normalize_for_numeric(source_value)
        target_str = _normalize_for_numeric(target_value)

        source_float = float(source_str)
        target_float = float(target_str)

        if source_float == target_float:
            return {"status": ValidationStatus.MATCHED_EXACTLY, "score": 100}
        if abs(source_float - target_float) <= (source_float * tolerance):
            return {"status": ValidationStatus.MATCHED_WITH_TOLERANCE, "score": 99, "notes": f"Values are within the {tolerance*100}% tolerance range."}
        else:
            return {"status": ValidationStatus.DOES_NOT_MATCH, "score": 0, "notes": f"Expected ~{source_float:.2f}, but found {target_float:.2f}."}
    except (ValueError, TypeError, AttributeError):
        return {"status": ValidationStatus.TYPE_ERROR, "notes": "Could not parse one or both values as a decimal number."}

def validate_currency_field(source_value: Any, target_value: Any) -> Dict:
    try:
        norm_source, norm_target = _normalize_for_numeric(source_value, keep_decimal=True), _normalize_for_numeric(target_value, keep_decimal=True)
        if norm_source == norm_target: return {"status": ValidationStatus.MATCHED_EXACTLY, "score": 100}
        else: return {"status": ValidationStatus.DOES_NOT_MATCH, "score": 0, "notes": f"Values do not match after removing currency symbols. Expected '{norm_source}', found '{norm_target}'."}
    except (ValueError, TypeError): return {"status": ValidationStatus.TYPE_ERROR, "notes": "Could not parse one or both currency values."}

def validate_container_field(source_value: Any, target_value: Any) -> Dict:
    def to_set(value: Any) -> set:
        if isinstance(value, list): return set(v.strip().upper() for v in value)
        if isinstance(value, str): return set(v.strip().upper() for v in re.split(r'[\s,]+', value) if v)
        return set()
    source_set, target_set = to_set(source_value), to_set(target_value)
    if source_set == target_set: return {"status": ValidationStatus.MATCHED_EXACTLY, "score": 100}
    score = fuzz.token_set_ratio(source_set, target_set)
    missing, extra = source_set - target_set, target_set - source_set
    notes = []
    if missing: notes.append(f"Missing from target: {', '.join(missing)}")
    if extra: notes.append(f"Extra in target: {', '.join(extra)}")
    if score > 85: return {"status": ValidationStatus.MATCHED_MOSTLY, "score": score, "notes": ". ".join(notes)}
    else: return {"status": ValidationStatus.DOES_NOT_MATCH, "score": score, "notes": ". ".join(notes)}

def validate_partial_match_field(source_value: Any, target_value: Any) -> Dict:
    """
    Validates fields where one value can be a subset of the other,
    using a more lenient threshold to handle abbreviations like 'NL' vs 'Netherlands'.
    """
    source_str = str(source_value)
    target_str = str(target_value)

    norm_source = normalize_string_for_fuzzy(source_str)
    norm_target = normalize_string_for_fuzzy(target_str)

    if norm_source == norm_target:
        return {"status": ValidationStatus.MATCHED_EXACTLY, "score": 100}

    score = fuzz.token_set_ratio(norm_source, norm_target)
    
    if score > 80:
        return {
            "status": ValidationStatus.MATCHED_MOSTLY,
            "score": score,
            "notes": f"Values are highly similar (Score: {score}%). This may be an abbreviation match."
        }
    else:
        return {
            "status": ValidationStatus.DOES_NOT_MATCH,
            "score": score,
            "notes": f"Values are significantly different (Score: {score}%)."
        }


def validate_generic_field(source_value: Any, target_value: Any, is_multi_line: bool) -> Dict:
    """
    An intelligent validator for text and address fields that uses a multi-layered
    fuzzy matching approach to better handle real-world variations.
    """
    source_str = str(source_value)
    target_str = str(target_value)

    norm_source = normalize_string_for_fuzzy(source_str)
    norm_target = normalize_string_for_fuzzy(target_str)
    
    if norm_source == norm_target:
        return {"status": ValidationStatus.MATCHED_CONTENT_ONLY, "score": 99, "notes": "Content matches, but formatting (e.g., punctuation, case) differs."}

    # 3. Perform a two-stage fuzzy match using different algorithms.
    #    token_sort_ratio: Good for overall similarity, less sensitive to extra words.
    #    token_set_ratio: Excellent for subset matching.
    sort_score = fuzz.token_sort_ratio(norm_source, norm_target)
    set_score = fuzz.token_set_ratio(norm_source, norm_target)
    
    # Use the HIGHER of the two scores as our final confidence score.
    # This gives us the best chance of finding a reasonable match.
    final_score = max(sort_score, set_score)
    
    notes = f"Fuzzy Match Score: {final_score}% (Sort: {sort_score}%, Set: {set_score}%)"

    # 4. Determine Status based on the final score.
    if final_score > 95: # A very high score indicates a near-perfect subset or identical content.
        return {"status": ValidationStatus.MATCHED_MOSTLY, "score": final_score, "notes": "One address appears to be a perfect or near-perfect subset of the other."}
    elif final_score > 75: # Lower the threshold to be more lenient on significant but similar addresses.
        return {"status": ValidationStatus.MATCHED_MOSTLY, "score": final_score, "notes": "Values are highly similar but have notable differences."}
    else:
        # For low scores, provide detailed diagnostics.
        source_words = set(norm_source.split())
        target_words = set(norm_target.split())
        missing_words = source_words - target_words
        extra_words = target_words - source_words
        
        diff_notes = []
        if missing_words: diff_notes.append(f"Words in source not in target: {', '.join(missing_words)}")
        if extra_words: diff_notes.append(f"Words in target not in source: {', '.join(extra_words)}")
        
        return {"status": ValidationStatus.DOES_NOT_MATCH, "score": final_score, "notes": " ".join(diff_notes)}


# ==============================================================================
# THE MAIN VALIDATION DISPATCHER (UPDATED WITH NEW LOGIC)
# ==============================================================================

def validate_documents(
    source_of_truth: Dict[str, Any], 
    target_doc: Dict[str, Any],
    target_doc_type: str # The key to our new context-aware logic
) -> Dict[str, Dict[str, Any]]:
    """
    Compares a target document against a source of truth, using a profile
    to understand which fields are required for the target document type.
    """
    
    validation_report = {}
    
    # Get the list of required fields for this document type from our profiles
    profile = VALIDATION_PROFILES.get(target_doc_type, {})
    required_fields_for_target = profile.get("required_fields", [])
    
    # The main loop now iterates over the keys in the source of truth, as it's the master record.
    for key, source_value in source_of_truth.items():
        target_value = target_doc.get(key)
        
        # --- NEW CONTEXT-AWARE LOGIC FOR MISSING VALUES ---
        # Check if the value is missing in the target document
        if target_value is None or str(target_value).strip() == "":
            
            # If it's missing, we check our profile: was it required?
            if key in required_fields_for_target:
                # This is a real problem.
                status = ValidationStatus.MISSING_REQUIRED_FIELD
                notes = "This required field is missing from the document."
            else:
                # This is expected behavior, not an error.
                status = ValidationStatus.NOT_APPLICABLE
                notes = "This field is not applicable to this document type."

            # We create the report entry and continue to the next field.
            validation_report[key] = {
                "source_value": source_value, "target_value": target_value,
                "status": status, "score": None, "notes": notes
            }
            continue # Skip the rest of the validation for this key

        # --- DISPATCHER LOGIC (If target_value exists) ---
        # This part of the logic runs only if the target value is present.
        if key in INTEGER_FIELDS:
            field_result = validate_integer_field(source_value, target_value)
        elif key in FLOAT_FIELDS:
            field_result = validate_float_field(source_value, target_value)
        elif key in CURRENCY_FIELDS:
            field_result = validate_currency_field(source_value, target_value)
        elif key in CONTAINER_FIELDS:
            field_result = validate_container_field(source_value, target_value)
        elif key in PARTIAL_MATCH_FIELDS:
            field_result = validate_partial_match_field(source_value, target_value)
        else:
            is_multi_line = key in MULTI_LINE_FIELDS
            field_result = validate_generic_field(source_value, target_value, is_multi_line)
        
        # Add the original values to the result dictionary for easy display in the UI
        field_result["source_value"] = source_value
        field_result["target_value"] = target_value
        validation_report[key] = field_result
        
    return validation_report