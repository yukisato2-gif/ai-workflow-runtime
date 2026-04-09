"""PDF テキスト抽出モジュール。

PyMuPDF (fitz) を使用して PDF からテキストを抽出する。
テキストレイヤを持つ PDF が対象。OCR は行わない。
"""

from pathlib import Path

import fitz  # PyMuPDF

from src.common import get_logger

logger = get_logger(__name__)


def extract_text_from_pdf(pdf_path: str) -> str:
    """PDF ファイルから全ページのテキストを抽出する。

    各ページのテキストを順に結合して返す。
    空ページや抽出失敗ページはログに記録して継続する。
    全ページでテキストが取得できなかった場合のみ RuntimeError を送出する。

    Args:
        pdf_path: PDF ファイルのパス。

    Returns:
        全ページから結合されたテキスト。

    Raises:
        FileNotFoundError: 指定されたファイルが存在しない場合。
        RuntimeError: PDF を開けない場合、または全ページでテキストが空の場合。
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

    doc.close()

    combined_text = "\n\n".join(page_texts)
    total_chars = len(combined_text)

    if total_chars == 0:
        logger.error(
            "No text extracted from any page in %s (%d pages). "
            "This PDF may be image-based (scanned). OCR is not enabled.",
            pdf_path,
            total_pages,
        )
        raise RuntimeError(
            f"No text extracted from PDF: {pdf_path} ({total_pages} pages). "
            "The PDF may be image-based. OCR is not enabled in this version."
        )

    logger.info("Text extraction completed: total %d chars from %d/%d pages", total_chars, len(page_texts), total_pages)
    return combined_text
