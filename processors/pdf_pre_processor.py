import fitz  # PyMuPDF
import cv2
import numpy as np
from typing import List, Optional


def _clean_single_image(image_object: np.ndarray, threshold: int = 210) -> Optional[np.ndarray]:
    """
    Private helper function to clean one image.
    Converts to grayscale and applies a binary threshold to remove light watermarks.
    
    Args:
        image_object: An OpenCV image (numpy array).
        threshold: The pixel intensity value (0-255) to use for thresholding.
                   Pixels > threshold become white, <= threshold become black.
                   
    Returns:
        A cleaned, black-and-white OpenCV image (numpy array).
    """
    if image_object is None:
        return None
        
    try:
        # 1. Convert the image to grayscale
        gray_image = cv2.cvtColor(image_object, cv2.COLOR_BGR2GRAY)
        
        # 2. Apply a binary threshold to create a high-contrast black & white image
        _, thresholded_image = cv2.threshold(gray_image, threshold, 255, cv2.THRESH_BINARY)
        
        return thresholded_image
    except cv2.error as e:
        print(f"OpenCV error during image cleaning: {e}")
        # If cleaning fails (e.g., on an already grayscale image), return the original grayscale
        if len(image_object.shape) == 2: return image_object
        return cv2.cvtColor(image_object, cv2.COLOR_BGR2GRAY)


def preprocess_pdf_for_ocr(
    pdf_bytes: bytes, 
    dpi: int = 300, 
    threshold: int = 100
) -> List[bytes]:
    """
    Main microservice function. Takes raw PDF bytes, cleans each page to remove
    watermarks, and returns a list of cleaned image bytes ready for OCR.

    Args:
        pdf_bytes: The raw byte content of the PDF file.
        dpi: The resolution to render the PDF pages at. Higher is better for OCR.
        threshold: The threshold for watermark removal (0-255).

    Returns:
        A list where each item is the byte content of a cleaned PNG image.
        Returns an empty list if an error occurs.
    """
    print("Starting PDF pre-processing...")
    cleaned_image_bytes_list = []

    try:
        # Open the PDF from the byte stream
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        print(f"PDF has {len(doc)} page(s). Beginning conversion and cleaning.")

        for page_num, page in enumerate(doc):
            print(f"  - Processing page {page_num + 1}...")
            
            # 1. Render page to a high-resolution pixmap (image)
            pix = page.get_pixmap(dpi=dpi)
            
            # 2. Convert to an OpenCV-compatible format (numpy array)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            if pix.n == 4: # Handle RGBA -> BGR
                 img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif pix.n == 3: # Handle RGB -> BGR
                 img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

            # 3. Clean the image using our helper function
            cleaned_image = _clean_single_image(img, threshold)
            
            if cleaned_image is not None:
                # 4. Encode the cleaned image as PNG bytes and add to our list
                is_success, encoded_buffer = cv2.imencode(".png", cleaned_image)
                if is_success:
                    cleaned_image_bytes_list.append(encoded_buffer.tobytes())
                else:
                    print(f"    - Warning: Failed to encode cleaned page {page_num + 1}.")

        doc.close()
        print("PDF pre-processing complete.")
        
    except Exception as e:
        print(f"An error occurred during PDF pre-processing: {e}")
        return []
        
    return cleaned_image_bytes_list