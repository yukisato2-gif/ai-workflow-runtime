"""モニタリング記録抽出ワークフローモジュール。

PDF からテキストを抽出し、Claude でモニタリング記録の基本情報を
JSON として構造化抽出し、バリデーションを経て結果を保存する。

プロンプトは cowork-assets 側の extraction-prompt.md に定義された
完成版プロンプト本文を使用する。
"""

import json
from pathlib import Path

from src.common import get_logger, WorkflowError
from src.clients.claude import ClaudeClient
from src.tools.pdf_preprocess import extract_text_from_pdf
from src.rules import validate_monitoring_record
from src.schemas import MonitoringRecord
from src.tools.sheets_writer import append_result_to_sheet

logger = get_logger(__name__)

# extraction-prompt.md の「完成版プロンプト本文」をそのまま転用
EXTRACTION_PROMPT_TEMPLATE = """\
以下のテキストはモニタリング記録PDFからOCRで抽出したものです。
下記のルールに従い、基本情報をJSON形式で抽出してください。

【抽出する項目】
1. person_name: モニタリング対象者（利用者の氏名）
2. implementation_date: 実施日
3. participants: 参加者（読点区切りのリスト）
4. implementation_period: 計画実施期間（開始日～終了日）
5. next_monitoring_date: 次回モニタリング時期

【抽出ルール】
- 各項目は帳票上の明示的なラベルに紐づく値のみを抽出してください
- 「実施日」と書かれた日付のみを implementation_date にしてください
- 「計画実施期間」と書かれた期間のみを implementation_period にしてください
- 参加者は帳票記載どおりにリストで返してください
- 帳票に記載がない項目は null にしてください
- 推測で値を埋めないでください
- 署名・捺印の内容は読み取らないでください
- 上記5項目以外は返さないでください

【判断に迷った場合】
- 複数の候補がある場合や、OCRノイズで正確に読み取れない場合は null にしてください
- その項目名を _review_flags 配列に追加してください

【出力形式】
- JSONのみを返してください
- 説明文は付けないでください
- コードブロック（```json```）で囲まないでください
- 以下のキー構造を必ず守ってください

{{
  "person_name": "氏名またはnull",
  "implementation_date": "実施日またはnull",
  "participants": ["参加者1", "参加者2"] ,
  "implementation_period": "開始日～終了日またはnull",
  "next_monitoring_date": "次回時期またはnull",
  "_review_flags": []
}}

【入力テキスト】
{ocr_text}
"""

OUTPUT_DIR = Path("output")
OUTPUT_FILE = OUTPUT_DIR / "result.json"


def run_monitoring_record_workflow(pdf_path: str, claude_client: ClaudeClient) -> MonitoringRecord:
    """モニタリング記録抽出ワークフローを実行する。

    STEP1: PDF テキスト抽出
    STEP2: Claude でモニタリング記録の基本情報を JSON 抽出
    STEP3: Pydantic バリデーション
    STEP4: 結果を output/result.json に保存

    Args:
        pdf_path: 処理対象の PDF ファイルパス。
        claude_client: Claude API クライアント。

    Returns:
        バリデーション済みのモニタリング記録。

    Raises:
        WorkflowError: ワークフロー実行中にエラーが発生した場合。
    """
    try:
        # STEP1: PDF テキスト抽出
        logger.info("STEP1: Extracting text from PDF")
        raw_text = extract_text_from_pdf(pdf_path)

        # STEP2: Claude でモニタリング記録の基本情報を JSON 抽出
        logger.info("STEP2: Sending text to Claude for monitoring record extraction")
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(ocr_text=raw_text)
        extracted_json = claude_client.send_message_json(prompt)

        # extraction-prompt.md のキーとスキーマの差分を吸収
        # プロンプトが返さないフィールドを補完
        extracted_json.setdefault("document_type", "モニタリング記録")
        extracted_json.setdefault("author", None)
        extracted_json.setdefault("confidence", 0.9 if not extracted_json.get("_review_flags") else 0.5)

        # 完全な抽出結果を保存（fields.yaml 準拠の全キーを含む）
        full_result = {
            "person_name": extracted_json.get("person_name"),
            "implementation_date": extracted_json.get("implementation_date"),
            "participants": extracted_json.get("participants"),
            "implementation_period": extracted_json.get("implementation_period"),
            "next_monitoring_date": extracted_json.get("next_monitoring_date"),
            "_review_flags": extracted_json.get("_review_flags", []),
        }

        # Pydantic モデルに変換（型・範囲の自動検証）
        record = MonitoringRecord(**extracted_json)

        # STEP3: ビジネスルールによるバリデーション
        logger.info("STEP3: Validating monitoring record")
        validate_monitoring_record(record)

        # 抽出結果をログ出力
        logger.info(
            "Extraction result:\n%s",
            json.dumps(full_result, ensure_ascii=False, indent=2),
        )

        # STEP4: 結果を output/result.json に保存（fields.yaml 準拠の全キーを含む）
        logger.info("STEP4: Saving result to %s", OUTPUT_FILE)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(
            json.dumps(full_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Result saved successfully")

        # STEP5: Google Sheets に追記（失敗してもワークフロー自体は成功扱い）
        logger.info("STEP5: Appending result to Google Sheets")
        append_result_to_sheet(pdf_path=pdf_path)

        return record

    except Exception as e:
        logger.error("Workflow failed: %s", e)
        raise WorkflowError(f"Monitoring record workflow failed: {e}") from e
