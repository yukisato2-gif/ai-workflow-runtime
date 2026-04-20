"""Claude ブラウザ自動化呼出ラッパ。

PDF 添付方式 (read_pdf_via_browser / run_test.py サブプロセス経由) を使用。
browser_reader.py の既存実装へ委譲するだけの薄いラッパ。

上位 (workflow.py) から見たインターフェース: 応答テキストを str で返す。
"""

from __future__ import annotations

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
    logger.info("Running Claude on PDF (upload mode): %s", pdf_path.name)
    try:
        response = read_pdf_via_browser(str(pdf_path), prompt)
    except Exception as e:
        raise WorkflowError(f"Claude runner failed for {pdf_path.name}: {e}") from e

    logger.info("Claude response length: %d chars", len(response))
    return response
