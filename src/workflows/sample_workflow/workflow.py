"""サンプルワークフローモジュール。

PDF からテキストを抽出し、Claude で JSON 構造化を行い、
バリデーションを経て結果を出力する一連のフローを実装する。
"""

import json

from src.common import get_logger, WorkflowError
from src.clients.claude import ClaudeClient
from src.tools.pdf_preprocess import extract_text_from_pdf
from src.rules import validate_extraction_result
from src.schemas import ExtractionResult, ExtractedItem

logger = get_logger(__name__)

EXTRACTION_PROMPT_TEMPLATE = """\
以下のテキストから構造化データを JSON 形式で抽出してください。

## 出力フォーマット
```json
{{
  "items": [
    {{"key": "項目名", "value": "値", "confidence": 0.95}}
  ]
}}
```

## 入力テキスト
{text}

JSON のみを出力してください。説明は不要です。
"""


def run_sample_workflow(pdf_path: str, claude_client: ClaudeClient) -> ExtractionResult:
    """サンプルワークフローを実行する。

    STEP1: PDF テキスト抽出（ダミー）
    STEP2: Claude で JSON 構造化抽出
    STEP3: バリデーション
    STEP4: 結果出力

    Args:
        pdf_path: 処理対象の PDF ファイルパス。
        claude_client: Claude API クライアント。

    Returns:
        バリデーション済みの抽出結果。

    Raises:
        WorkflowError: ワークフロー実行中にエラーが発生した場合。
    """
    try:
        # STEP1: PDF テキスト抽出
        logger.info("STEP1: Extracting text from PDF")
        raw_text = extract_text_from_pdf(pdf_path)

        # STEP2: Claude で JSON 抽出
        logger.info("STEP2: Sending text to Claude for JSON extraction")
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(text=raw_text)
        extracted_json = claude_client.send_message_json(prompt)

        # レスポンスを ExtractionResult に変換
        items = [
            ExtractedItem(**item)
            for item in extracted_json.get("items", [])
        ]
        result = ExtractionResult(
            source_file=pdf_path,
            items=items,
            raw_text=raw_text,
        )

        # STEP3: バリデーション
        logger.info("STEP3: Validating extraction result")
        validate_extraction_result(result)

        # STEP4: 結果出力
        logger.info("STEP4: Workflow completed successfully")
        logger.info(
            "Result: %s",
            json.dumps(result.model_dump(), ensure_ascii=False, indent=2),
        )
        return result

    except Exception as e:
        logger.error("Workflow failed: %s", e)
        raise WorkflowError(f"Sample workflow failed: {e}") from e
