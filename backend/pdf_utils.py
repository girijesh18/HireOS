import io
from pypdf import PdfReader
from loguru import logger

def extract_text_from_pdf(file_content: bytes) -> str:
    """Extract text from a PDF file using pypdf."""
    try:
        reader = PdfReader(io.BytesIO(file_content))
        text = ""
        for page in reader.pages:
            content = page.extract_text()
            if content:
                text += content + "\n"
        return text.strip()
    except Exception as e:
        logger.error(f"Failed to extract text from PDF: {e}")
        return ""
