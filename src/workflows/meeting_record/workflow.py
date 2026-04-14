"""担当者会議録ワークフローモジュール。

指定されたPDFからOCRでテキストを抽出し、Claudeで担当者会議録の
基本情報をJSON形式で構造化抽出し、結果を保存してGoogle Sheetsへ書き込む。
"""

import json
import os
from pathlib import Path

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

        # --- 方式分岐 (PDF_READ_MODE 環境変数で切替。デフォルト: api) ---
        _pdf_read_mode = os.getenv("PDF_READ_MODE", "api")

        if _pdf_read_mode == "browser":
            # ブラウザ方式: PDF を Claude Web UI に渡して直接 JSON 抽出
            logger.info("Browser mode - extracting via Claude Web UI: %s", pdf_name)
            from src.tools.pdf_preprocess.browser_reader import extract_json_via_browser
            result = extract_json_via_browser(pdf_path, MEETING_PROMPT_TEMPLATE)
        else:
            # API 方式 (デフォルト): 既存処理そのまま
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
