"""PDF テキスト抽出モジュール。

現在はダミー実装。将来的に pymupdf / pdfplumber 等に差し替え予定。
"""

from pathlib import Path

from src.common import get_logger

logger = get_logger(__name__)


def extract_text_from_pdf(pdf_path: str) -> str:
    """PDF ファイルからテキストを抽出する。

    現在はダミー実装であり、ファイルの存在確認のみ行い固定テキストを返す。

    Args:
        pdf_path: PDF ファイルのパス。

    Returns:
        抽出されたテキスト。

    Raises:
        FileNotFoundError: 指定されたファイルが存在しない場合。
    """
    path = Path(pdf_path)
    if not path.exists():
        logger.error("PDF file not found: %s", pdf_path)
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    logger.info("Extracting text from PDF: %s", pdf_path)

    # TODO: 実際の PDF パースライブラリに差し替える
    dummy_text = (
        "請求書番号: INV-2024-001\n"
        "日付: 2024-01-15\n"
        "金額: 150000\n"
        "取引先: 株式会社サンプル\n"
        "品目: コンサルティングサービス\n"
    )
    logger.info("Text extraction completed (length=%d)", len(dummy_text))
    return dummy_text
