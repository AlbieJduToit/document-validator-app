from google.cloud import documentai
from typing import Dict, Any, Optional

def build_text_from_raw_layout(document: documentai.Document) -> str:
    """
    Manually reconstructs the full text by iterating directly through the
    `document.document_layout.blocks` attribute. This is a robust
    workaround that bypasses the empty `.pages` property.
    """
    print("\n[INFO] Starting text reconstruction directly from document_layout...")
    
    # Check if the necessary structure exists
    if not document or not document.document_layout:
        print("[ERROR] The document or its document_layout is empty.")
        return ""

    full_text = []
    
    # The KEY CHANGE: We iterate over document.document_layout.blocks,
    # NOT document.pages.
    for block in document.document_layout.blocks:
        # Check if the block is a simple text block and has text
        if block.text_block and block.text_block.text:
            full_text.append(block.text_block.text)
        
        # Check if the block is a table
        elif block.table_block:
            table_str = ""
            table = block.table_block
            
            # This logic correctly extracts text from tables within the raw layout
            # Format header rows
            for row in table.header_rows:
                # A cell can contain multiple blocks, we'll take the first for simplicity
                cells = [cell.blocks[0].text_block.text.strip() for cell in row.cells if cell.blocks and cell.blocks[0].text_block]
                table_str += " | ".join(cells) + "\n"
            
            # Add a separator for the header
            if table.header_rows and table_str:
                table_str += "-" * len(table_str.splitlines()[-1]) + "\n"
            
            # Format body rows
            for row in table.body_rows:
                cells = [cell.blocks[0].text_block.text.strip() for cell in row.cells if cell.blocks and cell.blocks[0].text_block]
                table_str += " | ".join(cells) + "\n"
            
            full_text.append(table_str)

    print("[INFO] Direct text reconstruction complete.")
    return "\n".join(full_text)


def consolidate_extractions(
    base_extraction: Optional[Dict[str, Any]], 
    agent_fallback: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Merges two extraction results into a single, consolidated dictionary.

    It takes a base extraction and patches any of its null values
    with non-null values from the agent_fallback extraction.

    Args:
        base_extraction: The primary extraction result (e.g., from a Form Parser).
        agent_fallback: The secondary result from the AI agent, used to fill gaps.

    Returns:
        A single, consolidated dictionary, or None if the base is None.
    """
    # If the base extraction failed, there's nothing to consolidate.
    if base_extraction is None:
        return agent_fallback # Or return None, depending on desired behavior

    # If the agent extraction failed, just return the base.
    if agent_fallback is None:
        return base_extraction

    # Create a copy to avoid modifying the original dictionary in place
    consolidated_result = base_extraction.copy()

    print("\n[INFO] Consolidating extraction results...")
    print(f"Base result: {consolidated_result}")
    print(f"Agent fallback result: {agent_fallback}")

    # Iterate through the keys of our primary result
    for key, value in consolidated_result.items():
        # Check if the value is None (this is our trigger to look at the agent's data)
        if value is None:
            # Safely get the corresponding value from the agent's extraction
            # .get() is used to avoid errors if the key doesn't exist in the agent's result
            fallback_value = agent_fallback.get(key)
            
            # If the agent found a valid (non-None) value, use it to patch our result
            if fallback_value is not None:
                print(f"  -> Patching '{key}': Found '{fallback_value}' from agent to replace 'None'.")
                consolidated_result[key] = fallback_value

    print("[INFO] Consolidation complete.")
    return consolidated_result