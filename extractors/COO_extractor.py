from typing import Optional, Dict
import re

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


def extract_coo_data(document):
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
        "consignee_details": None,
        "container_number": None,
        "total_cartons": None,
        "total_gross_mass_kg": None
    }

    extracted_data["exporter_address"] = extract_coo_consignor_address(document)
    extracted_data["consignee_details"] = extract_coo_consignee_address(document)
    item_details = extract_coo_item_details(document)
    extracted_data["total_cartons"] = item_details.get("cartons")
    extracted_data["container_number"] = item_details.get("container_number")
    extracted_data["total_gross_mass_kg"] = extract_coo_gross_mass(document)

    return extracted_data


def find_line_by_substring(page, substring: str, document_text: str):
    """Finds the first line on a page containing a specific substring."""
    for line in page.lines:
        line_text = get_text(line.layout.text_anchor, document_text)
        if substring in line_text:
            return line
    return None

def extract_coo_consignor_address(document: dict) -> Optional[str]:
    """
    Extracts the Consignor (Exporter) address from a Certificate of Origin
    using a robust two-anchor vertical slice and a simple horizontal filter.
    """
    if not document.pages:
        return None
    
    document_text = document.text

    # --- Iterate through all pages to find the one with the data ---
    for page in document.pages:
        # --- Step 1 & 2: Find the two most reliable anchors ---
        start_anchor = find_line_by_substring(page, "1 Consignor", document_text)
        # On this document, the "Consignee" block is the next one down.
        stop_below_anchor = find_line_by_substring(page, "2 Consignee", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required COO anchors on Page {page.page_number}.")
            
            # --- Step 3: Define the vertical search box ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            if search_bottom_y <= search_top_y:
                print("Invalid vertical search box calculated. Checking next page.")
                continue

            print(f"Defined vertical search box: y=({search_top_y:.3f}, {search_bottom_y:.3f})")

            # --- Step 4: Collect lines within the box, then filter horizontally ---
            address_lines_with_pos = []
            for line in page.lines:
                if line in [start_anchor, stop_below_anchor]:
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
                
                # Check if the line is in our vertical slice AND on the left half of the page
                if search_top_y < line_center_y < search_bottom_y and line_center_x < 0.5:
                   
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    if line_text:
                        line_top_y = min(v.y for v in line_bbox.normalized_vertices)
                        address_lines_with_pos.append((line_top_y, line_text))

            if not address_lines_with_pos:
                print("No lines found within the consignor search area. Checking next page.")
                continue

            address_lines_with_pos.sort()
            # Post-processing to remove the stray numbers at the end
            full_address = "\n".join([text for _, text in address_lines_with_pos])
            
            # The last two lines are often stray codes, we can try to remove them if they are purely numeric
            address_lines = full_address.split('\n')
            if len(address_lines) > 2 and address_lines[-1].isdigit():
                address_lines.pop()
            if len(address_lines) > 2 and address_lines[-1].isdigit():
                address_lines.pop()
            
            final_address = "\n".join(address_lines)
            
            print("SUCCESS: Extracted COO Consignor Address.")
            return final_address

    print("Could not find both 'Consignor' and 'Consignee' anchors on any page.")
    return None


def extract_coo_consignee_address(document: dict) -> Optional[str]:
    """
    Extracts the Consignee address using a two-anchor
    vertical slice and a simple "left-half" horizontal filter.
    """
    if not document.pages:
        return None
    
    document_text = document.text

    for page in document.pages:
        # Step 1: Find the top and bottom anchors
        start_anchor = find_line_by_substring(page, "2 Consignee", document_text)
        # "4 Transport details" is the correct stop anchor for this block
        stop_below_anchor = find_line_by_substring(page, "4 Transport details", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required COO consignee anchors ('Consignee' and 'Transport') on Page {page.page_number}.")
            
            # Step 2: Define the vertical search box
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            if search_bottom_y <= search_top_y:
                continue

            print(f"Defined vertical search box: y=({search_top_y:.3f}, {search_bottom_y:.3f})")

            # Step 3: Collect lines within the vertical slice AND the left column
            address_lines_with_pos = []
            for line in page.lines:
                if line in [start_anchor, stop_below_anchor]:
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
                
                # Use the exact same logic that worked for the Consignor
                if search_top_y < line_center_y < search_bottom_y and line_center_x < 0.5:
                   
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    if line_text:
                        line_top_y = min(v.y for v in line_bbox.normalized_vertices)
                        address_lines_with_pos.append((line_top_y, line_text))

            if not address_lines_with_pos:
                continue

            address_lines_with_pos.sort()
            final_address = "\n".join([text for _, text in address_lines_with_pos])
            
            print("SUCCESS: Extracted COO Consignee Address.")
            return final_address

    print("Could not find both 'Consignee' and 'Transport' anchors on any page.")
    return None


def extract_coo_item_details(document: dict) -> Dict[str, Optional[str]]:
    """
    Extracts the carton count and container number from the 'Item number' section
    of a Certificate of Origin.
    """
    results = {"cartons": None, "container_number": None}
    if not document.pages:
        return results
    
    document_text = document.text

    # --- Iterate through all pages to find the one with the data ---
    for page in document.pages:
        # --- Step 1 & 2: Find the top and bottom anchors ---
        start_anchor = find_line_by_substring(page, "6 Item number", document_text)
        stop_below_anchor = find_line_by_substring(page, "8 The undersigned authority", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required item detail anchors on Page {page.page_number}.")
            
            # --- Step 3: Define the search box ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            # --- Step 4: Collect all lines within the box ---
            found_lines = []
            for line in page.lines:
                if line in [start_anchor, stop_below_anchor]:
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                
                if search_top_y < line_center_y < search_bottom_y:
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    if line_text:
                        found_lines.append(line_text)

            # --- Step 5: Parse the collected text with two regexes ---
            if found_lines:
                full_text = " ".join(found_lines)
                print(f"  - Analyzing text block: '{full_text}'")

                # Regex 1: Find the number preceding "CARTONS"
                # Allows for decimals in the number
                cartons_match = re.search(r'([\d.]+)\s+CARTONS', full_text, re.IGNORECASE)
                if cartons_match:
                    # Strip off the .00 if it exists
                    results["cartons"] = cartons_match.group(1).split('.')[0]
                    print(f"  - Found Cartons: {results['cartons']}")

                # Regex 2: Find a standard container number (4 letters, 7 numbers)
                # [A-Z]{4} -> Matches exactly 4 uppercase letters
                # \d{7} -> Matches exactly 7 digits
                container_match = re.search(r'[A-Z]{4}\d{7}', full_text)
                if container_match:
                    results["container_number"] = container_match.group(0)
                    print(f"  - Found Container Number: {results['container_number']}")
                
                return results
            else:
                print("No lines found within the item details search box. Checking next page.")
                continue

    print("Could not find both 'Item number' and 'Undersigned authority' anchors on any page.")
    return results


def extract_coo_gross_mass(document: dict) -> Optional[str]:
    """
    Extracts the gross mass from the 'Quantity' section of a Certificate of Origin.
    """
    if not document.pages:
        return None
    
    document_text = document.text

    # --- Iterate through all pages to find the one with the data ---
    for page in document.pages:
        # --- Step 1 & 2: Find the top and bottom anchors ---
        start_anchor = find_line_by_substring(page, "7 Quantity", document_text)
        stop_below_anchor = find_line_by_substring(page, "8 The undersigned authority", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required quantity anchors on Page {page.page_number}.")
            
            # --- Step 3: Define the search box ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            # This section is in the right half of the page
            search_left_x = 0.5
            search_right_x = 1.0
            
            # --- Step 4: Collect all lines within the box ---
            found_lines = []
            for line in page.lines:
                if line in [start_anchor, stop_below_anchor]:
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0

                if search_top_y < line_center_y < search_bottom_y and \
                   search_left_x < line_center_x < search_right_x:
                   
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    if line_text:
                        found_lines.append(line_text)

            # --- Step 5: Parse the number from the collected text ---
            if found_lines:
                full_text = " ".join(found_lines)
                print(f"  - Analyzing text block: '{full_text}'")
                
                # The flexible regex to find the number associated with "GROSS"
                match = re.search(r'([\d\s.,]+)\s*KGS?\s*GROSS', full_text, re.IGNORECASE)
                
                if match:
                    raw_number = match.group(1).strip()
                    # Remove spaces and commas to normalise thousand separators
                    gross_mass = raw_number.replace(" ", "").replace(",", "")
                    print(f"SUCCESS: Extracted Gross Mass: {gross_mass}")
                    return gross_mass
                else:
                    print(f"Could not find the 'number + GROSS' pattern in '{full_text}'.")
            else:
                print("No lines found within the quantity search box. Checking next page.")
                continue

    print("Could not find both 'Quantity' and 'Undersigned authority' anchors on any page.")
    return None