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

# JSON 強制指示ブロック
# プロンプトが自然文応答を返すのを防ぐため、load_prompt() の末尾に必ず付加する。
JSON_ONLY_SUFFIX = """

---

【返答形式 (厳守)】
出力は必ず JSON オブジェクトのみとしてください。
以下を一切含めないでください:
- 説明文・前置き・補足
- 箇条書き・見出し
- Markdown 装飾
- ```json ... ``` のコードフェンス
先頭は { 、末尾は } にしてください。
不明な項目は空文字 "" にしてください。
"""


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

    # cowork-assets のプロンプト本体に加え、JSON 強制指示を末尾に必ず付加する
    return prompt_path.read_text(encoding="utf-8") + JSON_ONLY_SUFFIX


def parse_claude_response(response_text: str) -> dict:
    """Claude の応答テキストから JSON を抽出・パースする。

    以下のサルベージを順に試みる:
      1. そのまま json.loads
      2. ```json ... ``` や ``` ... ``` コードフェンス除去後に json.loads
      3. 自然文が混入している場合、最初の "{" から最後の "}" までを
         抽出して json.loads (前置き・後置き文を除去)

    Args:
        response_text: Claude 応答テキスト。

    Returns:
        パース済み dict。

    Raises:
        WorkflowError: 全手段で JSON パースに失敗した場合。
    """
    text = response_text.strip()

    # 試行 1: そのまま
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 試行 2: コードフェンス除去
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        fenced = fence_match.group(1).strip()
        logger.debug("コードフェンスから JSON を抽出")
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            text = fenced  # フェンス除去後のテキストで後段サルベージへ

    # 試行 3: 最初の { から最後の } を抽出 (自然文サルベージ)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1].strip()
        logger.debug("自然文サルベージで JSON 抽出試行 (len=%d)", len(candidate))
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            logger.error("サルベージ後も JSON パース失敗: %s", e)
            logger.error("サルベージ先頭200文字: %s", candidate[:200])
            raise WorkflowError(
                f"Claude response is not valid JSON after salvage: {e}. "
                f"First 100 chars: {candidate[:100]}"
            ) from e

    # 全滅
    logger.error("JSON parse failed: { ... } パターンが見つかりません")
    logger.error("Response first 200 chars: %s", text[:200])
    raise WorkflowError(
        f"Claude response is not valid JSON. "
        f"First 100 chars: {text[:100]}"
    )
