from typing import Optional, Dict, List, Tuple
import re


def get_text(text_anchor: dict, text: str) -> str:
    """
    Document AI's text anchor maps to a part of the full text.
    This function extracts that part of the text.
    """
    if not text_anchor.text_segments:
        return ""
    
    # The text is stored in segments. Join them together.
    # The Form Parser typically only has one segment.
    start_index = int(text_anchor.text_segments[0].start_index)
    end_index = int(text_anchor.text_segments[0].end_index)
    
    return text[start_index:end_index]


def extract_bol_data(document):
    """
    Extracts key fields from a Document AI processed invoice.
    Uses a hybrid approach:
    1. Gets all key-value pairs from the Form Parser.
    2. Uses custom logic for fields the parser misses or gets wrong.

    We are creating the entire dictionary key here, but some fields that aren't captured here are captured by the Agent:
    - container numbers
    - gross/net mass
    - total cartons
    Hence the above will always be null from this output.
    """
    document_text = document.text
    
    form_data = {}
    for page in document.pages:
        for field in page.form_fields:
            key = get_text(field.field_name.text_anchor, document_text).strip().lower()
            value = get_text(field.field_value.text_anchor, document_text).strip()
            form_data[key] = value

    extracted_data = {
        "exporter_address": None,
        "consignee_details": None,
        "notify_party_details": None,
        "container_number": None,
        "vessel_name": None,
        "voyage": None,
        "port_of_destination": None,
        "total_cartons": None,
        "total_gross_mass_kg": None,
        "total_net_mass_kg": None
    }

    extracted_data["exporter_address"] = extract_block_between_headers(
        document,
        start_keyword="Shipper",
        stop_keywords=["Consignee"],
        horizontal_constraint="left"
    )

    # These are junk lines to exclude from OCR output, since doc is standardised these will always exist and can be hard coded
    consignee_exclusions = [
        "(B/L NOT NEGOTIABLE UNLESS CONSIGNED TO ORDER",
        "As principal, where" 
    ]

    extracted_data["consignee_details"] = extract_block_between_headers(
        document,
        start_keyword="Consignee",
        stop_keywords=["Notify"],
        horizontal_constraint="left",
        exclude_keywords=consignee_exclusions
    )

    # More junk lines
    notify_exclusions = [
        "Clause 20)" 
    ]

    extracted_data["notify_party_details"] = extract_block_between_headers(
        document,
        start_keyword="Notify",
        stop_keywords=["Vessel", "Initial Carriage"],
        horizontal_constraint="left",
        exclude_keywords=notify_exclusions
    )
    transport_details = extract_bol_vessel_voyage(document)
    extracted_data["vessel_name"] = transport_details.get("vessel_name")
    extracted_data["voyage"] = transport_details.get("voyage")
    extracted_data["port_of_destination"] = extract_bol_port_of_destination(document)

    return extracted_data

def find_line_by_substring(page, substring: str, document_text: str):
    """
    Finds the first line on a page containing a specific substring,
    ignoring case.
    """
    for line in page.lines:
        line_text = get_text(line.layout.text_anchor, document_text)
        
        # Use re.search with the IGNORECASE flag instead of a simple 'in' check.
        # This will match 'Shipper', 'SHIPPER', 'shipper', etc.
        if re.search(substring, line_text, re.IGNORECASE):
            return line
            
    return None

def extract_block_between_headers(
    document: dict, 
    start_keyword: str,
    stop_keywords: List[str],
    horizontal_constraint: str = "left",
    exclude_keywords: Optional[List[str]] = None
) -> Optional[str]:
    """
    The definitive universal function. Extracts a block of text between a
    start keyword and the CLOSEST of multiple possible stop keywords.
    """
    if not document.pages:
        return None
        
    document_text = document.text

    for page in document.pages:
        # Step 1: Find the start anchor
        start_anchor = find_line_by_substring(page, start_keyword, document_text)
        if not start_anchor:
            continue

        # --- Find all potential stop anchors and choose the closest one ---
        potential_stops = []
        for stop_word in stop_keywords:
            stop_anchor_candidate = find_line_by_substring(page, stop_word, document_text)
            if stop_anchor_candidate:
                # Store the anchor and its top y-coordinate
                stop_top_y = min(v.y for v in stop_anchor_candidate.layout.bounding_poly.normalized_vertices)
                potential_stops.append((stop_top_y, stop_anchor_candidate))
        
        if not potential_stops:
            print(f"Found start anchor '{start_keyword}' but none of the stop keywords.")
            continue
            
        # Sort potential stops by their vertical position
        potential_stops.sort()
        
        # The definitive stop anchor is the first one found below the start anchor
        start_anchor_bottom_y = max(v.y for v in start_anchor.layout.bounding_poly.normalized_vertices)
        definitive_stop_anchor = None
        for stop_y, stop_anchor in potential_stops:
            if stop_y > start_anchor_bottom_y:
                definitive_stop_anchor = stop_anchor
                break 
                
        if not definitive_stop_anchor:
            print(f"Found stop keywords, but none were below the start anchor '{start_keyword}'.")
            continue

        stop_keyword_text = get_text(definitive_stop_anchor.layout.text_anchor, document_text)
        print(f"Found anchors '{start_keyword}' and closest stop '{stop_keyword_text}' on Page {page.page_number}.")
        
        # --- The rest of the function proceeds as before with the definitive anchors ---
        start_bbox = start_anchor.layout.bounding_poly
        stop_below_bbox = definitive_stop_anchor.layout.bounding_poly
        
        search_top_y = max(v.y for v in start_bbox.normalized_vertices)
        search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
        slice_boundary_x = min(v.x for v in start_bbox.normalized_vertices)
        
        if search_bottom_y <= search_top_y:
            continue

        print(f"Defined vertical search: y=({search_top_y:.3f}, {search_bottom_y:.3f}). Left slice at x > {slice_boundary_x:.3f}")

        found_lines_with_pos = []
        for line in page.lines:
            if line in [start_anchor, definitive_stop_anchor]: continue

            line_bbox = line.layout.bounding_poly
            line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
            
            if not (search_top_y < line_center_y < search_bottom_y):
                continue
            
            line_left_x = min(v.x for v in line_bbox.normalized_vertices)
            if line_left_x < (slice_boundary_x - 0.01):
                continue
            
            line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
            if horizontal_constraint == "left" and line_center_x >= 0.5:
                continue
            elif horizontal_constraint == "right" and line_center_x < 0.5:
                continue
            
            line_text = get_text(line.layout.text_anchor, document_text).strip()
            
            if exclude_keywords:
                if any(re.search(re.escape(keyword), line_text, re.IGNORECASE) for keyword in exclude_keywords):
                    continue
            
            if line_text:
                line_top_y = min(v.y for v in line_bbox.normalized_vertices)
                found_lines_with_pos.append((line_top_y, line_text))

        if not found_lines_with_pos:
            continue

        found_lines_with_pos.sort()
        final_block = "\n".join([text for _, text in found_lines_with_pos])
        
        print(f"SUCCESS: Extracted block for '{start_keyword}'.")
        return final_block

    print(f"Could not find a valid text block for '{start_keyword}' on any page.")
    return None


def extract_line_under_header(document: dict, header_keyword: str) -> Optional[str]:
    """
    A robust function that finds a header by keyword and returns the text
    of the first valid data line below it, skipping over other potential headers.
    """
    if not document.pages:
        return None
        
    document_text = document.text

    for page in document.pages:
        anchor_line = find_line_by_substring(page, header_keyword, document_text)
        
        if not anchor_line:
            continue
            
        anchor_bbox = anchor_line.layout.bounding_poly
        anchor_center_x = (min(v.x for v in anchor_bbox.normalized_vertices) + max(v.x for v in anchor_bbox.normalized_vertices)) / 2.0
        anchor_bottom_y = max(v.y for v in anchor_bbox.normalized_vertices)
        
        column_tolerance = 0.20 # Use a slightly wider tolerance to be safe
        search_left_x = anchor_center_x - column_tolerance
        search_right_x = anchor_center_x + column_tolerance

        # Find all candidate lines below the anchor
        candidate_lines_with_pos = []
        for line in page.lines:
            if line == anchor_line: continue
                
            line_bbox = line.layout.bounding_poly
            line_top_y = min(v.y for v in line_bbox.normalized_vertices)
            line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0

            if line_top_y > anchor_bottom_y and search_left_x < line_center_x < search_right_x:
                candidate_lines_with_pos.append((line_top_y, line))
        
        if not candidate_lines_with_pos:
            continue

        # Sort the candidates by how close they are vertically
        candidate_lines_with_pos.sort()

        # --- Filter out lines that look like other headers ---
        # A blacklist of keywords that are likely to be headers, not data.
        HEADER_BLACKLIST = ["PORT OF", "FINAL DESTINATION", "PLACE OF", "RECEIPT", "LOADING"]

        for _, line_object in candidate_lines_with_pos:
            line_text = get_text(line_object.layout.text_anchor, document_text).strip()
            
            # Check if the line text contains any blacklisted header phrases
            if not any(keyword in line_text.upper() for keyword in HEADER_BLACKLIST):
                return line_text
        return None
    return None


def get_line_center(line) -> Tuple[float, float]:
    """Gets the (x, y) center of a line's bounding box."""
    bbox = line.layout.bounding_poly
    center_x = (min(v.x for v in bbox.normalized_vertices) + max(v.x for v in bbox.normalized_vertices)) / 2.0
    center_y = (min(v.y for v in bbox.normalized_vertices) + max(v.y for v in bbox.normalized_vertices)) / 2.0
    return (center_x, center_y)

def find_voyage_code_final(text: str) -> Optional[str]:
    """The definitive voyage code finder."""
    if not text: return None
    words = re.split(r'[\s-]+', text)
    for word in words:
        if 3 <= len(word) <= 10 and word.isalnum() and any(char.isdigit() for char in word):
            return word.upper()
    return None


def is_header_like(text: str) -> bool:
    """
    A helper to check if a string looks like part of a header,
    rather than a real value.
    """
    text_lower = text.lower()
    # A list of words that indicate this is probably another header, not a value.
    header_keywords = ["voyage", "voy", "no", "clause", "vessel"]
    # If the text contains any of these keywords, it's likely a header.
    return any(keyword in text_lower for keyword in header_keywords)


def find_value_for_header_final(page, document_text: str, keyword: str) -> Optional[str]:
    """
    The definitive helper. Intelligently checks for a value on the same line,
    ignoring it if it looks like another header, then checks to the right, and finally below.
    """
    anchor_line = find_line_by_substring(page, keyword, document_text)
    if not anchor_line:
        return None

    anchor_text = get_text(anchor_line.layout.text_anchor, document_text)
    words = anchor_text.split()
    
    keyword_index = -1
    for i, word in enumerate(words):
        if keyword.lower() in word.lower():
            keyword_index = i
            break
            
    if keyword_index != -1 and keyword_index < len(words) - 1:
        value_on_same_line = " ".join(words[keyword_index + 1:]).strip(":- ")
        
        if value_on_same_line and not is_header_like(value_on_same_line):
            return value_on_same_line

    anchor_bbox = anchor_line.layout.bounding_poly
    anchor_center_y = get_line_center(anchor_line)[1]
    anchor_right_x = max(v.x for v in anchor_bbox.normalized_vertices)
    anchor_center_x = get_line_center(anchor_line)[0]
    
    # Look for value to the RIGHT (with gap check)
    horizontal_gap_threshold = 0.05
    closest_right_value = None
    min_dist_right = float('inf')
    for line in page.lines:
        if line == anchor_line: continue
        if abs(get_line_center(line)[1] - anchor_center_y) < 0.02:
            line_left_x = min(v.x for v in line.layout.bounding_poly.normalized_vertices)
            if line_left_x > anchor_right_x:
                distance = line_left_x - anchor_right_x
                if distance < min_dist_right and distance < horizontal_gap_threshold:
                    min_dist_right = distance
                    closest_right_value = get_text(line.layout.text_anchor, document_text)
    if closest_right_value:
        return closest_right_value.strip()

    # If nothing else, look BELOW
    closest_below_value = None
    min_dist_below = float('inf')
    for line in page.lines:
        if line == anchor_line: continue
        line_top_y = min(v.y for v in line.layout.bounding_poly.normalized_vertices)
        # Must be below and in a reasonably similar column
        if line_top_y > anchor_center_y and abs(get_line_center(line)[0] - anchor_center_x) < 0.2:
            distance = line_top_y - anchor_center_y
            if distance < min_dist_below:
                min_dist_below = distance
                closest_below_value = get_text(line.layout.text_anchor, document_text)
    return closest_below_value.strip() if closest_below_value else None


def extract_bol_vessel_voyage(document: dict) -> Dict[str, Optional[str]]:
    """
    The definitive master function using the new "find nearby or below" helper
    and the correct "Vessel First" waterfall logic.
    """
    results = {"vessel_name": None, "voyage": None}
    if not document.pages:
        return results
        
    page = document.pages[0]
    document_text = document.text

    print("\n--- Running 'Vessel First' Strategy with Universal Helper ---")
    
    vessel_candidate_text = find_value_for_header_final(page, document_text, "Vessel")
    
    if not vessel_candidate_text:
        print("Could not find any value for the 'Vessel' keyword.")
        return results

    print(f"  - Found vessel candidate text: '{vessel_candidate_text}'")

    voyage_code_in_vessel = find_voyage_code_final(vessel_candidate_text)
    
    if voyage_code_in_vessel:
        results["voyage"] = voyage_code_in_vessel
        results["vessel_name"] = vessel_candidate_text.replace(voyage_code_in_vessel, "").strip(' -')
        print(f"SUCCESS (Merged Field): Vessel='{results['vessel_name']}', Voyage='{results['voyage']}'")
    else:
        results["vessel_name"] = vessel_candidate_text
        print(f"  - No voyage code in vessel string. Now searching for separate voyage value...")
        
        voyage_candidate_text = find_value_for_header_final(page, document_text, "Voyage-No")
        if not voyage_candidate_text:
            voyage_candidate_text = find_value_for_header_final(page, document_text, "Voy")
        
        voyage_code = find_voyage_code_final(voyage_candidate_text)
        
        if voyage_code:
            results["voyage"] = voyage_code
            print(f"SUCCESS (Separate Fields): Vessel='{results['vessel_name']}', Voyage='{results['voyage']}'")
        else:
            print(f"  - Found text '{voyage_candidate_text}' but it contains no valid voyage code. Discarding.")
            results["voyage"] = None
            print(f"SUCCESS (Vessel Only): Vessel='{results['vessel_name']}', Voyage='{results['voyage']}'")
        
    return results


def find_value_for_header_with_blacklist(
    page, 
    document_text: str, 
    keyword: str, 
    ignore_list: List[str] = None
) -> Optional[str]:
    """
    Finds a value for a header keyword, but completely
    ignores any anchor lines that contain words from the ignore_list.
    """
    if ignore_list is None:
        ignore_list = []

    # Step 1: Find a valid anchor line
    anchor_line = None
    for line in page.lines:
        line_text = get_text(line.layout.text_anchor, document_text)
        line_text_lower = line_text.lower()
        
        # Condition 1: The line must contain our main keyword
        if keyword.lower() in line_text_lower:
            # Condition 2: The line must NOT contain any of the ignored words
            is_ignored = any(ignored_word.lower() in line_text_lower for ignored_word in ignore_list)
            
            if not is_ignored:
                anchor_line = line
                break 
    
    if not anchor_line:
        return None

    # Step 2: Now that we have a VALID anchor, find its value
    # Check same line
    anchor_text = get_text(anchor_line.layout.text_anchor, document_text)
    words = anchor_text.split()
    keyword_index = -1
    for i, word in enumerate(words):
        if keyword.lower() in word.lower():
            keyword_index = i
            break
    if keyword_index != -1 and keyword_index < len(words) - 1:
        value = " ".join(words[keyword_index + 1:]).strip(":- ")
        if value: return value

    # Check right
    anchor_bbox = anchor_line.layout.bounding_poly
    anchor_center_y = get_line_center(anchor_line)[1]
    anchor_right_x = max(v.x for v in anchor_bbox.normalized_vertices)
    closest_right_value = None
    min_dist = float('inf')
    for line in page.lines:
        if line == anchor_line: continue
        if abs(get_line_center(line)[1] - anchor_center_y) < 0.02 and min(v.x for v in line.layout.bounding_poly.normalized_vertices) > anchor_right_x:
            dist = min(v.x for v in line.layout.bounding_poly.normalized_vertices) - anchor_right_x
            if dist < min_dist and dist < 0.05:
                min_dist = dist
                closest_right_value = get_text(line.layout.text_anchor, document_text)
    if closest_right_value: return closest_right_value.strip()

    # Check below
    anchor_center_x = get_line_center(anchor_line)[0]
    closest_below_value = None
    min_dist = float('inf')
    for line in page.lines:
        if line == anchor_line: continue
        line_top_y = min(v.y for v in line.layout.bounding_poly.normalized_vertices)
        if line_top_y > anchor_center_y and abs(get_line_center(line)[0] - anchor_center_x) < 0.2:
            dist = line_top_y - anchor_center_y
            if dist < min_dist:
                min_dist = dist
                closest_below_value = get_text(line.layout.text_anchor, document_text)
    return closest_below_value.strip() if closest_below_value else None



def extract_bol_port_of_destination(document: dict) -> Optional[str]:
    """
    Extracts the Port of Destination using the helper with a blacklist.
    """
    if not document.pages:
        return None
        
    page = document.pages[0]
    document_text = document.text
    
    print("\n--- Extracting Port of Destination with Blacklist Logic ---")

    # Attempt 1: Look for "PORT OF DISCHARGE"  
    print("  - Searching for 'PORT OF DISCHARGE', ignoring 'AGENT'...")
    
    value = find_value_for_header_with_blacklist(
        page, 
        document_text, 
        keyword="PORT OF DISCHARGE", 
        ignore_list=["AGENT"]  
    )
    
    if value and not is_header_like(value):
        print(f"SUCCESS: Found value '{value}' for keyword 'PORT OF DISCHARGE'.")
        return value

    # Attempt 2 (Fallback): Look for "PORT OF DESTINATION"
    print("  - 'PORT OF DISCHARGE' not found or invalid. Trying 'PORT OF DESTINATION'...")
    
    value = find_value_for_header_with_blacklist(
        page, 
        document_text, 
        keyword="PORT OF DESTINATION"
    )
    
    if value and not is_header_like(value):
        print(f"SUCCESS: Found value '{value}' for keyword 'PORT OF DESTINATION'.")
        return value
    
    print("--- FAILED to find Port of Destination. ---")
    return None