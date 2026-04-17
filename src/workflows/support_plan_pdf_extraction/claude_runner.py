"""Claude ブラウザ自動化呼出ラッパ (最小版)。

既存の browser_reader.read_pdf_via_browser を呼び出して、
PDF ファイルとプロンプトから Claude の応答テキストを得る。

UI 依存ロジックは browser-pdf-test/run_test.py + browser_reader.py に
閉じ込めたまま、本ワークフローは応答テキストを受け取るだけ。
"""

from pathlib import Path

from src.common import get_logger, WorkflowError
from src.tools.pdf_preprocess.browser_reader import read_pdf_via_browser

logger = get_logger(__name__)


def run_claude_on_pdf(pdf_path: Path, prompt: str) -> str:
    """PDF と prompt を Claude (ブラウザ自動化) に渡し、応答テキストを返す。

    Args:
        pdf_path: 処理対象の PDF ファイル。
        prompt: Claude に送信するプロンプト本文。

    Returns:
        Claude の応答テキスト (JSON 文字列想定)。

    Raises:
        WorkflowError: 呼出に失敗した場合。
    """
    logger.info("Running Claude on PDF: %s", pdf_path.name)
    try:
        response = read_pdf_via_browser(str(pdf_path), prompt)
    except Exception as e:
        raise WorkflowError(f"Claude runner failed for {pdf_path.name}: {e}") from e

    logger.info("Claude response length: %d chars", len(response))
    return response
