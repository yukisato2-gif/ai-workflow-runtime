"""モニタリング記録抽出ワークフローモジュール。

PDF からテキストを抽出し、Claude でモニタリング記録の基本情報を
JSON として構造化抽出し、バリデーションを経て結果を保存する。
"""

import json
from pathlib import Path

from src.common import get_logger, WorkflowError
from src.clients.claude import ClaudeClient
from src.tools.pdf_preprocess import extract_text_from_pdf
from src.rules import validate_monitoring_record
from src.schemas import MonitoringRecord

logger = get_logger(__name__)

EXTRACTION_PROMPT_TEMPLATE = """\
以下のテキストはモニタリング記録PDFから抽出したものです。
下記のキー構造に従い、基本情報をJSON形式で抽出してください。

ルール:
- JSONのみを返してください
- 説明文は一切付けないでください
- ```json``` のコードブロックで囲まないでください
- 不明な項目は null としてください
- 必ず以下のキー構造を守ってください

出力するJSONの構造:
{{
  "document_type": "モニタリング記録",
  "person_name": "氏名",
  "implementation_date": "実施日",
  "participants": ["参加者1", "参加者2"],
  "next_monitoring_date": "次回モニタリング時期",
  "author": "モニタリング実施者",
  "confidence": 0.95
}}

入力テキスト:
{text}
"""

OUTPUT_DIR = Path("output")
OUTPUT_FILE = OUTPUT_DIR / "result.json"


def run_sample_workflow(pdf_path: str, claude_client: ClaudeClient) -> MonitoringRecord:
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
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(text=raw_text)
        extracted_json = claude_client.send_message_json(prompt)

        # Pydantic モデルに変換（型・範囲の自動検証）
        record = MonitoringRecord(**extracted_json)

        # STEP3: ビジネスルールによるバリデーション
        logger.info("STEP3: Validating monitoring record")
        validate_monitoring_record(record)

        # 抽出結果をログ出力
        logger.info(
            "Extraction result:\n%s",
            json.dumps(record.model_dump(), ensure_ascii=False, indent=2),
        )

        # STEP4: 結果を output/result.json に保存
        logger.info("STEP4: Saving result to %s", OUTPUT_FILE)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(
            json.dumps(record.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Result saved successfully")

        return record

    except Exception as e:
        logger.error("Workflow failed: %s", e)
        raise WorkflowError(f"Sample workflow failed: {e}") from e
