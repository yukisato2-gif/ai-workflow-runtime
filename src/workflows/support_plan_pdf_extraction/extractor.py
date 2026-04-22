"""書類種別に応じたプロンプト選択 + Claude 応答の JSON パース (最小版)。

プロンプト本文は cowork-assets/.../prompts/*.md から読み込む。
cowork-assets のパスは環境変数 COWORK_ASSETS_DIR で指定可能。
既定値は ai-workflow-runtime の兄弟ディレクトリ。
"""

from __future__ import annotations

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

# JSON 強制指示ブロック (冒頭)
# プロンプト本文より前に置くことで、Claude が末尾指示を軽視する
# 挙動を抑止する。PREFIX + 本文 + SUFFIX の順で結合される。
JSON_ONLY_PREFIX = """あなたはデータ抽出専用エンジンです。

これから渡されるPDFから情報を抽出し、
必ずJSONのみを返してください。

重要:
- 日本語の説明は禁止
- 挨拶は禁止
- 要約は禁止
- 質問は禁止
- コードフェンスは禁止
- 文章は禁止

出力は必ず1つのJSONオブジェクトのみとし、
1文字目は必ず { で開始してください。
"""


# JSON 強制指示ブロック (末尾)
# プロンプトが自然文応答を返すのを防ぐため、load_prompt() の末尾に必ず付加する。
# Claude が自然文で応答する癖を強く抑止する目的で、冒頭で強制宣言し、
# 禁止事項を具体例つきで列挙し、末尾で再度念押しする。
JSON_ONLY_SUFFIX = """

---

【最重要・返答形式の絶対条件】
あなたの返答は「単一の JSON オブジェクト」のみです。
JSON 以外の文字を 1 文字たりとも出力してはいけません。

## 絶対禁止 (以下を含めた時点で失敗)
- 挨拶・導入文 (例:「拝見しました」「確認しました」「以下の通りです」)
- 要約・解説・分析・補足・注釈
- 前置き・後書き
- 箇条書き・見出し・Markdown 装飾
- ```json や ``` などのコードフェンス
- 「ご質問があればお知らせください」等の会話表現
- 人間向けの説明を1行も書かないこと

## 必須
- 出力の 1 文字目は `{` で始めること
- 出力の最終文字は `}` で終えること
- 指定されたスキーマ以外のキーを勝手に追加しないこと
- 不明な項目の値は null を入れること (空文字ではなく null)

## 出力テンプレート (このまま、JSON 以外何も出さない)
{ ... }
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

    # JSON 強制指示を冒頭・末尾の両方に配置することで、
    # Claude が末尾指示を軽視する挙動を抑止する。
    # 結合順: PREFIX + "\n\n" + 本文 + "\n\n" + SUFFIX
    body = prompt_path.read_text(encoding="utf-8")
    return JSON_ONLY_PREFIX + "\n\n" + body + "\n\n" + JSON_ONLY_SUFFIX


def _try_parse_json(candidate: str) -> dict | None:
    """候補文字列を JSON としてパースする。失敗時は None。"""
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    # JSON としては文字列や数値もあり得るが、ワークフローは dict を期待する。
    # dict 以外 (list, str 等) は None として次のパターンに委ねる。
    if isinstance(parsed, dict):
        return parsed
    return None


def _try_parse_json_any(candidate: str) -> dict | list | None:
    """配列サルベージ用: list も許容する。失敗時は None。"""
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def parse_claude_response(response_text: str) -> dict:
    """Claude の応答テキストから JSON を抽出・パースする。

    応答が自然文混じりでも JSON を取り出せるよう、以下の優先順で試行する:
      (1) ```json ... ``` の fenced block を抽出して parse
      (2) ``` ... ``` の generic fenced block 内を parse
      (3) 応答本文全体の最初の "{" から最後の "}" までを抽出して parse
          (オブジェクトライク候補)
      (4) (3) が失敗時、最初の "[" から最後の "]" を抽出して parse
          (配列ライク候補; list の場合は辞書化してラップ)
      (5) 全て失敗したら応答全文のプレビューをログに残して WorkflowError

    既存の戻り値仕様 (dict) は維持。

    Args:
        response_text: Claude 応答テキスト。

    Returns:
        パース済み dict。

    Raises:
        WorkflowError: 全手段で JSON 抽出に失敗した場合。
    """
    text = response_text.strip()

    # (1) ```json ... ``` fenced block
    fenced_json_match = re.search(
        r"```json\s*\n?(.*?)\n?\s*```", text, re.DOTALL | re.IGNORECASE
    )
    if fenced_json_match:
        candidate = fenced_json_match.group(1).strip()
        logger.info("[Extractor] fenced json block found (len=%d)", len(candidate))
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed
        logger.error("[Extractor] json parse failed for pattern: fenced json block")

    # (2) ``` ... ``` generic fenced block
    fenced_generic_match = re.search(
        r"```\s*\n?(.*?)\n?\s*```", text, re.DOTALL
    )
    if fenced_generic_match:
        candidate = fenced_generic_match.group(1).strip()
        logger.info("[Extractor] generic fenced block found (len=%d)", len(candidate))
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed
        logger.error("[Extractor] json parse failed for pattern: generic fenced block")

    # (3) object-like json candidate: 最初の { から最後の }
    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start != -1 and obj_end != -1 and obj_end > obj_start:
        candidate = text[obj_start : obj_end + 1].strip()
        logger.info("[Extractor] object-like json candidate found (len=%d)", len(candidate))
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed
        logger.error("[Extractor] json parse failed for pattern: object-like candidate")

    # (4) array-like json candidate: 最初の [ から最後の ]
    arr_start = text.find("[")
    arr_end = text.rfind("]")
    if arr_start != -1 and arr_end != -1 and arr_end > arr_start:
        candidate = text[arr_start : arr_end + 1].strip()
        logger.info("[Extractor] array-like json candidate found (len=%d)", len(candidate))
        parsed_any = _try_parse_json_any(candidate)
        if isinstance(parsed_any, dict):
            return parsed_any
        if isinstance(parsed_any, list):
            # workflow は dict を期待するため、items でラップして返す
            logger.info("[Extractor] array をラップして dict 化 (items key)")
            return {"items": parsed_any}
        logger.error("[Extractor] json parse failed for pattern: array-like candidate")

    # (5) 全滅: 応答全文プレビューをログに残して失敗
    logger.error("[Extractor] all extraction patterns failed")
    logger.error("[Extractor] response first 500 chars:\n%s", text[:500])
    raise WorkflowError(
        f"Claude response is not valid JSON. "
        f"First 100 chars: {text[:100]}"
    )
