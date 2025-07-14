from typing import Optional, Dict
import re
from google.cloud.documentai_v1.types import Document


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

def extract_pl_data(document):
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
        "notify_party_details": None,
        "invoice_party_details": None,
        "container_number": None,
        "vessel_name": None,
        "port_of_destination": None,
        "total_cartons": None,
        "total_gross_mass_kg": None,
        "total_net_mass_kg": None
    }

    extracted_data['exporter_address'] = extract_exporter_address(document)
    extracted_data["consignee_details"] = form_data.get("consignee")
    extracted_data["invoice_party_details"] = form_data.get("invoice party")
    extracted_data["notify_party_details"] = form_data.get("notify party")
    extracted_data["container_number"] = form_data.get("container no:")
    extracted_data["vessel_name"] = form_data.get("vessel:")
    extracted_data["port_of_destination"] = form_data.get("p.o.d:")
    summary_totals = extract_summary_totals(document)
    extracted_data["total_cartons"] = summary_totals.get("cartons")
    extracted_data["total_gross_mass_kg"] = summary_totals.get("gross_weight")
    extracted_data["total_net_mass_kg"] = summary_totals.get("net_weight")

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

def find_line_by_substring(page, substring: str, document_text: str):
    """Finds the first line on a page containing a specific substring."""
    for line in page.lines:
        line_text = get_text(line.layout.text_anchor, document_text)
        if substring in line_text:
            return line
    return None

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
        line_left_x = min(v.x for v in line_bbox.normalized_vertices)
        line_center_x = (min(v.x for v in line_bbox.normalized_vertices) + max(v.x for v in line_bbox.normalized_vertices)) / 2.0
        line_bottom_y = max(v.y for v in line_bbox.normalized_vertices)
        
        # Check if the line is: 1. Above anchor, 2. Right of left boundary, 3. Centered in column
        is_above = line_bottom_y < bottom_anchor_top_y
        respects_left_boundary = line_left_x >= strict_left_boundary_x
        is_centered = abs(line_center_x - column_center_x) < horizontal_tolerance
        
        if is_above and respects_left_boundary and is_centered:
            candidate_lines.append(line)

    if len(candidate_lines) < 2:
        print("Could not find sufficient address lines above 'Reg No'.")
        return get_text(bottom_anchor_line.layout.text_anchor, document_text).strip()

    # --- Step 4 & 5: Sort, prune with gap analysis, and format (No changes needed) ---
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


def find_table_near_line(page, anchor_line):
    """Finds the table entity that is spatially closest to a given anchor line."""
    if not anchor_line: return None
    
    anchor_bottom_y = max(v.y for v in anchor_line.layout.bounding_poly.normalized_vertices)
    
    closest_table = None
    min_distance = float('inf')
    
    for table in page.tables:
        table_top_y = min(v.y for v in table.layout.bounding_poly.normalized_vertices)
        distance = abs(table_top_y - anchor_bottom_y)
        if distance < min_distance:
            min_distance = distance
            closest_table = table
            
    return closest_table



def extract_summary_totals(document: dict) -> dict:
    """
    Finds the "Total:" line,
    finds all adjacent numbers, sorts them by their horizontal position, and
    assigns them based on their known left-to-right order.
    """
    results = { "cartons": None, "gross_weight": None, "net_weight": None }
    if not document.pages: return results
        
    page = document.pages[0]
    document_text = document.text

    # --- Step 1: Find the "Total:" anchor line and its vertical position ---
    # Look for the *last* instance of Total: to be sure we get the summary one.
    all_total_lines = [line for line in page.lines if "Total:" in get_text(line.layout.text_anchor, document_text)]
    if not all_total_lines:
        print("Could not find any 'Total:' lines on the page.")
        return results
    
    total_anchor_line = all_total_lines[-1]
    
    anchor_bbox = total_anchor_line.layout.bounding_poly
    anchor_center_y = (min(v.y for v in anchor_bbox.normalized_vertices) + max(v.y for v in anchor_bbox.normalized_vertices)) / 2.0
    print(f"Found FINAL 'Total:' anchor at vertical center y={anchor_center_y:.3f}")

    # --- Step 2: Find all number-only lines at the same vertical level ---
    number_lines = []
    for line in page.lines:
        if line == total_anchor_line: continue
        line_text = get_text(line.layout.text_anchor, document_text)
        if not re.fullmatch(r'[\d.]+', line_text): continue
        line_bbox = line.layout.bounding_poly
        line_center_y = (min(v.y for v in line_bbox.normalized_vertices) + max(v.y for v in line_bbox.normalized_vertices)) / 2.0
        if abs(line_center_y - anchor_center_y) > 0.015: continue
        number_lines.append(line)
        
    if not number_lines:
        print("Could not find any number lines at the same level as the 'Total:' anchor.")
        return results

    # --- Step 3: Sort the number lines by their horizontal (x) position ---
    number_lines.sort(key=lambda l: min(v.x for v in l.layout.bounding_poly.normalized_vertices))
    
    # Extract the text from the sorted lines
    sorted_values = [get_text(line.layout.text_anchor, document_text) for line in number_lines]
    print(f"Found and sorted values: {sorted_values}")

    # --- Step 4: Assign values based on their known order ---
    # We expect 4 values: Pallets, Cartons, Gross, Net
    if len(sorted_values) >= 4:
        # We don't care about pallets, so we skip index 0
        results["cartons"] = sorted_values[1]
        results["gross_weight"] = sorted_values[2]
        results["net_weight"] = sorted_values[3]
    elif len(sorted_values) == 3:
        results["cartons"] = sorted_values[0]
        results["gross_weight"] = sorted_values[1]
        results["net_weight"] = sorted_values[2]


    print(f"Final results by order: {results}")
    return results