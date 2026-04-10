"""ai-workflow-runtime エントリポイント。

環境変数を読み込み、サンプルワークフローを実行する。
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.common import get_logger
from src.clients.claude import ClaudeClient
from src.workflows.monitoring_record import run_monitoring_record_workflow

logger = get_logger(__name__)


def main() -> None:
    """メイン関数。環境設定を読み込みワークフローを実行する。"""
    # プロジェクトルートの .env を明示的に指定（既存環境変数も上書き）
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("CLAUDE_MODEL", "claude-3-opus-20240229")

    if not api_key:
        logger.error("ANTHROPIC_API_KEY is not set")
        sys.exit(1)

    # TODO: PDF パスは将来的に引数 or 設定ファイルから取得する
    pdf_path = "sample.pdf"

    logger.info("Starting ai-workflow-runtime")
    logger.info("Model: %s", model)

    claude_client = ClaudeClient(api_key=api_key, model=model)

    try:
        result = run_monitoring_record_workflow(pdf_path=pdf_path, claude_client=claude_client)
        logger.info("Workflow finished. person_name=%s, confidence=%.2f", result.person_name, result.confidence)
    except Exception as e:
        logger.error("Runtime error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
