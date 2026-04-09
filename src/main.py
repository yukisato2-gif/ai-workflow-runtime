"""ai-workflow-runtime エントリポイント。

環境変数を読み込み、サンプルワークフローを実行する。
"""

import os
import sys

from dotenv import load_dotenv

from src.common import get_logger
from src.clients.claude import ClaudeClient
from src.workflows.sample_workflow import run_sample_workflow

logger = get_logger(__name__)


def main() -> None:
    """メイン関数。環境設定を読み込みワークフローを実行する。"""
    load_dotenv()

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
        result = run_sample_workflow(pdf_path=pdf_path, claude_client=claude_client)
        logger.info("Workflow finished. Items extracted: %d", len(result.items))
    except Exception as e:
        logger.error("Runtime error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
