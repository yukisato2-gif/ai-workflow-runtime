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
        "モニタリング記録\n"
        "\n"
        "利用者氏名: 山田太郎\n"
        "実施日: 2024年6月15日\n"
        "参加者: 山田太郎（本人）、田中花子（サービス管理責任者）、佐藤次郎（相談支援専門員）\n"
        "次回モニタリング時期: 2024年9月\n"
        "モニタリング実施者: 佐藤次郎\n"
        "\n"
        "【生活状況】\n"
        "日中活動は安定して参加できている。体調面も良好。\n"
        "【サービス利用状況】\n"
        "計画通りのサービスを利用中。特に変更の希望なし。\n"
        "【総合的な援助の方針】\n"
        "現行プランを継続し、次回モニタリングで再評価する。\n"
    )
    logger.info("Text extraction completed (length=%d)", len(dummy_text))
    return dummy_text
