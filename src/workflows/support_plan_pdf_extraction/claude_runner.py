"""Claude ブラウザ自動化呼出ラッパ (新方式)。

新方式 (2026-04 以降):
  PDF 添付ではなく、Chrome で PDF を開いてテキスト抽出し、
  抽出テキストを Claude UI に送信して応答を得る。

旧方式 (read_pdf_via_browser / run_test.py サブプロセス経由) は
browser_reader.py に残しているが、本 workflow からは呼ばない。

上位 (workflow.py) から見たインターフェースは変更しない。
run_claude_on_pdf は従来通り「応答テキスト」を返す。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.common import get_logger, WorkflowError
from src.tools.pdf_preprocess.browser_reader import extract_json_via_text

logger = get_logger(__name__)


def run_claude_on_pdf(pdf_path: Path, prompt: str) -> str:
    """PDF からテキスト抽出 → Claude に送信し、応答 (JSON 文字列) を返す。

    新方式: upload_pdf は使わない。
      1. Chrome (CDP 接続) で PDF を開く
      2. document.body.innerText などでテキスト抽出
      3. 抽出テキストをプロンプトに埋め込んで Claude UI に送信
      4. Claude の応答 JSON を取得

    Args:
        pdf_path: 処理対象の PDF ファイル。
        prompt: Claude に送信するプロンプト本文 (テンプレート)。

    Returns:
        Claude の応答 (JSON を再シリアライズした文字列)。

    Raises:
        WorkflowError: 呼出に失敗した場合。
    """
    logger.info("Running Claude on PDF (text-extract mode): %s", pdf_path.name)
    try:
        parsed = extract_json_via_text(str(pdf_path), prompt)
    except Exception as e:
        raise WorkflowError(f"Claude runner failed for {pdf_path.name}: {e}") from e

    # 上位インターフェース維持: JSON 文字列を返す
    response_text = json.dumps(parsed, ensure_ascii=False)
    logger.info("Claude response length: %d chars", len(response_text))
    return response_text
