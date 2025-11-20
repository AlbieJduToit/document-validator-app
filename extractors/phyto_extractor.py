from typing import Optional, Dict
import re

def get_text(text_anchor: dict, text: str) -> str:
    """
    Document AI's text anchor maps to a part of the full text.
    This function extracts that part of the text.
    """
    if not text_anchor or not text_anchor.text_segments:
        return ""
    
    start_index = int(text_anchor.text_segments[0].start_index)
    end_index = int(text_anchor.text_segments[0].end_index)
    
    return text[start_index:end_index].strip()


def extract_phyto_data(document):
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
        "port_of_destination": None,
        "total_cartons": None,
        "container_number": None,
        "total_gross_mass_kg": None,
        "total_net_mass_kg": None
    }

    extracted_data['exporter_address'] = extract_exporter_address_phyto(document)
    extracted_data["consignee_details"] = extract_consignee_address_phyto(document)
    extracted_data["container_number"] = extract_container_phyto(document)
    extracted_data["port_of_destination"] = extract_point_of_entry(document)
    extracted_data["total_cartons"] = extract_phyto_total_cartons(document)
    weights = extract_phyto_weights(document)
    extracted_data["total_gross_mass_kg"] = weights.get("gross")
    extracted_data["total_net_mass_kg"] = weights.get("net")

    return extracted_data


def find_line_by_substring(page, substring: str, document_text: str):
    """Finds the first line on a page containing a specific substring."""
    for line in page.lines:
        line_text = get_text(line.layout.text_anchor, document_text)
        if substring in line_text:
            return line
    return None


def extract_exporter_address_phyto(document: dict) -> Optional[str]:
    """
    Extracts the exporter address from a Phyto document by defining a robust
    search box between the 'exporter' and 'packages' headers, constrained
    to the left half of the page.
    """
    if not document.pages:
        return None
    
    document_text = document.text

    # --- Step 1: Search all pages for our two reliable anchors ---
    for page in document.pages:
        start_anchor = find_line_by_substring(page, "1. Name and address of exporter", document_text)
        stop_below_anchor = find_line_by_substring(page, "3. Number and Description of Packages", document_text)
        
        # If both anchors are found on THIS page, we've found our target.
        if start_anchor and stop_below_anchor:
            print(f"Found required top and bottom anchors on Page {page.page_number}.")
            
            # --- Step 2: Define the search box ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            
            # Vertical boundaries
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            # Horizontal boundaries - a simple rule for the left half of the page
            search_left_x = 0.0
            search_right_x = 0.5 # Look only in the left 50% of the page
            
            print(f"Defined search box: y=({search_top_y:.3f}, {search_bottom_y:.3f}), x=({search_left_x:.3f}, {search_right_x:.3f})")

            # --- Step 3: Collect lines within the box ---
            address_lines_with_pos = []
            for line in page.lines:
                if line == start_anchor or line == stop_below_anchor:
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
                
                if search_top_y < line_center_y < search_bottom_y and \
                   search_left_x < line_center_x < search_right_x:
                   
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    if line_text:
                        line_top_y = min(v.y for v in line_bbox.normalized_vertices)
                        address_lines_with_pos.append((line_top_y, line_text))

            if not address_lines_with_pos:
                print("No lines found within the defined search box. Checking next page.")
                continue

            address_lines_with_pos.sort()
            final_address = "\n".join([text for _, text in address_lines_with_pos])
            
            print("SUCCESS: Extracted Phyto Exporter Address.")
            return final_address

    print("Could not find both 'Exporter' and 'Packages' anchors on any page.")
    return None

def extract_consignee_address_phyto(document: dict) -> Optional[str]:
    """
    Extracts the consignee address from a pre-cleaned Phyto document by defining
    a robust search box between the 'consignee' and 'marks' headers,
    constrained to the right half of the page.
    """
    if not document.pages:
        return None
    
    document_text = document.text

    # --- Iterate through all pages to find the one with the data ---
    for page in document.pages:
        # --- Step 1 & 2: Find the top and bottom anchors ---
        start_anchor = find_line_by_substring(page, "2. Declared name and address of consignee", document_text)
        stop_below_anchor = find_line_by_substring(page, "4. Distinguishing Marks", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required consignee anchors on Page {page.page_number}.")
            
            # --- Step 3 & 4: Define the search box ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            
            # Vertical boundaries
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            # Horizontal boundaries - a simple rule for the right half of the page
            search_left_x = 0.5 # Start searching from the middle of the page
            search_right_x = 1.0 # Go all the way to the right edge
            
            print(f"Defined search box: y=({search_top_y:.3f}, {search_bottom_y:.3f}), x=({search_left_x:.3f}, {search_right_x:.3f})")

            # --- Step 5: Collect lines within the box ---
            address_lines_with_pos = []
            for line in page.lines:
                if line == start_anchor or line == stop_below_anchor:
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
                
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
            
            print("SUCCESS: Extracted Phyto Consignee Address.")
            return final_address

    print("Could not find both 'Consignee' and 'Marks' anchors on any page.")
    return None

def extract_container_phyto(document: dict) -> Optional[str]:
    """
    Extracts the container number from a Phyto document.

    Strategy:
    1) Try geometry-based extraction under '4. Distinguishing Marks'
       (right half of the page, between '4. Distinguishing Marks' and 'conveyance').
       If we find a container-like code (e.g. TEMU9530408), return it.
       If we only see 'NONE' or no code, fall back.
    2) Fallback: look in the 'Additional Information' line for a container number.
    3) Last resort: first container-like pattern anywhere in the document text.
    """
    if not document.pages:
        return None

    document_text = document.text

    # ------------------
    # 1) GEOMETRIC SEARCH UNDER "Distinguishing Marks"
    # ------------------
    for page in document.pages:
        start_anchor = find_line_by_substring(page, "4. Distinguishing Marks", document_text)
        stop_below_anchor = find_line_by_substring(page, "conveyance", document_text)

        if start_anchor and stop_below_anchor:
            print(f"Found required marks anchors on Page {page.page_number}.")

            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly

            # Vertical boundaries
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)

            # Right half of the page
            search_left_x = 0.5
            search_right_x = 1.0

            print(
                f"Defined search box: y=({search_top_y:.3f}, {search_bottom_y:.3f}), "
                f"x=({search_left_x:.3f}, {search_right_x:.3f})"
            )

            found_lines: list[str] = []
            for line in page.lines:
                if line in (start_anchor, stop_below_anchor):
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (
                    min(v.y for v in line_bbox.normalized_vertices)
                    + max(v.y for v in line_bbox.normalized_vertices)
                ) / 2.0
                line_center_x = (
                    min(v.x for v in line_bbox.normalized_vertices)
                    + max(v.x for v in line_bbox.normalized_vertices)
                ) / 2.0

                if (
                    search_top_y < line_center_y < search_bottom_y
                    and search_left_x < line_center_x < search_right_x
                ):
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    if line_text:
                        found_lines.append(line_text)

            if found_lines:
                combined = " ".join(found_lines)
                print(f"Distinguishing Marks block text: '{combined}'")

                # Try to find a container-like code in the marks block
                m = re.search(r"[A-Z]{4}\d{7}", combined)
                if m:
                    container_number = m.group(0)
                    print(f"SUCCESS: Extracted container from marks block: {container_number}")
                    return container_number

                # If it's literally 'NONE', don't treat that as a container number
                if combined.strip().upper() == "NONE":
                    print("Marks block is 'NONE' – falling back to Additional Information / regex.")
                else:
                    # Non-empty but no container pattern: still fall back
                    print("Marks block has no container-like pattern – falling back.")
            else:
                print("No line found within the marks search box. Checking next page.")
                # continue to next page / fallback

    # ------------------
    # 2) FALLBACK: "Additional Information" LINE
    # ------------------
    # Example line:
    # "15. Additional Information:\nTEMU9530408, SEAL NO: FX35960860"
    m_info = re.search(
        r"Additional Information:\s*([^\n]*)",
        document_text,
        re.IGNORECASE,
    )
    if m_info:
        info_line = m_info.group(1).strip()
        print(f"Found 'Additional Information' line: '{info_line}'")
        m_cont = re.search(r"[A-Z]{4}\d{7}", info_line)
        if m_cont:
            container_number = m_cont.group(0)
            print(f"SUCCESS: Extracted container from Additional Information: {container_number}")
            return container_number

    # ------------------
    # 3) LAST RESORT: FIRST CONTAINER-LIKE PATTERN ANYWHERE
    # ------------------
    m_any = re.search(r"[A-Z]{4}\d{7}", document_text)
    if m_any:
        container_number = m_any.group(0)
        print(f"SUCCESS (fallback): Extracted first container-like pattern: {container_number}")
        return container_number

    print("Could not find a container number in Phyto document.")
    return None


def extract_point_of_entry(document: dict) -> Optional[str]:
    """
    Extracts the point of entry (port of destination) from under its header
    on a pre-cleaned Phyto document.
    """
    if not document.pages:
        return None
    
    document_text = document.text

    # --- Iterate through all pages to find the one with the data ---
    for page in document.pages:
        # --- Step 1 & 2: Find the top and bottom anchors ---
        start_anchor = find_line_by_substring(page, "7. Declared point of entry", document_text)
        # Using "Botanical" as the stop keyword is very reliable
        stop_below_anchor = find_line_by_substring(page, "9. Botanical Name of Plants", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required point of entry anchors on Page {page.page_number}.")
            
            # --- Step 3 & 4: Define the search box ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            
            # Vertical boundaries
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            
            # Horizontal boundaries - a simple rule for the right half of the page
            search_left_x = 0.5 # Start searching from the middle of the page
            search_right_x = 1.0 # Go all the way to the right edge
            
            print(f"Defined search box: y=({search_top_y:.3f}, {search_bottom_y:.3f}), x=({search_left_x:.3f}, {search_right_x:.3f})")

            # --- Step 5: Collect the single line within the box ---
            found_lines = []
            for line in page.lines:
                if line == start_anchor or line == stop_below_anchor:
                    continue

                line_bbox = line.layout.bounding_poly
                line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
                line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
                
                if search_top_y < line_center_y < search_bottom_y and \
                   search_left_x < line_center_x < search_right_x:
                   
                    line_text = get_text(line.layout.text_anchor, document_text).strip()
                    if line_text:
                        found_lines.append(line_text)

            # Return the first (and likely only) line found in the box.
            if found_lines:
                port_of_destination = found_lines[0]
                print(f"SUCCESS: Extracted Point of Entry: {port_of_destination}")
                return port_of_destination
            else:
                print("No line found within the point of entry search box. Checking next page.")
                continue

    print("Could not find both 'Point of Entry' and 'Botanical Name' anchors on any page.")
    return None


def extract_phyto_total_cartons(document: dict) -> Optional[str]:
    """
    Extracts the total cartons by finding the line(s) in the 'Packages'
    section and using a specific regex to find the number preceding 'CARTONS'.
    """
    if not document.pages:
        return None
    
    document_text = document.text

    # Iterate through all pages to find the one with the data
    for page in document.pages:
        # --- Step 1 & 2: Find the top and bottom anchors (unchanged) ---
        start_anchor = find_line_by_substring(page, "3. Number and Description of Packages", document_text)
        stop_below_anchor = find_line_by_substring(page, "5. Place of Origin", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required packages anchors on Page {page.page_number}.")
            
            # --- Step 3: Define the search box (unchanged) ---
            start_bbox = start_anchor.layout.bounding_poly
            stop_below_bbox = stop_below_anchor.layout.bounding_poly
            
            search_top_y = max(v.y for v in start_bbox.normalized_vertices)
            search_bottom_y = min(v.y for v in stop_below_bbox.normalized_vertices)
            search_left_x = 0.0
            search_right_x = 0.5
            
            print(f"Defined search box: y=({search_top_y:.3f}, {search_bottom_y:.3f}), x=({search_left_x:.3f}, {search_right_x:.3f})")

            # --- Step 4: Collect the line(s) within the box (unchanged) ---
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

            # --- Step 5: Parse the number using the new, specific regex ---
            if found_lines:
                full_text = " ".join(found_lines)
                
                # re.IGNORECASE makes it match "CARTONS", "cartons", etc.
                match = re.search(r'(\d+)\s+CARTONS', full_text, re.IGNORECASE)
                
                if match:
                    total_cartons = match.group(1) # The captured number
                    print(f"SUCCESS: Found text '{full_text}' and extracted cartons: {total_cartons}")
                    return total_cartons
                else:
                    print(f"Found text '{full_text}' but could not find the 'number + CARTONS' pattern.")
            else:
                print("No line found within the packages search box. Checking next page.")
                continue

    print("Could not find both 'Packages' and 'Origin' anchors on any page.")
    return None

def extract_phyto_weights(document: dict) -> Dict[str, Optional[str]]:
    """
    Extracts net and gross weights by finding the start and end anchors and
    analyzing the raw text block between them.
    """
    results = {"gross": None, "net": None}
    if not document.pages:
        return results
    
    document_text = document.text

    for page in document.pages:
        # Step 1: Find the start and end anchors
        start_anchor = find_line_by_substring(page, "8. Name of", document_text)
        stop_below_anchor = find_line_by_substring(page, "9. Botanical", document_text)
        
        if start_anchor and stop_below_anchor:
            print(f"Found required weight anchors on Page {page.page_number}.")

            # Step 2: Get indices for the block BETWEEN the two anchors
            start_index = start_anchor.layout.text_anchor.text_segments[0].end_index
            end_index = stop_below_anchor.layout.text_anchor.text_segments[0].start_index

            # Step 3: Extract and normalize the text block
            text_block = document_text[start_index:end_index]
            cleaned = re.sub(r"\s+", " ", text_block).strip()
            print(f" - Analyzing text block: '{cleaned}'")

            # Step 4: Regexes (allow KG or KGS, commas or dots)
            # NETT
            net_match = re.search(
                r'([\d.,]+)\s*KG[S]?\s*NETT',
                cleaned,
                re.IGNORECASE
            )
            if net_match:
                results["net"] = net_match.group(1).replace(",", "")
                print(f"  - Found Net Weight: {results['net']}")

            # GROSS
            gross_match = re.search(
                r'([\d.,]+)\s*KG[S]?\s*GROSS',
                cleaned,
                re.IGNORECASE
            )
            if gross_match:
                results["gross"] = gross_match.group(1).replace(",", "")
                print(f"  - Found Gross Weight: {results['gross']}")

            # If we found at least one, we’re done
            if results["net"] or results["gross"]:
                return results

    print("Could not find both '8. Name of' and '9. Botanical' anchors on any page.")
    return results