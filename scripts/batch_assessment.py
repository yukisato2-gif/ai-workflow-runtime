"""アセスメントバッチ処理スクリプト。

指定フォルダ内の全PDFを順番に処理し、
OCR → 抽出 → Google Sheets 書き込みを行う。

使い方:
    python scripts/batch_assessment.py <フォルダパス>
"""

import json
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv

from src.common import get_logger
from src.clients.claude import ClaudeClient
from src.tools.pdf_preprocess import extract_text_from_pdf
from src.tools.sheets_writer import append_assessment_result_to_sheet

logger = get_logger(__name__)

ASSESSMENT_PROMPT_TEMPLATE = """\
以下のテキストはアセスメントPDFからOCRで抽出したものです。
下記のルールに従い、基本情報をJSON形式で抽出してください。

【抽出する項目】
1. date: 日付（作成日・実施日）
2. person_name: 利用者名
3. home_name: ホーム名（グループホーム名・事業所名）

【抽出ルール】
- 各項目は帳票上の明示的なラベルに紐づく値のみを抽出してください
- 「日付」「作成日」「実施日」と書かれた日付を date にしてください
- 「利用者名」「氏名」「対象者」と書かれた名前を person_name にしてください
- 「ホーム名」「グループホーム名」「事業所名」と書かれた値を home_name にしてください
- 帳票に記載がない項目は null にしてください
- 推測で値を埋めないでください
- OCRの誤字を勝手に修正しないでください
- 署名・捺印の内容は読み取らないでください
- 上記3項目以外は返さないでください

【出力形式】
- JSONのみを返してください
- 説明文は付けないでください
- コードブロックで囲まないでください

{{
  "date": null,
  "person_name": null,
  "home_name": null
}}

【入力テキスト】
{ocr_text}
"""


def process_single_pdf(pdf_path: str, claude_client: ClaudeClient) -> bool:
    """アセスメントPDF1件を処理する。"""
    try:
        pdf_name = Path(pdf_path).name
        logger.info("Processing: %s", pdf_name)

        text = extract_text_from_pdf(pdf_path)
        prompt = ASSESSMENT_PROMPT_TEMPLATE.format(ocr_text=text)
        result = claude_client.send_message_json(prompt)

        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        Path("output/result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        append_assessment_result_to_sheet(pdf_path=pdf_path)
        return True

    except Exception as e:
        logger.error("Failed to process %s: %s", pdf_path, e)
        return False


def main() -> None:
    """メイン関数。"""
    if len(sys.argv) < 2:
        print("Usage: python scripts/batch_assessment.py <folder_path>")
        sys.exit(1)

    folder_path = Path(sys.argv[1])
    if not folder_path.exists():
        logger.error("Folder not found: %s", folder_path)
        sys.exit(1)

    env_path = _project_root / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")

    if not api_key:
        logger.error("ANTHROPIC_API_KEY is not set")
        sys.exit(1)

    pdfs = sorted(folder_path.glob("*.pdf"))
    if not pdfs:
        logger.info("No PDF files found in %s", folder_path)
        return

    logger.info("Batch start: %d PDFs in %s", len(pdfs), folder_path)
    claude_client = ClaudeClient(api_key=api_key, model=model)

    success_count = 0
    fail_count = 0

    for pdf in pdfs:
        if process_single_pdf(str(pdf), claude_client):
            success_count += 1
        else:
            fail_count += 1

    logger.info("Batch complete: total=%d, success=%d, fail=%d", len(pdfs), success_count, fail_count)


if __name__ == "__main__":
    main()
