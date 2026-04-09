"""PDF テキスト抽出モジュール。

PyMuPDF (fitz) を使用して PDF からテキストを抽出する。
テキストレイヤがない画像ベース PDF の場合は、
pytesseract による OCR をフォールバックとして実行する。
"""

import io
import shutil
from pathlib import Path

import fitz  # PyMuPDF

from src.common import get_logger

logger = get_logger(__name__)

# OCR 用の画像解像度 (DPI)。Tesseract 推奨の 300dpi を使用。
OCR_DPI = 300

# OCR で使用する言語。日本語 + 英語。
OCR_LANG = "jpn+eng"


def _check_tesseract_available() -> str:
    """Tesseract OCR が利用可能か確認する。

    Returns:
        Tesseract 実行ファイルのパス。

    Raises:
        RuntimeError: Tesseract が見つからない場合。
    """
    # pytesseract が import できるか確認
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "pytesseract is not installed. Run: pip install pytesseract Pillow"
        )

    # Tesseract 本体が PATH にあるか確認
    tesseract_path = shutil.which("tesseract")
    if tesseract_path:
        return tesseract_path

    # Windows のデフォルトインストール先を確認
    win_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if win_default.exists():
        return str(win_default)

    raise RuntimeError(
        "Tesseract OCR is not installed or not found in PATH. "
        "Install from: https://github.com/UB-Mannheim/tesseract/wiki "
        "and ensure 'tesseract' is on PATH, or install to the default location."
    )


def _ocr_extract_from_pdf(doc: fitz.Document, pdf_path: str) -> str:
    """画像ベース PDF の各ページを OCR でテキスト抽出する。

    PyMuPDF でページを画像化し、pytesseract で OCR を実行する。
    一部ページの失敗は警告ログに記録して継続する。

    Args:
        doc: 開済みの PyMuPDF Document。
        pdf_path: ログ用の PDF ファイルパス。

    Returns:
        全ページの OCR テキストを結合した文字列。

    Raises:
        RuntimeError: Tesseract が利用不可の場合、または全ページで OCR テキストが空の場合。
    """
    import pytesseract
    from PIL import Image

    tesseract_path = _check_tesseract_available()
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    logger.info("Tesseract found: %s", tesseract_path)

    total_pages = len(doc)
    scale = OCR_DPI / 72
    mat = fitz.Matrix(scale, scale)

    page_texts: list[str] = []

    for i in range(total_pages):
        try:
            page = doc[i]
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            text = pytesseract.image_to_string(img, lang=OCR_LANG)
            text = text.strip()
            logger.info("OCR Page %d/%d: extracted %d chars", i + 1, total_pages, len(text))

            if text:
                page_texts.append(text)
            else:
                logger.warning("OCR Page %d/%d: no text recognized", i + 1, total_pages)
        except Exception as e:
            logger.warning("OCR Page %d/%d: failed: %s", i + 1, total_pages, e)

    combined_text = "\n\n".join(page_texts)
    total_chars = len(combined_text)

    if total_chars == 0:
        raise RuntimeError(
            f"OCR produced no text from PDF: {pdf_path} ({total_pages} pages). "
            "The document may be blank or unreadable."
        )

    logger.info(
        "OCR extraction completed: total %d chars from %d/%d pages",
        total_chars, len(page_texts), total_pages,
    )
    return combined_text


def extract_text_from_pdf(pdf_path: str) -> str:
    """PDF ファイルから全ページのテキストを抽出する。

    まず PyMuPDF で通常のテキスト抽出を試みる。
    全ページでテキストが取得できなかった場合のみ、
    OCR によるフォールバック抽出を実行する。

    Args:
        pdf_path: PDF ファイルのパス。

    Returns:
        全ページから結合されたテキスト。

    Raises:
        FileNotFoundError: 指定されたファイルが存在しない場合。
        RuntimeError: PDF を開けない場合、または全手段でテキストが空の場合。
    """
    path = Path(pdf_path)
    if not path.exists():
        logger.error("PDF file not found: %s", pdf_path)
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    logger.info("Extracting text from PDF: %s", pdf_path)

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        logger.error("Failed to open PDF: %s", e)
        raise RuntimeError(f"Failed to open PDF: {pdf_path}") from e

    total_pages = len(doc)
    logger.info("PDF opened successfully (pages=%d)", total_pages)

    # ── 通常テキスト抽出 ──
    page_texts: list[str] = []

    for i in range(total_pages):
        try:
            page = doc[i]
            text = page.get_text() or ""
            text = text.strip()
            logger.info("Page %d/%d: extracted %d chars", i + 1, total_pages, len(text))

            if text:
                page_texts.append(text)
            else:
                logger.warning("Page %d/%d: no text extracted (empty or image-only)", i + 1, total_pages)
        except Exception as e:
            logger.warning("Page %d/%d: extraction failed: %s", i + 1, total_pages, e)

    combined_text = "\n\n".join(page_texts)
    total_chars = len(combined_text)

    if total_chars > 0:
        logger.info("Text extraction completed: total %d chars from %d/%d pages", total_chars, len(page_texts), total_pages)
        doc.close()
        return combined_text

    # ── OCR フォールバック ──
    logger.info(
        "No text layer found in %s (%d pages). Falling back to OCR.",
        pdf_path, total_pages,
    )

    try:
        ocr_text = _ocr_extract_from_pdf(doc, pdf_path)
    finally:
        doc.close()

    return ocr_text
