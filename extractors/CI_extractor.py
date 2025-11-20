from typing import Optional, Dict, List, Tuple, Any
import re
from google.cloud import documentai


def get_text(text_anchor: dict, text: str) -> str:
    """
    Document AI's text anchor maps to a part of the full text.
    This function extracts that part of the text.
    """
    if not text_anchor.text_segments:
        return ""
    
    start_index = int(text_anchor.text_segments[0].start_index)
    end_index = int(text_anchor.text_segments[0].end_index)
    
    return text[start_index:end_index]


def extract_invoice_data(document: documentai.Document) -> Dict[str, Any]:
    """
    Extracts key fields from a Document AI processed invoice.
    Uses a hybrid approach:
    1. Gets all key-value pairs from the Form Parser.
    2. Uses custom logic for fields the parser misses or gets wrong.
    """
    document_text = document.text
    
    # 1. Create a dictionary from the Form Parser's easy findings
    # This makes lookup much faster than looping every time.
    form_data = {}
    for page in document.pages:
        for field in page.form_fields:
            key = get_text(field.field_name.text_anchor, document_text).strip().lower()
            value = get_text(field.field_value.text_anchor, document_text).strip()
            form_data[key] = value

    # 2. Define the final, clean data structure
    extracted_data = {
        "exporter_address": None,
        "consignee_details": None,
        "notify_party_details": None,
        "invoice_party_details": None,
        "container_number": None,
        "vessel_name": None,
        "voyage": None,
        "port_of_destination": None,
        "total_cartons": None,
        "total_value": None,
        "total_gross_mass_kg": None,
        "total_net_mass_kg": None,
        "banking_details": None
    }

    total_cartons = extract_total_cartons_from_header_text(document) 
    if not total_cartons:
        total_cartons = extract_cartons_spatially_by_header_anchor(document)

    # 3. Populate structure using the form_data and extraction fucntions
    extracted_data["exporter_address"] = extract_exporter_address(document)
    party_details = extract_all_party_details(document)
    extracted_data["consignee_details"] = party_details.get("consignee_details")
    extracted_data["invoice_party_details"] = party_details.get("invoice_party_details")
    extracted_data["notify_party_details"] = party_details.get("notify_party_details")
    extracted_data["container_number"] = form_data.get("container")
    extracted_data["vessel_name"] = form_data.get("vessel")
    extracted_data["voyage"] = form_data.get("voyage")
    extracted_data["port_of_destination"] = form_data.get("port of destination")
    extracted_data["total_value"] = form_data.get("total value:")
    extracted_data["total_cartons"] = total_cartons
    mass_totals = extract_mass_totals_by_regex(document)
    extracted_data["total_gross_mass_kg"] = mass_totals.get("gross")
    extracted_data["total_net_mass_kg"] = mass_totals.get("net")
    extracted_data["banking_details"] = extract_banking_details(document)

    return extracted_data
      
def find_block_by_substring(page, substring: str, document_text: str): 
    """Finds the first block on a page containing a specific substring.""" 
    for block in page.blocks:
        block_text = get_text(block.layout.text_anchor, document_text)
        if substring in block_text:
            return block
    return None

def get_block_center_x(block):
    """Calculates the horizontal center of a block's bounding box."""
    vertices = block.layout.bounding_poly.normalized_vertices
    if not vertices: return 0
    return (min(v.x for v in vertices) + max(v.x for v in vertices)) / 2

      
def extract_exporter_address(document: dict) -> Optional[str]:
    """
    Finds the exporter address by establishing a
    strict left boundary and a flexible center-point alignment based on the
    'Reg No' anchor, then uses gap analysis to find the block's top.
    """
    if not document.pages:
        return None

    page = document.pages[0]
    document_text = document.text

    # --- Step 1: Find the most reliable bottom anchor ---
    bottom_anchor_line = find_line_by_substring(page, "Reg No", document_text)
    if not bottom_anchor_line:
        print("Could not find 'Reg No' anchor line.")
        return None
    
    # --- Step 2: Define a HYBRID boundary based on the anchor ---
    bottom_anchor_bbox = bottom_anchor_line.layout.bounding_poly
    
    # A. The strict left boundary to exclude the logo
    strict_left_boundary_x = min(v.x for v in bottom_anchor_bbox.normalized_vertices) - 0.02 # Add small tolerance
    
    # B. The center of the column for flexible alignment
    column_center_x = (min(v.x for v in bottom_anchor_bbox.normalized_vertices) + max(v.x for v in bottom_anchor_bbox.normalized_vertices)) / 2.0
    horizontal_tolerance = 0.1 # Allow line centers to be within 10% of the page width
    
    bottom_anchor_top_y = min(v.y for v in bottom_anchor_bbox.normalized_vertices)
    print(f"Defined left boundary at x > {strict_left_boundary_x:.3f} and center near x={column_center_x:.3f}")

    # --- Step 3: Gather candidate lines using the hybrid boundary ---
    candidate_lines = [bottom_anchor_line]
    for line in page.lines:
        if line == bottom_anchor_line:
            continue

        line_bbox = line.layout.bounding_poly
        line_center_x = (
            min(v.x for v in line_bbox.normalized_vertices)
            + max(v.x for v in line_bbox.normalized_vertices)
        ) / 2.0
        line_bottom_y = max(v.y for v in line_bbox.normalized_vertices)

        # 1) Above the Reg No line
        is_above = line_bottom_y < bottom_anchor_top_y
        # 2) Reasonably within the same column
        is_centered = abs(line_center_x - column_center_x) < horizontal_tolerance

        if is_above and is_centered:
            candidate_lines.append(line)

    if len(candidate_lines) < 2:
        print("Could not find sufficient address lines above 'Reg No'.")
        return get_text(bottom_anchor_line.layout.text_anchor, document_text).strip()

    # Step 4 & 5: Sort, prune with gap analysis, and format
    candidate_lines.sort(key=lambda l: min(v.y for v in l.layout.bounding_poly.normalized_vertices))
    
    vertical_gap_threshold = 0.015
    final_block_lines = [candidate_lines[-1]]

    for i in range(len(candidate_lines) - 2, -1, -1):
        current_line, line_below = candidate_lines[i], candidate_lines[i+1]
        current_bottom_y = max(v.y for v in current_line.layout.bounding_poly.normalized_vertices)
        below_top_y = min(v.y for v in line_below.layout.bounding_poly.normalized_vertices)
        
        if (below_top_y - current_bottom_y) > vertical_gap_threshold:
            print(f"Detected large vertical gap above line: '{get_text(current_line.layout.text_anchor, document_text).strip()}'")
            break
        
        final_block_lines.append(current_line)
    
    final_block_lines.reverse()
    
    final_text_lines = [get_text(l.layout.text_anchor, document_text).strip() for l in final_block_lines]
             
    return "\n".join(final_text_lines)

    
    

def extract_total_cartons_from_header_text(document: dict) -> Optional[str]:
    """
    Extracts the total cartons by analyzing the full text of the table's
    header section, which is robust against messy OCR joining header lines.
    """
    document_text = document.text

    for page in document.pages:
        for table in page.tables:
            # Step 1: Concatenate all header cell text into one string 
            full_header_text = ""
            for header_row in table.header_rows:
                for cell in header_row.cells:
                    full_header_text += " " + get_text(cell.layout.text_anchor, document_text)

            cleaned_header_text = full_header_text.replace('\n', ' ').strip()
            print(f"\nAnalyzing combined table header text: '{cleaned_header_text}'")

            # Step 2: Check for both patterns in the combined header text
            contains_cartons_keyword = "Cartons" in full_header_text
            
            # Find a number in parentheses
            match = re.search(r'\((\d+)\)', full_header_text)
            
            if contains_cartons_keyword and match:
                total_cartons = match.group(1)
                print(f"SUCCESS: Found 'Cartons' keyword and pattern '({total_cartons})' in header text.")
                return total_cartons

    print("Could not find both 'Cartons' and a value in parentheses in any table header.")
    return None
    
def find_line_by_substring(page, substring: str, document_text: str):
    """Finds the first line on a page containing a specific substring."""
    for line in page.lines:
        line_text = get_text(line.layout.text_anchor, document_text)
        if substring in line_text:
            return line
    return None

def extract_cartons_spatially_by_header_anchor(document: dict) -> Optional[str]:
    """
    Finds the total cartons value by spatially locating the 'Cartons'
    header text and then finding the value in parentheses directly below it.
    This method does NOT rely on the document's table entities.
    """
    if not document.pages:
        return None
        
    page = document.pages[0]
    document_text = document.text

    # Step 1: Find the 'Cartons' header line itself
    cartons_header_line = find_line_by_substring(page, "Cartons", document_text)
    
    if not cartons_header_line:
        print("Could not find a line containing 'Cartons' on the page.")
        return None
        
    # Step 2: Get the header's coordinates to define our search area
    header_bbox = cartons_header_line.layout.bounding_poly
    header_left_x = min(v.x for v in header_bbox.normalized_vertices)
    header_right_x = max(v.x for v in header_bbox.normalized_vertices)
    header_bottom_y = max(v.y for v in header_bbox.normalized_vertices)
    print(f"Found 'Cartons' header line. Searching for value below y={header_bottom_y:.3f} and between x=({header_left_x:.3f}, {header_right_x:.3f})")

    # Step 3: Search all other lines for the value
    for line in page.lines:
        # Don't check the header line itself
        if line == cartons_header_line:
            continue

        line_bbox = line.layout.bounding_poly
        line_top_y = min(v.y for v in line_bbox.normalized_vertices)
        line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0

        # Condition 1: Must be below the header
        is_below = line_top_y > header_bottom_y
        
        # Condition 2: Must be horizontally aligned in the same column
        is_aligned = header_left_x < line_center_x < header_right_x

        if is_below and is_aligned:
            line_text = get_text(line.layout.text_anchor, document_text)
            match = re.search(r'\((\d+)\)', line_text)
            if match:
                total_cartons = match.group(1)
                print(f"SUCCESS: Found aligned line '{line_text.strip()}' and extracted value: {total_cartons}")
                return total_cartons

    print("Could not spatially locate a value in parentheses below the 'Cartons' header line.")
    return None

def extract_mass_totals_by_regex(document: dict) -> Dict[str, Optional[str]]:
    """
    Finds the total gross and net mass by searching the entire document text
    for specific patterns using regular expressions.

    Returns a dictionary with 'gross' and 'net' keys.
    """
    full_text = document.text
    
    gross_mass = None
    net_mass = None

    # --- Define the Regex Patterns ---
    # r'Total Gross Mass \[kg\]' -> Matches the literal text. Brackets must be escaped with \.
    # \s* -> Matches zero or more whitespace characters.
    # ([\d.]+) -> This is the capture group. It matches and captures one or more digits or dots.
    gross_mass_pattern = r'Total Gross Mass \[kg\]\s*([\d.]+)'
    net_mass_pattern = r'Total Net Mass \[kg\]\s*([\d.]+)'

    # Search for Gross Mass
    gross_match = re.search(gross_mass_pattern, full_text)
    if gross_match:
        gross_mass = gross_match.group(1) # group(1) is our captured number
        print(f"SUCCESS: Found Gross Mass using regex: {gross_mass}")
    else:
        print("Could not find Total Gross Mass using regex.")

    # Search for Net Mass
    net_match = re.search(net_mass_pattern, full_text)
    if net_match:
        net_mass = net_match.group(1)
        print(f"SUCCESS: Found Net Mass using regex: {net_mass}")
    else:
        print("Could not find Total Net Mass using regex.")

    return {"gross": gross_mass, "net": net_mass}


def _process_party_column(candidates: List[Tuple[float, str]]) -> Optional[str]:
    """
    Takes a list of (y_pos, text_string) tuples, sorts them,
    and applies vertical gap analysis to get the final, clean text block.
    """
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])

    vertical_gap_threshold = 0.03
    final_lines = [candidates[0][1]]
    last_y_pos = candidates[0][0]

    for i in range(1, len(candidates)):
        current_y_pos, current_text = candidates[i]
        if (current_y_pos - last_y_pos) > vertical_gap_threshold:
            print(f"Detected large vertical gap. Stopping column search.")
            break
        final_lines.append(current_text)
        last_y_pos = current_y_pos
    return "\n".join(final_lines)


def get_char_positions(line, page_tokens: list, document_text: str) -> List[Tuple[str, float]]:
    positions = []
    if not line.layout.text_anchor.text_segments: return positions
    line_start, line_end = line.layout.text_anchor.text_segments[0].start_index, line.layout.text_anchor.text_segments[0].end_index
    line_tokens = [t for t in page_tokens if t.layout.text_anchor.text_segments and t.layout.text_anchor.text_segments[0].start_index >= line_start and t.layout.text_anchor.text_segments[0].end_index <= line_end]
    for token in line_tokens:
        token_text = get_text(token.layout.text_anchor, document_text)
        if not token_text: continue
        bbox = token.layout.bounding_poly
        start_x, end_x = min(v.x for v in bbox.normalized_vertices), max(v.x for v in bbox.normalized_vertices)
        width = end_x - start_x
        for i, char in enumerate(token_text):
            positions.append((char, start_x + (i / len(token_text)) * width if len(token_text) > 0 else 0))
    positions.sort(key=lambda item: item[1])
    return positions


def extract_all_party_details(document: dict) -> Dict[str, Optional[str]]:
    """
    Detects the number of party headers and chooses the appropriate parsing strategy.
    """
    results = {"consignee_details": None, "invoice_party_details": None, "notify_party_details": None}
    if not document.pages: return results
        
    page = document.pages[0]
    document_text = document.text
    page_tokens = page.tokens

    # Step 1: Discover available anchors
    party_keywords = { "consignee_details": "Consignee", "invoice_party_details": "Invoice Party", "notify_party_details": "Notify Party" }
    found_anchors = { key: find_line_by_substring(page, keyword, document_text) for key, keyword in party_keywords.items() }
    present_anchors = sorted([(key, anchor) for key, anchor in found_anchors.items() if anchor], key=lambda item: min(v.x for v in item[1].layout.bounding_poly.normalized_vertices))
    
    if not present_anchors:
        print("No party detail anchors found on the page.")
        return results

    # Initialize the candidate buckets using the final, correct keys
    candidates = {key: [] for key in party_keywords.keys()}
    anchor_bottom_y = max(max(v.y for v in anchor.layout.bounding_poly.normalized_vertices) for _, anchor in present_anchors)

    # Step 2: Branch logic based on the number of headers found

    # LOGIC FOR MULTI-COLUMN LAYOUTS
    if len(present_anchors) >= 2:
        print(f"Detected {len(present_anchors)} headers. Using multi-column re-slicing logic.")
        boundaries = {}
        for i, (key, anchor) in enumerate(present_anchors):
            right_bound = min(v.x for v in present_anchors[i+1][1].layout.bounding_poly.normalized_vertices) if i + 1 < len(present_anchors) else 1.0
            boundaries[key] = {'right': right_bound}
        
        for line in page.lines:
            if min(v.y for v in line.layout.bounding_poly.normalized_vertices) <= anchor_bottom_y: continue
            char_positions = get_char_positions(line, page_tokens, document_text)
            if not char_positions: continue
            
            line_buckets = {key: "" for key, _ in present_anchors}
            for char, x_pos in char_positions:
                # Find the correct bucket for this character
                assigned = False
                for key, bounds in boundaries.items():
                    if x_pos < bounds['right']:
                        line_buckets[key] += char
                        assigned = True
                        break
                if not assigned: # Should only happen for the last column's text
                    line_buckets[present_anchors[-1][0]] += char
            
            for key, text in line_buckets.items():
                 if text.strip(): candidates[key].append((min(v.y for v in line.layout.bounding_poly.normalized_vertices), text.strip()))

    # LOGIC FOR SINGLE-COLUMN LAYOUTS
    elif len(present_anchors) == 1:
        key, anchor = present_anchors[0]
        print(f"Detected 1 header ('{party_keywords[key]}'). Using single-column logic.")
        
        anchor_bbox = anchor.layout.bounding_poly
        column_center_x = (min(v.x for v in anchor_bbox.normalized_vertices) + max(v.x for v in anchor_bbox.normalized_vertices)) / 2.0
        
        for line in page.lines:
            if min(v.y for v in line.layout.bounding_poly.normalized_vertices) <= anchor_bottom_y: continue
            
            line_bbox = line.layout.bounding_poly
            line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
            
            if abs(line_center_x - column_center_x) < 0.2:
                 text = get_text(line.layout.text_anchor, document_text).strip()
                 if text:
                    candidates[key].append((min(v.y for v in line.layout.bounding_poly.normalized_vertices), text))

    # Step 3: Final Processing for all found candidates
    for key, candidate_list in candidates.items():
        if candidate_list:
            print(f"Processing {len(candidate_list)} candidates for {key}")
            results[key] = _process_party_column(candidate_list)
            
    return results


def _extract_banking_details_by_header(document: dict) -> Optional[str]:
    """
    Strategy 1: Finds the 'Banking Details:' header and extracts the text block below it.
    """
    if not document.pages:
        return None
        
    page = document.pages[0]
    document_text = document.text

    anchor_line = find_line_by_substring(page, "Banking Details:", document_text)
    if not anchor_line:
        return None 
        
    anchor_bbox = anchor_line.layout.bounding_poly
    anchor_center_x = (min(v.x for v in anchor_bbox.normalized_vertices) + max(v.x for v in anchor_bbox.normalized_vertices)) / 2.0
    column_tolerance = 0.20
    search_left_x, search_right_x = anchor_center_x - column_tolerance, anchor_center_x + column_tolerance
    anchor_bottom_y = max(v.y for v in anchor_bbox.normalized_vertices)
    print(f"Found 'Banking Details:' anchor. Searching for lines below y={anchor_bottom_y:.3f} and within x=({search_left_x:.3f}, {search_right_x:.3f})")

    candidate_lines_with_pos = []
    for line in page.lines:
        if line == anchor_line or not line.layout.bounding_poly.normalized_vertices: continue
        line_top_y = min(v.y for v in line.layout.bounding_poly.normalized_vertices)
        if line_top_y <= anchor_bottom_y: continue
        
        line_center_x = (min(v.x for v in line.layout.bounding_poly.normalized_vertices) + max(v.x for v in line.layout.bounding_poly.normalized_vertices)) / 2.0
        if search_left_x < line_center_x < search_right_x:
            candidate_lines_with_pos.append((line_top_y, line))

    if not candidate_lines_with_pos: return None
        
    candidate_lines_with_pos.sort(key=lambda item: item[0])

    vertical_gap_threshold = 0.02
    final_lines = []
    last_added_line = candidate_lines_with_pos[0][1]
    final_lines.append(get_text(last_added_line.layout.text_anchor, document_text).strip())
    for i in range(1, len(candidate_lines_with_pos)):
        current_line = candidate_lines_with_pos[i][1]
        last_bottom_y = max(v.y for v in last_added_line.layout.bounding_poly.normalized_vertices)
        current_top_y = min(v.y for v in current_line.layout.bounding_poly.normalized_vertices)
        if (current_top_y - last_bottom_y) > vertical_gap_threshold:
            print("Detected large vertical gap. Stopping Banking Details search.")
            break
        final_lines.append(get_text(current_line.layout.text_anchor, document_text).strip())
        last_added_line = current_line

    return "\n".join(final_lines)


def extract_banking_details(document: dict) -> Optional[str]:
    """
    It first tries a header search. If that fails,
    it finds all banking keywords to define a precise column, then performs a
    bi-directional gap analysis within that column to assemble the full block.
    """
    if not document.pages:
        return None
    page = document.pages[0]
    document_text = document.text

    # Strategy 1: The Fast Path (Header Search)
    print("\n--- Extracting Banking Details ---")
    print("Trying Strategy 1: Searching for 'Banking Details:' header...")
    try:
        # Assuming _extract_banking_details_by_header exists from a previous step.
        details = _extract_banking_details_by_header(document)
        if details:
            print("SUCCESS: Found details using header anchor.")
            return details
    except NameError:
        print("Header search helper not found, proceeding to fallback.")

    # Strategy 2: The Fallback (Precise Column + Gap Analysis)
    print("Header not found. Trying Strategy 2: Precise column search with gap analysis...")
    
    BANKING_KEYWORDS = ["account name", "account number", "swift address", "branch name", "branch code"]
    
    # 1. Find ALL lines that contain any of our keywords.
    anchor_lines = [
        line for line in page.lines
        if any(keyword in get_text(line.layout.text_anchor, document_text).lower() for keyword in BANKING_KEYWORDS)
    ]

    if not anchor_lines:
        print("Could not find any banking keywords to use as anchors.")
        return None
    
    print(f"Found {len(anchor_lines)} banking keyword anchor lines to define the column.")

    # 2. Define a PRECISE column based on the collective width of the anchor lines.
    column_left_x = min(min(v.x for v in line.layout.bounding_poly.normalized_vertices) for line in anchor_lines) - 0.02
    column_right_x = max(max(v.x for v in line.layout.bounding_poly.normalized_vertices) for line in anchor_lines) + 0.02

    # 3. Gather all lines on the page that fall within this precise column.
    candidate_lines = [
        line for line in page.lines
        if column_left_x < ((min(v.x for v in line.layout.bounding_poly.normalized_vertices) + max(v.x for v in line.layout.bounding_poly.normalized_vertices)) / 2.0) < column_right_x
    ]
    
    if not candidate_lines:
        return None

    # 4. Sort candidates and find a "seed" anchor to start our search from.
    candidate_lines.sort(key=lambda l: min(v.y for v in l.layout.bounding_poly.normalized_vertices))
    seed_anchor_line = anchor_lines[0] # The first one we found is a good starting point.
    
    try:
        start_index = candidate_lines.index(seed_anchor_line)
    except ValueError:
        print("Seed anchor was not found in the filtered candidate list. Aborting.")
        return None

    # 5. Perform the bi-directional search with gap analysis on the pre-filtered candidates.
    final_block_lines = [seed_anchor_line]
    vertical_gap_threshold = 0.02

    # Search upwards from the seed
    for i in range(start_index - 1, -1, -1):
        current_line, line_below = candidate_lines[i], candidate_lines[i+1]
        if (min(v.y for v in line_below.layout.bounding_poly.normalized_vertices) - max(v.y for v in current_line.layout.bounding_poly.normalized_vertices)) > vertical_gap_threshold:
            break
        final_block_lines.append(current_line)
    
    # Search downwards from the seed
    last_added_line_in_downward_search = seed_anchor_line
    for i in range(start_index + 1, len(candidate_lines)):
        current_line = candidate_lines[i]
        if (min(v.y for v in current_line.layout.bounding_poly.normalized_vertices) - max(v.y for v in last_added_line_in_downward_search.layout.bounding_poly.normalized_vertices)) > vertical_gap_threshold:
            break
        final_block_lines.append(current_line)
        last_added_line_in_downward_search = current_line
        
    # 6. Final Assembly
    final_block_lines.sort(key=lambda l: min(v.y for v in l.layout.bounding_poly.normalized_vertices))
    
    print("SUCCESS: Assembled banking details block using precise column and gap analysis.")
    return "\n".join([get_text(l.layout.text_anchor, document_text).strip() for l in final_block_lines])

