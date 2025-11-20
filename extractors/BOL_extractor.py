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

    raw_exporter = extract_block_between_headers(
    document,
    start_keyword="Shipper",
    stop_keywords=["Consignee"],
    horizontal_constraint="left"
)
    # NEW: fallback if geometry failed
    if not raw_exporter:
        raw_exporter = extract_bol_shipper_by_regex(document)
    extracted_data["exporter_address"] = clean_exporter_address_block(raw_exporter)

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

    # Fallback: text-only regex if geometric extraction failed
    if not extracted_data["consignee_details"]:
        alt_consignee = extract_bol_consignee_by_regex(document)
        alt_consignee = extend_consignee_with_contact_lines(document, alt_consignee)
        if alt_consignee:
            extracted_data["consignee_details"] = alt_consignee

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

    # Fallback: text-only regex if geometric extraction failed
    if not extracted_data["notify_party_details"]:
        alt_notify = extract_bol_notify_by_regex(document)
        if alt_notify:
            extracted_data["notify_party_details"] = alt_notify

    transport_details = extract_bol_vessel_voyage(document)
    extracted_data["vessel_name"] = transport_details.get("vessel_name")
    extracted_data["voyage"] = transport_details.get("voyage")
    extracted_data["port_of_destination"] = extract_bol_port_of_destination(document)

    return extracted_data

def find_line_by_substring(page, substring: str, document_text: str):
    """
    Finds the most likely header line containing a specific substring:
    - Prefer exact match (line == substring, ignoring case)
    - Then prefer lines that start with the keyword (e.g. 'Shipper ...')
      and are not possessive like 'SHIPPER'S ...'
    - Finally, fall back to the shortest matching line.
    """
    target = substring.lower()
    candidates = []

    for line in page.lines:
        line_text = get_text(line.layout.text_anchor, document_text).strip()
        if not line_text:
            continue

        lt = line_text.lower()
        if target in lt:
            candidates.append({
                "line": line,
                "text": line_text,
                "lt": lt,
                "exact": lt == target,
                "starts_with": lt.startswith(target),
                # Handle cases like "SHIPPER'S LOAD..." (we usually want to avoid these as anchors)
                "has_possessive": (target + "'s") in lt,
            })

    if not candidates:
        return None

    # 1) Prefer exact match (e.g. 'Vessel')
    exacts = [c for c in candidates if c["exact"]]
    if exacts:
        # If more than one, choose the shortest line
        best = min(exacts, key=lambda c: len(c["text"]))
        return best["line"]

    # 2) Prefer lines that start with the keyword and are NOT possessive
    #    ('Shipper ...' yes, 'SHIPPER'S LOAD...' no)
    starts_clean = [
        c for c in candidates
        if c["starts_with"] and not c["has_possessive"]
    ]
    if starts_clean:
        best = min(starts_clean, key=lambda c: len(c["text"]))
        return best["line"]

    # 3) Then allow starts_with even if possessive, if nothing else found
    starts_any = [c for c in candidates if c["starts_with"]]
    if starts_any:
        best = min(starts_any, key=lambda c: len(c["text"]))
        return best["line"]

    # 4) Fallback: shortest line containing the keyword
    best = min(candidates, key=lambda c: len(c["text"]))
    return best["line"]

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
    Master function:
    1) Try geometry-based header/value logic (current behaviour).
    2) If vessel looks implausible, fall back fully to text regex.
    3) NEW: If voyage is still missing, use text regex to backfill just the voyage.
    """
    results = {"vessel_name": None, "voyage": None}
    if not document.pages:
        return results
        
    page = document.pages[0]
    document_text = document.text

    print("\n--- Running 'Vessel First' Strategy with Universal Helper ---")
    
    vessel_candidate_text = find_value_for_header_final(page, document_text, "Vessel")
    
    if vessel_candidate_text:
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
    else:
        print("Could not find any value for the 'Vessel' keyword using geometry-based search.")

    # --- Existing sanity check: if vessel looks weird, rely fully on regex ---
    if not is_plausible_vessel_name(results["vessel_name"]):
        print("Vessel candidate looks implausible. Falling back to text-based regex extractor...")
        regex_result = extract_bol_vessel_voyage_regex(document)
        if regex_result.get("vessel_name"):
            results["vessel_name"] = regex_result["vessel_name"]
        if regex_result.get("voyage"):
            results["voyage"] = regex_result["voyage"]

    # --- NEW: If voyage is still missing, use regex JUST to backfill voyage ---
    if results["voyage"] is None:
        print("Voyage code still missing after geometry. Trying regex-based voyage extraction...")
        regex_result = extract_bol_vessel_voyage_regex(document)
        # Only overwrite vessel if we never got one from geometry
        if not results["vessel_name"] and regex_result.get("vessel_name"):
            results["vessel_name"] = regex_result["vessel_name"]
        if regex_result.get("voyage") and regex_result["voyage"] != results["vessel_name"]:
            results["voyage"] = regex_result["voyage"]
            print(f"SUCCESS (Regex Voyage Backfill): Vessel='{results['vessel_name']}', Voyage='{results['voyage']}'")
        else:
            print("Regex voyage backfill did not find a valid voyage code.")

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

def extract_bol_consignee_by_regex(document) -> Optional[str]:
    """
    Fallback: extract consignee block purely from text,
    between 'Consignee' and the next 'Notify Party' line.
    """
    text = document.text

    m = re.search(
        r'Consignee[^\n]*\n(.*?)(?=\nNotify Party)',  # stop right before the next 'Notify Party' line
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None

    block = m.group(1).strip()

    # Lines we *never* want in consignee details
    EXCLUDE_PATTERNS = [
        r"As principal, where",   # the exact junk line you showed
        r"care of",               # catches "care of", "c/o", etc if they appear elsewhere
    ]

    cleaned_lines = []
    for ln in block.splitlines():
        s = ln.strip()
        if not s:
            continue

        # Skip any line that matches one of our exclude patterns
        if any(re.search(pat, s, re.IGNORECASE) for pat in EXCLUDE_PATTERNS):
            continue

        cleaned_lines.append(s)

    return "\n".join(cleaned_lines) if cleaned_lines else None


def extend_consignee_with_contact_lines(document, base_consignee: str) -> str:
    """
    Starting from the last line of the extracted consignee block, look
    ahead in the raw document text and append contact lines like:
      - Notify Party-USCI: ...
      - Email: ...

    Stops when it reaches the main Notify Party header or another section.
    """
    if not base_consignee:
        return base_consignee

    text = document.text
    lines = base_consignee.split("\n")
    last_line = lines[-1].strip()
    if not last_line:
        return base_consignee

    # Find where that last line occurs in the linear OCR text
    idx = text.find(last_line)
    if idx == -1:
        # If we can't locate it, just return the original block
        return base_consignee

    # Look ahead a bit after the last line (tune length if needed)
    lookahead = text[idx + len(last_line): idx + len(last_line) + 600]

    extra_lines: list[str] = []
    for raw_line in lookahead.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        upper = line.upper()

        # Stop when we hit the *next* Notify Party header or another big section
        if "NOTIFY PARTY (SEE CLAUSE" in upper or upper.startswith("VESSEL "):
            break

        # Contact lines we want to attach to the consignee
        if upper.startswith("NOTIFY PARTY-USCI") or upper.startswith("NOTIFY PARTY - USCI"):
            extra_lines.append(line)
            continue

        if upper.startswith("EMAIL:"):
            extra_lines.append(line)
            continue

    if extra_lines:
        return base_consignee + "\n" + "\n".join(extra_lines)

    return base_consignee



def extract_bol_notify_by_regex(document) -> Optional[str]:
    """
    Fallback: extract notify party block between
    'Notify Party (see clause 22)' and 'Vessel'.
    """
    text = document.text

    m = re.search(
        r'Notify Party\s*\(see clause 22\)[^\n]*\n(.*?)(?=\nVessel\b|\nVessel\s*\(see|\Z)',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None

    block = m.group(1).strip()
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    return "\n".join(lines) if lines else None

def is_plausible_vessel_name(name: Optional[str]) -> bool:
    """
    Heuristic check to see if a vessel name looks reasonable.
    We reject:
    - Very long sentences
    - Lines containing obviously non-vessel words (payment, preconditions, URLs, etc.)
    """
    if not name:
        return False

    s = name.strip()
    if not s:
        return False

    # Too long ⇒ likely sentence, not a ship name
    if len(s) > 40:
        return False

    junk_keywords = [
        "payment",
        "precondition",
        "preconditions",
        "support",
        "customer",
        "reference(s)",
        "verify copy approval",
        "maersk.com",
        "http",
        "https",
        "details available here",
    ]
    lower_s = s.lower()
    if any(k in lower_s for k in junk_keywords):
        return False

    return True

def extract_bol_vessel_voyage_regex(document) -> Dict[str, Optional[str]]:
    """
    Text-based fallback: extracts vessel & voyage using text only.
    Handles both:
      - 'Vessel (see ...)\\nTOCONAO\\nVoyage No.\\n532N'
      - 'Vessel\\nVoyage No.\\nMAERSK FELIXSTOWE\\n...\\n528N'
    """
    text = document.text
    result = {"vessel_name": None, "voyage": None}

    # --- Vessel name: line between 'Vessel' and 'Voyage' (original logic) ---
    m_vessel_block = re.search(
        r"Vessel[^\n]*\n([^\n]+)\n\s*Voyage",
        text,
        re.IGNORECASE,
    )
    if m_vessel_block:
        vessel_candidate = m_vessel_block.group(1).strip()
        if vessel_candidate:
            result["vessel_name"] = vessel_candidate

    # --- Voyage: try to find a code near the 'Voyage' header ---

    lines = text.splitlines()
    voyage_code: Optional[str] = None

    # Attempt 1: look at the line immediately after 'Voyage No.'
    m_voy = re.search(
        r"Voyage\s*No\.?\s*\n([^\n]+)",
        text,
        re.IGNORECASE,
    )
    if m_voy:
        raw_after_voy = m_voy.group(1).strip()
        voyage_code = find_voyage_code_final(raw_after_voy)

    # Attempt 2: if that failed (e.g. line is 'MAERSK FELIXSTOWE'), scan a small window
    if voyage_code is None:
        idx = None
        for i, line in enumerate(lines):
            if re.search(r"\bVoyage\b", line, re.IGNORECASE):
                idx = i
                break

        if idx is not None:
            # Look at the next ~5 lines after the 'Voyage' header
            window_text = " ".join(lines[idx + 1: idx + 6])
            voyage_code = find_voyage_code_final(window_text)

    if voyage_code:
        result["voyage"] = voyage_code

    return result


def clean_exporter_address_block(block: Optional[str]) -> Optional[str]:
    """
    Remove phone/email/contact lines from the exporter block while
    keeping the actual postal/address + VAT/Reg lines.
    """
    if not block:
        return block

    cleaned_lines = []
    for line in block.splitlines():
        s = line.strip()
        if not s:
            continue

        lower = s.lower()

        # Drop obvious contact / comms lines
        if '@' in s:
            continue
        if lower.startswith("tel") or lower.startswith("tel:"):
            continue
        if lower.startswith("phone") or lower.startswith("phone:"):
            continue
        if lower.startswith("fax") or lower.startswith("fax:"):
            continue
        # Lines that are just a phone number like "+27 ..." etc.
        if re.match(r'^\+?\d', s) and not re.search(r'[A-Za-z]', s):
            continue

        cleaned_lines.append(s)

    return "\n".join(cleaned_lines) if cleaned_lines else None

def extract_bol_shipper_by_regex(document) -> Optional[str]:
    """
    Fallback: extract shipper/exporter block purely from text,
    between the main 'Shipper (...)' header and the next 'Consignee' header.
    """
    text = document.text

    # Capture everything after the Shipper header up to the Consignee header
    m = re.search(
        r"Shipper\s*\(.*?\)[^\n]*\n(.*?)(?=\nConsignee\s*\(|\nNotify Party|\nThis contract is subject)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None

    block = m.group(1).strip()

    # Drop lines we know are not part of the postal address
    EXCLUDE_PREFIXES = [
        r"Booking No\.?", 
        r"Export references",
        r"Svc Contract",
    ]

    cleaned_lines = []
    for ln in block.splitlines():
        s = ln.strip()
        if not s:
            continue

        # Skip noisy lines like "Export references", "Svc Contract", etc.
        if any(re.match(pat, s, re.IGNORECASE) for pat in EXCLUDE_PREFIXES):
            continue

        cleaned_lines.append(s)

    return "\n".join(cleaned_lines) if cleaned_lines else None
