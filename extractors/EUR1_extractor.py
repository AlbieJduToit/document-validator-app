from typing import Optional, Dict
import re
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


def extract_eur1_data(document):
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
        "vessel_name": None,
        "voyage": None,
        "port_of_destination": None,
        "container_number": None,
        "total_cartons": None,
        "total_gross_mass_kg": None,
        "total_net_mass_kg": None
    }

    extracted_data["exporter_address"] = extract_eur1_exporter_address(document)
    extracted_data["consignee_details"] = extract_eur1_consignee_address(document)
    item_details = extract_eur1_item_details(document)
    extracted_data["total_cartons"] = item_details.get("cartons")
    extracted_data["container_number"] = item_details.get("container_number")
    weights = extract_eur1_weights(document)
    extracted_data["total_gross_mass_kg"] = weights.get("gross")
    extracted_data["total_net_mass_kg"] = weights.get("net")
    transport_details = extract_eur1_transport_details(document)
    extracted_data["vessel_name"] = transport_details.get("vessel_name")
    extracted_data["voyage"] = transport_details.get("voyage")
    extracted_data["port_of_destination"] = transport_details.get("port_of_destination")

    return extracted_data


def find_line_by_substring(page, substring: str, document_text: str):
    """Finds the first line on a page containing a specific substring."""
    for line in page.lines:
        line_text = get_text(line.layout.text_anchor, document_text)
        if substring in line_text:
            return line
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

def extract_eur1_exporter_address(document: dict) -> Optional[str]:
    """
    Extracts the Exporter address from a EUR.1 certificate
    using the correct 'Consignee' anchor as the bottom boundary.
    """
    if not document.pages:
        return None
    
    document_text = document.text

    # --- Iterate through all pages to find the one with the data ---
    for page in document.pages:
        # --- Step 1 & 2: Find the two CORRECT anchors that define the block ---
        start_anchor = find_line_by_substring(page, "1. Exporter", document_text)
        # Using "3. Consignee" is the correct, reliable bottom anchor.
        stop_below_anchor = find_line_by_substring(page, "3. Consignee", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required EUR.1 exporter anchors ('Exporter' and 'Consignee') on Page {page.page_number}.")
            
            # --- Step 3: Define the vertical search box ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            if search_bottom_y <= search_top_y:
                print("Invalid vertical search box calculated. Checking next page.")
                continue

            print(f"Defined vertical search box: y=({search_top_y:.3f}, {search_bottom_y:.3f})")

            # --- Step 4: Collect lines within the slice and on the left half of the page ---
            address_lines_with_pos = []
            for line in page.lines:
                if line in [start_anchor, stop_below_anchor]:
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
                
                # The precise check: in the vertical slice AND on the left half of the page.
                if search_top_y < line_center_y < search_bottom_y and line_center_x < 0.5:
                   
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    # A final filter to exclude the known noisy line
                    if "See notes overleaf" not in line_text:
                        if line_text:
                            line_top_y = min(v.y for v in line_bbox.normalized_vertices)
                            address_lines_with_pos.append((line_top_y, line_text))

            if not address_lines_with_pos:
                print("No lines found within the exporter search area. Checking next page.")
                continue

            address_lines_with_pos.sort()
            final_address = "\n".join([text for _, text in address_lines_with_pos])
            
            print("SUCCESS: Extracted EUR.1 Exporter Address.")
            return final_address

    print("Could not find both 'Exporter' and 'Consignee' anchors on any page.")
    return None


def extract_eur1_consignee_address(document: dict) -> Optional[str]:
    """
    Extracts the Consignee address by defining a
    precise four-sided bounding box using three reliable anchors.
    """
    if not document.pages:
        return None
    
    document_text = document.text

    for page in document.pages:
        # --- Step 1: Find all three anchors to define our precise box ---
        start_anchor = find_line_by_substring(page, "3. Consignee", document_text)
        stop_below_anchor = find_line_by_substring(page, "6. Transport details", document_text)
        # Use "4. Country" as the right-hand wall of our search box
        stop_right_anchor = find_line_by_substring(page, "4. Country, group of", document_text)
        
        if start_anchor and stop_below_anchor and stop_right_anchor:
            print(f"Found all three required consignee anchors on Page {page.page_number}.")
            
            # --- Step 2: Define the PRECISE four-sided bounding box ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            stop_right_bbox = stop_right_anchor.layout.bounding_poly
            
            # Vertical boundaries
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            # Horizontal boundaries
            search_left_x = min(v.x for v in start_bbox.normalized_vertices)
            # The right wall is the LEFT edge of the next column's header
            search_right_x = min(v.x for v in stop_right_bbox.normalized_vertices)
            
            if search_bottom_y <= search_top_y or search_right_x <= search_left_x:
                print("Invalid search box calculated. Checking next page.")
                continue

            print(f"Defined precise search box: y=({search_top_y:.3f}, {search_bottom_y:.3f}), x=({search_left_x:.3f}, {search_right_x:.3f})")

            # --- Step 3: Collect lines with center points inside the box ---
            address_lines_with_pos = []
            for line in page.lines:
                if line in [start_anchor, stop_below_anchor, stop_right_anchor]:
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
                
                # The final, precise check
                if search_top_y < line_center_y < search_bottom_y and \
                   search_left_x < line_center_x < search_right_x:
                   
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    if line_text:
                        line_top_y = min(v.y for v in line_bbox.normalized_vertices)
                        address_lines_with_pos.append((line_top_y, line_text))

            if not address_lines_with_pos:
                print("No lines found within the consignee search box. Checking next page.")
                continue

            address_lines_with_pos.sort()
            final_address = "\n".join([text for _, text in address_lines_with_pos])
            
            print("SUCCESS: Extracted EUR.1 Consignee Address.")
            return final_address

    print("Could not find all three required anchors for consignee on any page.")
    return None


def extract_eur1_item_details(document: dict) -> Dict[str, Optional[str]]:
    """
    Extracts the total cartons (by summing all instances) and the container
    number from the 'Item number' section of a EUR.1 certificate.
    """
    results = {"cartons": None, "container_number": None}
    if not document.pages:
        return results
    
    document_text = document.text

    # --- Iterate through all pages to find the one with the data ---
    for page in document.pages:
        # --- Step 1 & 2: Find the top and bottom anchors ---
        start_anchor = find_line_by_substring(page, "8. Item number", document_text)
        stop_below_anchor = find_line_by_substring(page, "11. CUSTOMS ENDORSEMENT", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required item detail anchors on Page {page.page_number}.")
            
            # --- Step 3: Define the search box for the left column ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            search_left_x = 0.0
            search_right_x = 0.5 # Constrain to left half of the page
            
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

            # --- Step 5: Parse the collected text with regexes ---
            if found_lines:
                full_text = " ".join(found_lines)
                print(f"  - Analyzing text block: '{full_text}'")

                # --- Regex for Cartons (find ALL and sum them) ---
                # re.findall will return a list of all matching numbers, e.g., ['345', '20']
                carton_matches = re.findall(r'(\d+)\s+CARTONS', full_text, re.IGNORECASE)
                if carton_matches:
                    # Convert all found strings to integers and sum them up
                    total_cartons = sum(int(match) for match in carton_matches)
                    results["cartons"] = str(total_cartons) # Store the final sum as a string
                    print(f"  - Found Carton Counts: {carton_matches}. Total: {results['cartons']}")
                
                # --- Regex for Container Number ---
                container_match = re.search(r'[A-Z]{4}\d{7}', full_text)
                if container_match:
                    results["container_number"] = container_match.group(0)
                    print(f"  - Found Container Number: {results['container_number']}")
                
                return results
            else:
                print("No lines found within the item details search box. Checking next page.")
                continue

    print("Could not find both 'Item number' and 'CUSTOMS ENDORSEMENT' anchors on any page.")
    return results


def extract_eur1_weights(document: dict) -> Dict[str, Optional[str]]:
    """
    Finds the vertical region containing the weights
    and uses two independent and robust regexes to find the values.
    """
    results = {"gross": None, "net": None}
    if not document.pages:
        return results
    
    document_text = document.text

    for page in document.pages:
        # Step 1: Find the vertical anchors
        start_anchor = find_line_by_substring(page, "8. Item number", document_text)
        stop_below_anchor = find_line_by_substring(page, "11. CUSTOMS ENDORSEMENT", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required vertical weight anchors on Page {page.page_number}.")
            
            # Step 2: Define the vertical search slice
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            print(f"Defined vertical search slice: y=({search_top_y:.3f}, {search_bottom_y:.3f})")

            # Step 3: Collect ALL lines within the vertical slice
            found_lines = []
            for line in page.lines:
                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                if search_top_y < line_center_y < search_bottom_y:
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    if line_text:
                        found_lines.append(line_text)

            # Step 4: Parse the collected "haystack" of text
            if found_lines:
                full_text = " ".join(found_lines)
                print(f"  - Analyzing combined text block: '{full_text}'")

                # Regex for NETT (this one is reliable and strict)
                net_match = re.search(r'([\d,.]+)\s*KG\s*NETT', full_text, re.IGNORECASE)
                if net_match:
                    results["net"] = net_match.group(1).replace(',', '')
                    print(f"  - Found Net Weight: {results['net']}")

                # A MORE FORGIVING regex for GROSS
                # It looks for a number, then KG, then any characters (.*?), then GROSS.
                gross_match = re.search(r'([\d,.]+)\s*KG.*?\s*GROSS', full_text, re.IGNORECASE)
                if gross_match:
                    results["gross"] = gross_match.group(1).replace(',', '')
                    print(f"  - Found Gross Weight: {results['gross']}")
                
                return results
            else:
                print("No lines found within the vertical slice.")
                continue

    print("Could not find both 'Item number' and 'CUSTOMS ENDORSEMENT' anchors on any page.")
    return results


def extract_eur1_transport_details(document: dict) -> Dict[str, Optional[str]]:
    """
    Extracts transport details from a EUR.1 certificate using robust regex methods
    for all fields to avoid geometric layout issues.
    """
    results = {"vessel_name": None, "voyage": None, "port_of_destination": None}
    if not document.pages:
        return results
    
    document_text = document.text
    
    print("\n--- Running Transport Details Extraction (Robust Regex Method) ---")

    # --- 1. Extract Port of Destination with a simple, direct regex ---
    # This pattern finds the label and captures the rest of that specific line.
    # The `[^\n]` means "match any character that is NOT a newline".
    pod_match = re.search(
        r"PORT OF DISCHARGE:\s*([^\n]*)", 
        document_text, 
        re.IGNORECASE
    )
    
    if pod_match:
        # The captured text is group 1.
        raw_port_text = pod_match.group(1).strip()
        
        # Now, we clean up this line to remove the noise on the far right.
        # We can assume the port name ends before a large gap or a word in all caps like KPOSPPSTIVELY.
        # A simple way is to split by a common delimiter like a comma and take the parts we want.
        # Or, we can stop at the known noise.
        # Let's use a method that stops at the known noise or a big gap.
        
        # This regex splits the string at the first occurrence of 3 or more spaces, or at the known noise word.
        cleaned_port = re.split(r'\s{3,}|KPOSPPSTIVELY', raw_port_text, maxsplit=1)[0].strip()
        
        # A final clean-up to remove any trailing comma.
        results["port_of_destination"] = cleaned_port.rstrip(',')
        print(f"  - SUCCESS: Found Port of Destination: {results['port_of_destination']}")
    else:
        print("  - WARNING: Could not find Port of Destination using line regex.")


    # --- 2. Extract Vessel/Voyage using proven regex ---
    vessel_voy_match = re.search(
        r'VESSEL & VOY:\s*(.*?)\s*PORT OF LOAD', 
        document_text, 
        re.IGNORECASE | re.DOTALL
    )
    if vessel_voy_match:
        vessel_voy_line = vessel_voy_match.group(1).strip()
        print(f"  - Isolated Vessel/Voyage line: '{vessel_voy_line}'")
        
        words = vessel_voy_line.split()
        if words and any(char.isdigit() for char in words[-1]):
            results["voyage"] = words[-1]
            results["vessel_name"] = " ".join(words[:-1])
            print(f"  - SUCCESS: Found Vessel: {results['vessel_name']}")
            print(f"  - SUCCESS: Found Voyage: {results['voyage']}")
        else:
            results["vessel_name"] = vessel_voy_line
    else:
        print("  - WARNING: Could not find Vessel/Voyage pattern.")
            
    return results