"""書類種別に応じたプロンプト選択 + Claude 応答の JSON パース (最小版)。

プロンプト本文は cowork-assets/.../prompts/*.md から読み込む。
cowork-assets のパスは環境変数 COWORK_ASSETS_DIR で指定可能。
既定値は ai-workflow-runtime の兄弟ディレクトリ。
"""

import json
import os
import re
from pathlib import Path

from src.common import get_logger, WorkflowError

logger = get_logger(__name__)


# 書類種別 → プロンプトファイル名
PROMPT_FILES: dict[str, str] = {
    "assessment": "assessment.md",
    "plan_draft": "plan_draft.md",
    "meeting_record": "meeting_record.md",
    "plan_final": "plan_final.md",
    "monitoring": "monitoring.md",
}


def _resolve_prompts_dir() -> Path:
    """cowork-assets のプロンプトディレクトリを解決する。"""
    override = os.getenv("SUPPORT_PLAN_PROMPTS_DIR")
    if override:
        return Path(override)

    base = os.getenv("COWORK_ASSETS_DIR")
    if base:
        return (
            Path(base)
            / "20_部署スキル"
            / "運営監査課_operations-audit"
            / "個別支援計画抽出_pdf-extraction-support-plan"
            / "prompts"
        )

    # 既定値: ai-workflow-runtime の兄弟ディレクトリ
    runtime_root = Path(__file__).resolve().parents[3]
    return (
        runtime_root.parent
        / "cowork-assets"
        / "20_部署スキル"
        / "運営監査課_operations-audit"
        / "個別支援計画抽出_pdf-extraction-support-plan"
        / "prompts"
    )


def load_prompt(document_type: str) -> str:
    """書類種別に対応するプロンプト本文を読み込む。

    Args:
        document_type: assessment / plan_draft / meeting_record /
                       plan_final / monitoring のいずれか。

    Returns:
        プロンプト本文 (Markdown 文字列そのまま)。

    Raises:
        WorkflowError: unknown や該当ファイル不在の場合。
    """
    if document_type not in PROMPT_FILES:
        raise WorkflowError(f"No prompt defined for document_type={document_type}")

    prompts_dir = _resolve_prompts_dir()
    prompt_path = prompts_dir / PROMPT_FILES[document_type]
    if not prompt_path.exists():
        raise WorkflowError(
            f"Prompt file not found: {prompt_path}\n"
            f"Set COWORK_ASSETS_DIR or SUPPORT_PLAN_PROMPTS_DIR environment variable."
        )

    return prompt_path.read_text(encoding="utf-8")


def parse_claude_response(response_text: str) -> dict:
    """Claude の応答テキストから JSON を抽出・パースする。

    応答がコードブロック (```json ... ```) で囲まれていても除去する。

    Args:
        response_text: Claude 応答テキスト。

    Returns:
        パース済み dict。

    Raises:
        WorkflowError: JSON パースに失敗した場合。
    """
    text = response_text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
        logger.debug("Extracted JSON from code block")

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s", e)
        logger.error("Response first 200 chars: %s", text[:200])
        raise WorkflowError(
            f"Claude response is not valid JSON: {e}. "
            f"First 100 chars: {text[:100]}"
        ) from e
