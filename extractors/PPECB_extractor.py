from typing import Optional, Dict, List, Any
import re
import sys
from google.cloud import documentai
from google.cloud.documentai_v1.types import Document


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


def extract_ppecb_data(document: documentai.Document) -> Dict[str, Any]:
    """
    Extracts key fields from a Document AI processed invoice.
    Uses a hybrid approach:
    1. Gets all key-value pairs from the Form Parser.
    2. Uses custom logic for fields the parser misses or gets wrong.
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
        "container_number": None,
        "vessel_name": None,
        "voyage": None,
        "port_of_destination": None,
        "total_cartons": None,
        "total_gross_mass_kg": None,
        "total_net_mass_kg": None
    }

    extracted_data["exporter_address"] = extract_exporter_address(document)
    extracted_data["container_number"] = extract_container_numbers(document)
    extracted_data["vessel_name"] = extract_vessel_name_with_regex(document_text)
    page_2_details = extract_voyage_and_port(document)
    extracted_data["voyage"] = extract_voyage_number(document)
    extracted_data["port_of_destination"] = page_2_details.get("port_of_destination")
    extracted_data["total_cartons"] = extract_total_cartons(document_text)
    mass_totals = extract_mass_totals(document_text)
    extracted_data["total_gross_mass_kg"] = mass_totals.get("gross")
    extracted_data["total_net_mass_kg"] = mass_totals.get("net")

    return extracted_data
      
def get_text(text_anchor: dict, text: str) -> str:
    """Extracts text from a Document AI text anchor."""
    if not hasattr(text_anchor, 'text_segments') or not text_anchor.text_segments:
        return ""
    start_index = int(text_anchor.text_segments[0].start_index)
    end_index = int(text_anchor.text_segments[0].end_index)
    return text[start_index:end_index]

def find_block_by_substring(page: Document.Page, substring: str, document_text: str) -> Optional[Document.Page.Block]:
    """Finds the first block on a page containing a specific substring."""
    for block in page.blocks:
        block_text = get_text(block.layout.text_anchor, document_text)
        if substring in block_text:
            return block
    return None

def get_block_bounds(block: Document.Page.Block) -> Optional[Dict[str, float]]:
    """Extracts the bounding box coordinates of a layout block."""
    if not block.layout.bounding_poly or not block.layout.bounding_poly.normalized_vertices:
        return None
    vertices = block.layout.bounding_poly.normalized_vertices
    return {
        'x_min': min(v.x for v in vertices), 'x_max': max(v.x for v in vertices),
        'y_min': min(v.y for v in vertices), 'y_max': max(v.y for v in vertices)
    }

def _find_value_to_right_of_anchor(page: Document.Page, document_text: str, anchor_text: str) -> Optional[str]:
    """
    A generic helper to find the text of the closest block to the right of a given anchor block.
    """
    anchor_block = find_block_by_substring(page, anchor_text, document_text)
    if not anchor_block:
        print(f"      - Anchor '{anchor_text}' not found on this page.")
        return None
        
    anchor_bounds = get_block_bounds(anchor_block)
    if not anchor_bounds: return None

    closest_block = None
    min_distance = sys.float_info.max

    for block in page.blocks:
        if block == anchor_block: continue
        
        candidate_bounds = get_block_bounds(block)
        if not candidate_bounds: continue

        is_to_the_right = candidate_bounds['x_min'] > anchor_bounds['x_max']
        vertical_overlap = max(anchor_bounds['y_min'], candidate_bounds['y_min']) < min(anchor_bounds['y_max'], candidate_bounds['y_max'])

        if is_to_the_right and vertical_overlap:
            distance = candidate_bounds['x_min'] - anchor_bounds['x_max']
            if distance < min_distance:
                min_distance = distance
                closest_block = block
    
    if closest_block:
        return get_text(closest_block.layout.text_anchor, document_text).strip()
    
    return None

def extract_exporter_address(document: Document) -> Optional[str]:
    """
    Extracts the Exporter address by defining a precise search box on the left
    side of the page between two reliable anchors.
    """
    print("\n--- Running Exporter Address Extraction (Hybrid Positional Method) ---")
    if not document.pages:
        return None
        
    page = document.pages[0]
    document_text = document.text
    
    # 1. Find the top and bottom anchors for our vertical slice.
    start_anchor_block = find_block_by_substring(page, "1. Trader", document_text)
    stopper_anchor_block = find_block_by_substring(page, "2. Packer", document_text)
    
    if not start_anchor_block or not stopper_anchor_block:
        print(">>> WARNING: Could not find both start and stop anchors for address.")
        return None
    print("   [✓] Found start and stop anchor blocks.")

    start_anchor_bounds = get_block_bounds(start_anchor_block)
    stopper_bounds = get_block_bounds(stopper_anchor_block)
    if not start_anchor_bounds or not stopper_bounds: return None

    # 2. Define the precise search box (our "sandbox").
    #    It starts below the 'Trader' anchor, stops above the 'Packer' anchor,
    #    and is constrained to the left half of the page.
    search_box = {
        'x_min': 0.0,
        'x_max': 0.5, # <-- CRITICAL: Only search the left half of the page.
        'y_min': start_anchor_bounds['y_max'], # Start after the anchor.
        'y_max': stopper_bounds['y_min']      # Stop before the next section.
    }
    print(f"   [✓] Defined search box: y=({search_box['y_min']:.3f}, {search_box['y_max']:.3f}), x=({search_box['x_min']:.3f}, {search_box['x_max']:.3f})")

    # 3. Collect all text lines whose center point is inside the search box.
    candidate_lines = []
    for block in page.blocks:
        # Check every block on the page.
        block_bounds = get_block_bounds(block)
        if not block_bounds: continue
            
        # Calculate the block's center point
        block_center_x = (block_bounds['x_min'] + block_bounds['x_max']) / 2
        block_center_y = (block_bounds['y_min'] + block_bounds['y_max']) / 2
        
        # Check if the center is inside our defined search box
        if (search_box['x_min'] < block_center_x < search_box['x_max'] and
            search_box['y_min'] < block_center_y < search_box['y_max']):
            
            line_text = get_text(block.layout.text_anchor, document_text).strip()
            # Add the line and its vertical position for sorting later
            if line_text:
                candidate_lines.append((block_bounds['y_min'], line_text))

    if not candidate_lines:
        print(">>> WARNING: No text blocks found in the defined address area.")
        return None

    # 4. Sort the collected lines by their vertical position (top to bottom).
    candidate_lines.sort()
    
    # 5. Clean and assemble the final address.
    #    We extract just the text, filter out noise, and join.
    address_parts = []
    for _, text in candidate_lines:
        # A final filter to remove any junk lines that might have been caught
        if re.search(r'[a-zA-Z0-9]', text) and "serial number" not in text.lower():
            address_parts.append(text)
            
    final_address = "\n".join(address_parts)
    
    print(f"--- Final Address ---\n{final_address}\n---------------------")
    return final_address if final_address else None



def is_valid_container_number(text: str) -> bool:
    """Validates if a string follows the 4-letter, 7-number container format."""
    clean_text = text.strip()
    return (
        len(clean_text) == 11 and
        clean_text[:4].isalpha() and
        clean_text[4:].isdigit()
    )

def extract_container_numbers(document: Document) -> Optional[List[str]]:
    """
    Extracts all valid container numbers found below the 'Container numbers:' anchor
    using a more robust regex-based search on the raw text.
    """
    print("\n--- Running Container Number Extraction (Regex Method) ---")
    document_text = document.text
    
    # Define a regex to find the block of text between the start and a clear stopper.
    # The stopper can be "8. Packages"
    pattern = re.compile(
        r"Container numbers:(.*?)8\.\s*Packages",
        re.DOTALL | re.IGNORECASE
    )
    
    match = pattern.search(document_text)
    
    if not match:
        print(">>> WARNING: Could not find text between 'Container numbers:' and '8. Packages'.")
        return None

    # The text block containing the numbers is in the captured group
    text_block = match.group(1)
    
    # Now find all valid container numbers within that specific block of text
    found_containers = re.findall(r'[A-Z]{4}\d{7}', text_block)
    
    if found_containers:
        print(f"   [✓] Found valid container(s): {found_containers}")
        return found_containers
    else:
        print(">>> No valid container numbers found in the defined area.")
        return None


def extract_vessel_name_with_regex(document_text: str) -> Optional[str]:
    """
    Extracts the vessel name using a regular expression to find the value
    after the 'Vessel:' label.
    """
    print("\n--- Running Vessel Name Extraction (Regex Method) ---")
    
    # Pattern: Find "Vessel:", skip any whitespace, then capture all characters
    # until the end of the line. `re.IGNORECASE` makes it robust.
    # `re.MULTILINE` makes `$` match the end of a line, not just the end of the whole string.
    match = re.search(r"Vessel:\s*(.*)", document_text, re.IGNORECASE)
    
    if match:
        # The captured value is in group(1). Strip any extra whitespace from it.
        vessel_name = match.group(1).strip()
        print(f"   [✓] Found Vessel Name: '{vessel_name}'")
        return vessel_name
    else:
        print(">>> WARNING: Could not find 'Vessel:' pattern in the document.")
        return None
    

def extract_total_cartons(document_text: str) -> Optional[str]:
    """
    Finds all occurrences of '<number> cartons' within the 'Packages' column,
    sums the numbers, and returns the total as a string.
    """
    print("\n--- Running Total Cartons Extraction (with Summation) ---")
    
    # Method 1: Sandbox method (Primary)
    # Isolate the text between the "Packages" and "Type of product" columns.
    sandbox_pattern = re.compile(
        r"8\.\s*Packages.*?:\s*(.*?)9\.\s*Type of product",
        re.DOTALL | re.IGNORECASE
    )
    sandbox_match = sandbox_pattern.search(document_text)

    if sandbox_match:
        text_block = sandbox_match.group(1)
        
        # Use re.findall() to get a list of ALL numbers that are followed by "cartons".
        # Example: '1600 cartons\n280 cartons' -> ['1600', '280']
        numbers_found = re.findall(r'(\d+)\s+cartons', text_block, re.IGNORECASE)
        
        if numbers_found:
            # Convert all found number strings to integers and sum them up.
            total_sum = sum(int(num) for num in numbers_found)
            print(f"   [✓] Found carton entries {numbers_found}. Sum: {total_sum}")
            # Return the final sum as a string.
            return str(total_sum)

    # Method 2: Fallback to the "Total:" line (e.g., on the addendum page)
    # This is a good backup if the primary method fails.
    print("   [!] Primary method failed or found no entries. Trying fallback...")
    fallback_match = re.search(r'Total:\s*(\d+)', document_text, re.IGNORECASE)
    if fallback_match:
        total = fallback_match.group(1)
        print(f"   [✓] Found total cartons using fallback method: '{total}'")
        return total

    print(">>> WARNING: Could not find total cartons using any method.")
    return None


def extract_mass_totals(document_text: str) -> Dict[str, Optional[str]]:
    """
    Finds all net and gross mass entries, sums them separately, and returns
    a dictionary with the totals.
    """
    print("\n--- Running Net/Gross Mass Extraction (with Summation) ---")
    
    # Define the primary search area (sandbox) between headers 11 and 12.
    sandbox_pattern = re.compile(
        r"11\.\s*Total weight.*?net(.*?)12\.\s*This is to certify",
        re.DOTALL | re.IGNORECASE
    )
    
    sandbox_match = sandbox_pattern.search(document_text)
    
    net_total = 0.0
    gross_total = 0.0
    
    if sandbox_match:
        text_block = sandbox_match.group(1)
        
        # This pattern captures the number (group 1) and the type 'net' or 'gross' (group 2).
        # Example: ('24071.00', 'net')
        pattern_for_weights = re.compile(r'([\d.]+)\s*kg\s*\((net|gross)\)', re.IGNORECASE)
        
        matches = pattern_for_weights.findall(text_block)
        
        if matches:
            print(f"   [✓] Found {len(matches)} weight entries in the sandbox.")
            for value_str, type_str in matches:
                try:
                    value_float = float(value_str)
                    if 'net' in type_str.lower():
                        net_total += value_float
                    elif 'gross' in type_str.lower():
                        gross_total += value_float
                except ValueError:
                    print(f"      [!] Skipping invalid number: '{value_str}'")
            # If we found matches here, we trust this result and don't need the fallback.
            return {
                "net": f"{net_total:.2f}" if net_total > 0 else None,
                "gross": f"{gross_total:.2f}" if gross_total > 0 else None
            }

    # Fallback Method: Check the "Total:" line on the addendum page.
    # This typically only provides the net total.
    print("   [!] Primary sandbox method failed or found no entries. Trying fallback...")
    fallback_match = re.search(r'Total:\s*\d+\s*([\d.]+)', document_text, re.IGNORECASE)
    if fallback_match:
        net_value_str = fallback_match.group(1)
        print(f"   [✓] Found net mass using fallback method: '{net_value_str}'")
        return {
            "net": net_value_str,
            "gross": None
        }

    print(">>> WARNING: Could not find mass totals using any method.")
    return {"net": None, "gross": None}


def extract_voyage_number(document: Document) -> Optional[str]:
    """
    Extracts the voyage number by finding a value with both letters and numbers
    that appears near the "Voyage number" text on page 2. This regex
    approach is robust against layout and block variations.
    """
    print("\n--- Running Voyage Number Extraction (Regex Method) ---")
    target_page_text = None

    # 1. Get the text content of ONLY page 2
    if document.pages and len(document.pages) > 1:
        page_2 = document.pages[1]
        if page_2.layout.text_anchor.text_segments:
            start = int(page_2.layout.text_anchor.text_segments[0].start_index)
            end = int(page_2.layout.text_anchor.text_segments[0].end_index)
            target_page_text = document.text[start:end]
    
    if not target_page_text:
        print(">>> WARNING: Could not extract text from page 2.")
        return None
    print("   [✓] Successfully extracted text from page 2.")
    
    # 2. Use a regex to find the value.
    # This pattern looks for "Voyage", then some characters, then "number",
    # and then captures the first word-like sequence that contains both a digit and a letter.
    pattern = re.compile(
        r"Voyage"          # Find the word "Voyage"
        r".*?"             # Match any characters non-greedily
        r"number"          # Find the word "number"
        r"\s*?"            # Match any whitespace
        r"([A-Za-z0-9]*[A-Z][A-Za-z0-9]*\d[A-Za-z0-9]*|[A-Za-z0-9]*\d[A-Za-z0-9]*[A-Z][A-Za-z0-9]*)", # Capture an alphanumeric word with at least one letter and one digit
        re.DOTALL | re.IGNORECASE
    )

    match = pattern.search(target_page_text)
    
    if match:
        # The voyage number is the last captured group.
        voyage_number = match.group(1)
        print(f"   [✓] Found valid voyage number using regex: '{voyage_number}'")
        return voyage_number
    else:
        print(">>> WARNING: Regex pattern for voyage number did not find a match on page 2.")
        return None
    


def extract_voyage_and_port(document: Document) -> Dict[str, Optional[str]]:
    """
    Extracts Voyage and Port of Destination from Page 2 using positional logic.
    """
    print("\n--- Running Page 2 Positional Extraction (Voyage & Port) ---")
    results = {"voyage": None, "port_of_destination": None}
    
    target_page = None
    if document.pages and len(document.pages) > 1:
        target_page = document.pages[1]
    
    if not target_page:
        print(">>> WARNING: Could not find page 2.")
        return results

    document_text = document.text
    
    # Use helper to find the port code
    results["port_of_destination"] = _find_value_to_right_of_anchor(target_page, document_text, "Port of entry")
    
    # For voyage, we still need the more complex logic because the label is split
    # and we need to validate the value.
    voyage_anchor = find_block_by_substring(target_page, "Voyage", document_text)
    number_anchor = find_block_by_substring(target_page, "number", document_text)
    stopper_anchor = find_block_by_substring(target_page, "Producer(s)/ PUC(s)", document_text)
    
    if voyage_anchor and number_anchor and stopper_anchor:
        voyage_bounds = get_block_bounds(voyage_anchor)
        number_bounds = get_block_bounds(number_anchor)
        stopper_bounds = get_block_bounds(stopper_anchor)
        
        if voyage_bounds and number_bounds and stopper_bounds:
            column_x_min = min(voyage_bounds['x_min'], number_bounds['x_min'])
            column_x_max = max(voyage_bounds['x_max'], number_bounds['x_max'])
            
            for block in target_page.blocks:
                block_bounds = get_block_bounds(block)
                if not block_bounds: continue

                is_in_column = (max(column_x_min, block_bounds['x_min']) < min(column_x_max, block_bounds['x_max']) + 0.05)
                is_below_voyage = block_bounds['y_min'] > voyage_bounds['y_max']
                is_above_stopper = block_bounds['y_max'] < stopper_bounds['y_min']

                if is_in_column and is_below_voyage and is_above_stopper:
                    block_text = get_text(block.layout.text_anchor, document_text).strip()
                    if re.search(r'[a-zA-Z]', block_text) and re.search(r'\d', block_text):
                        results["voyage"] = block_text
                        break # Found it, stop searching

    print(f"   [✓] Extracted Port: {results['port_of_destination']}, Voyage: {results['voyage']}")
    return results