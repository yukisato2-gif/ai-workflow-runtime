"""担当者会議録バッチ処理スクリプト。

指定フォルダ内の全PDFを順番に処理し、
OCR → 抽出 → Google Sheets 書き込みを行う。
重複ファイルは既存の重複防止機能によりスキップされる。

使い方:
    python scripts/batch_meeting.py <フォルダパス>

例:
    python scripts/batch_meeting.py input/meeting_records
"""

import os
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv

from src.common import get_logger
from src.clients.claude import ClaudeClient
from src.workflows.meeting_record import process_single_pdf  # noqa: F401

logger = get_logger(__name__)


def main() -> None:
    """メイン関数。指定フォルダ内のPDFをバッチ処理する。"""
    if len(sys.argv) < 2:
        print("Usage: python scripts/batch_meeting.py <folder_path>")
        sys.exit(1)

    folder_path = Path(sys.argv[1])
    if not folder_path.exists():
        logger.error("Folder not found: %s", folder_path)
        sys.exit(1)

    # 環境変数読み込み
    env_path = _project_root / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")

    if not api_key:
        logger.error("ANTHROPIC_API_KEY is not set")
        sys.exit(1)

    # PDF一覧取得
    pdfs = sorted(folder_path.glob("*.pdf"))
    if not pdfs:
        logger.info("No PDF files found in %s", folder_path)
        return

    logger.info("Batch start: %d PDFs in %s", len(pdfs), folder_path)

    claude_client = ClaudeClient(api_key=api_key, model=model)

    # 処理カウンタ
    success_count = 0
    skip_count = 0
    fail_count = 0

    for pdf in pdfs:
        pdf_str = str(pdf)
        result = process_single_pdf(pdf_str, claude_client)
        if result:
            success_count += 1
        else:
            fail_count += 1

    # サマリ出力
    # skip_count は Sheets 側のログで確認（重複スキップは append 内で処理）
    logger.info(
        "Batch complete: total=%d, success=%d, fail=%d",
        len(pdfs), success_count, fail_count,
    )


if __name__ == "__main__":
    main()
