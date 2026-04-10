"""担当者会議録バッチ処理スクリプト。

指定フォルダ内の全PDFを順番に処理し、
OCR → 抽出 → Google Sheets 書き込みを行う。
重複ファイルは既存の重複防止機能によりスキップされる。

使い方:
    python scripts/batch_meeting.py <フォルダパス>

例:
    python scripts/batch_meeting.py input/meeting_records
"""

import json
import os
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv

from src.common import get_logger
from src.clients.claude import ClaudeClient
from src.tools.pdf_preprocess import extract_text_from_pdf
from src.tools.sheets_writer import append_meeting_result_to_sheet

logger = get_logger(__name__)

# extraction-prompt-meeting.md の完成版プロンプト本文
MEETING_PROMPT_TEMPLATE = """\
以下のテキストは担当者会議録PDFからOCRで抽出したものです。
下記のルールに従い、基本情報をJSON形式で抽出してください。

【抽出する項目】
1. person_name: 利用者名
2. meeting_date: 開催日
3. meeting_time: 開催時間
4. recorder: 記載者
5. location: 開催場所
6. participants: 参加者（読点区切りのリスト）
7. plan_period: 計画期間（開始日～終了日）

【抽出ルール】
- 各項目は帳票上の明示的なラベルに紐づく値のみを抽出してください
- 「開催日」と書かれた日付のみを meeting_date にしてください。「作成日」「記載日」等の日付は絶対に使わないでください
- 「開催時間」と書かれた時間帯のみを meeting_time にしてください
- 「記載者」「作成者」「記録者」と書かれた名前のみを recorder にしてください。署名欄は対象外です
- 「開催場所」と書かれた値のみを location にしてください
- 参加者は帳票記載どおりにリストで返してください。肩書きもそのまま含めてください
- 「計画期間」「実施期間」と書かれた期間のみを plan_period にしてください
- 帳票に記載がない項目は null にしてください
- 推測で値を埋めないでください
- OCRの誤字を勝手に修正しないでください
- 署名・捺印の内容は読み取らないでください
- 上記7項目以外は返さないでください

【判断に迷った場合】
- 複数の候補がある場合や、OCRノイズで正確に読み取れない場合は null にしてください
- その項目名を _review_flags 配列に追加してください

【出力形式】
- JSONのみを返してください
- 説明文は付けないでください
- コードブロック（```json```）で囲まないでください
- 以下のキー構造を必ず守ってください

{{
  "person_name": "利用者名またはnull",
  "meeting_date": "開催日またはnull",
  "meeting_time": "開催時間またはnull",
  "recorder": "記載者またはnull",
  "location": "開催場所またはnull",
  "participants": ["参加者1", "参加者2"],
  "plan_period": "開始日～終了日またはnull",
  "_review_flags": []
}}

【入力テキスト】
{ocr_text}
"""


def process_single_pdf(pdf_path: str, claude_client: ClaudeClient) -> bool:
    """担当者会議録PDF1件を処理する。

    Args:
        pdf_path: PDFファイルのフルパス。
        claude_client: Claude API クライアント。

    Returns:
        True: 正常処理完了、False: 失敗。
    """
    try:
        pdf_name = Path(pdf_path).name
        logger.info("Processing: %s", pdf_name)

        # OCR
        text = extract_text_from_pdf(pdf_path)

        # Claude 抽出
        prompt = MEETING_PROMPT_TEMPLATE.format(ocr_text=text)
        result = claude_client.send_message_json(prompt)

        # result.json 保存
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        result_file = output_dir / "result.json"
        result_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Sheets 追記（重複防止は内部で処理）
        append_meeting_result_to_sheet(pdf_path=pdf_path)

        return True

    except Exception as e:
        logger.error("Failed to process %s: %s", pdf_path, e)
        return False


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
