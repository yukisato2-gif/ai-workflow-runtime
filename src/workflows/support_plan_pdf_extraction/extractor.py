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

    # (5) fallback: markdown 表 / 「項目: 値」/ 「項目<TAB>値」形式から救済
    # Claude が JSON 強制指示に従わず markdown 表で返すケース（特に長文 PDF）への
    # 最終救済。抽出できた key-value を dict にして返す。後段 normalizer の
    # 日本語キー alias 吸収に任せる。
    salvaged = _salvage_kv_lines(text)
    if salvaged:
        logger.info("[Extractor] kv-line salvage found %d entries", len(salvaged))
        return salvaged

    # (5.5) enhanced kv salvage: 日本語ラベルマップ + 期間分解付きの最終 fallback。
    # 既存 _salvage_kv_lines が 0 件で空 dict を返した場合のみ発火する。
    # 既存スキーマ (home_name / created_date / author / plan_period) に合わせて
    # 値が 1 つでも見つかれば return、全部空なら従来通り (6) で WorkflowError を raise。
    enhanced = _kv_salvage_enhanced(text)
    enhanced_pp = enhanced.get("plan_period", {}) or {}
    if (
        enhanced.get("home_name")
        or enhanced.get("created_date")
        or enhanced.get("author")
        or enhanced_pp.get("start")
        or enhanced_pp.get("end")
    ):
        logger.info(
            "[Extractor] enhanced kv salvage produced values: "
            "home_name=%r created_date=%r author=%r plan_period=%r",
            enhanced.get("home_name"),
            enhanced.get("created_date"),
            enhanced.get("author"),
            enhanced.get("plan_period"),
        )
        return enhanced

    # (6) 全滅: 応答全文プレビューをログに残して失敗
    logger.error("[Extractor] all extraction patterns failed")
    logger.error("[Extractor] response first 500 chars:\n%s", text[:500])
    raise WorkflowError(
        f"Claude response is not valid JSON. "
        f"First 100 chars: {text[:100]}"
    )


def _salvage_kv_lines(text: str) -> dict:
    """markdown 表 / 「項目: 値」/ 「項目<TAB>値」形式から key-value を救済する。

    既存の JSON 抽出 (1)-(4) が全失敗した場合の最終 fallback。
    Claude が自然文混じりの表形式で返すケース（モデル癖）を救済する。

    パース対象:
    - "| 項目 | 値 |" (markdown table 行)
    - "項目<TAB>値" (タブ区切り)
    - "項目: 値" / "項目：値" (半角・全角コロン)

    無視対象:
    - 空行 / 区切り罫線 ("|---|---|" など)
    - 値が空 / 値=key と同一
    - 明らかに本文記述 (文末が句点 "。" 等)

    Returns:
        抽出できた key-value の dict (1件以上)、抽出ゼロなら空 dict。
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # markdown 表の罫線行 ("|---|---|" 等) はスキップ
        if re.fullmatch(r"\|?\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?", line):
            continue

        key: str | None = None
        value: str | None = None

        # markdown table: "| 項目 | 値 |" → cells = [項目, 値, ...]
        if line.startswith("|") and line.endswith("|") and line.count("|") >= 3:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and cells[1]:
                key, value = cells[0], cells[1]
        # tab 区切り
        elif "\t" in line:
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                key, value = parts[0].strip(), parts[1].strip()
        # コロン区切り (全角・半角)
        else:
            m = re.match(r"^([^\:：]{1,40})[\:：]\s*(.+)$", line)
            if m and m.group(2).strip():
                key, value = m.group(1).strip(), m.group(2).strip()

        if not key or not value:
            continue
        # 同値・記号のみ・「項目」「内容」のような表ヘッダはスキップ
        if key == value:
            continue
        if key in ("項目", "内容", "key", "value", "Key", "Value"):
            continue
        # 本文記述を排除: 値が長く句点で終わる文章はスキップ (キー名側は許容)
        if len(value) > 200 and value.endswith(("。", ".")):
            continue
        # 同一キー重複時は最初の値を優先 (ヘッダ近傍が先に来る想定)
        if key not in result:
            result[key] = value

    return result


def _kv_salvage_enhanced(text: str) -> dict:
    """日本語ラベルマッピング + 期間分解 + ラベルなし救済付きの強化 KV salvage。

    parse_claude_response の既存 (1)-(5) パスがすべて失敗し、かつ
    既存 _salvage_kv_lines も 0 件で空 dict を返した場合のみ呼ばれる。
    既存スキーマ (sheets_writer / normalizer) と整合する以下のキー固定形式で返す:

        {
          "home_name": "",
          "created_date": "",
          "author": "",
          "plan_period": {"start": "", "end": ""}
        }

    処理の優先順 (Pass1 で取れたものは Pass2/3 で上書きしない):
      Pass1: 「ラベル: 値」「ラベル：値」(コロン区切り)
      Pass2: 「ラベル 値」(空白区切り、ラベル正規化済み)
      Pass3: ラベルなし heuristics
        - 「〜」「～」「~」「 - 」を含み両側に数字を含む行 → plan_period
        - 行全体が日付のみ → created_date
        - 「氏名 様」パターン → author 候補 (※利用者氏名と紛れる可能性あり、ログで明示)

    日付正規化は行わず、検出文字列をそのまま値に格納する (downstream の
    normalizer に正規化を委ねる)。値が見つからなかったキーは "" のまま。
    """
    # ラベル → 正規化キー
    label_map = {
        "作成日": "created_date",
        "作成月": "created_date",
        "グループホーム名": "home_name",
        "事業所名": "home_name",
        "サービス管理責任者": "author",
        "作成者": "author",
        "担当者": "author",
        "計画期間": "plan_period",
        "支援期間": "plan_period",
        "対象期間": "plan_period",
    }

    result: dict = {
        "home_name": "",
        "created_date": "",
        "author": "",
        "plan_period": {"start": "", "end": ""},
    }

    # 期間分解用 ("〜" U+301C / "～" U+FF5E / "~" / "-" 対応、"-" のみ前後空白必須)
    period_split_re = re.compile(r"\s*[〜～~]\s*|\s+-\s+")
    line_kv_re = re.compile(r"^(.+?)[:：]\s*(.+)$")
    rule_re = re.compile(r"\|?\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?")
    # コロンなし「ラベル<空白>値」(label_map のキーで前方一致)
    label_space_re = re.compile(
        r"^(計画期間|支援期間|対象期間|作成日|作成月|"
        r"グループホーム名|事業所名|"
        r"サービス管理責任者|作成者|担当者)[\s\u3000]+(.+)$"
    )
    # 行全体が日付 (YYYY/MM/DD, YYYY年M月D日, YYYY-MM-DD, YYYY/MM, YYYY年M月 等)
    date_only_re = re.compile(
        r"^[\s\u3000]*"
        r"(\d{4}\s*[年/.\-]\s*\d{1,2}(?:\s*[月/.\-]\s*\d{1,2}日?)?)"
        r"[\s\u3000]*$"
    )
    # 期間判定 (両側に数字を要求し、誤検出を抑制)
    has_period_sep_re = re.compile(r"[〜～~]|\s-\s")
    # 「氏名 様」検出 (漢字/かな 1〜6 文字、姓名間の空白許容)
    name_sama_re = re.compile(
        r"([\u3040-\u30ff\u4e00-\u9fff]{1,6}[\s\u3000]?[\u3040-\u30ff\u4e00-\u9fff]{0,6})"
        r"[\s\u3000]*様"
    )

    def _norm_label(s: str) -> str:
        """ラベル文字列の OCR ノイズを吸収 (全角空白除去 + 連続空白圧縮)。値は触らない。"""
        s = s.replace("\u3000", "")
        s = re.sub(r"\s+", "", s)
        return s.strip()

    def _norm_value(s: str) -> str:
        """値の軽量正規化 (前後 strip + 連続半角空白を 1 つに)。"""
        s = s.strip().strip("|").strip()
        s = re.sub(r"[ \t]{2,}", " ", s)
        return s

    def _split_period(value: str):
        """期間文字列を (start, end) に分割。両側に数字が無ければ None。"""
        v = _norm_value(value)
        parts = period_split_re.split(v, maxsplit=1)
        if (len(parts) == 2 and parts[0].strip() and parts[1].strip()
                and re.search(r"\d", parts[0]) and re.search(r"\d", parts[1])):
            return parts[0].strip(), parts[1].strip()
        return None

    captured: list[str] = []

    # ===== Pass1: コロン区切り「ラベル: 値」=====
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```") or rule_re.fullmatch(line):
            continue
        m = line_kv_re.match(line)
        if not m:
            continue
        label_raw = m.group(1).strip().lstrip("|").strip()
        value = _norm_value(m.group(2))
        if not label_raw or not value:
            continue
        label = _norm_label(label_raw)
        norm_key = label_map.get(label)
        if norm_key is None:
            for lbl, k in label_map.items():
                if label.startswith(lbl):
                    norm_key = k
                    break
        if norm_key is None:
            continue
        if norm_key == "plan_period":
            pp = _split_period(value)
            if pp:
                if not result["plan_period"]["start"]:
                    result["plan_period"]["start"] = pp[0]
                    captured.append(f"plan_period.start(labeled)={pp[0]!r}")
                if not result["plan_period"]["end"]:
                    result["plan_period"]["end"] = pp[1]
                    captured.append(f"plan_period.end(labeled)={pp[1]!r}")
        else:
            if not result.get(norm_key):
                result[norm_key] = value
                captured.append(f"{norm_key}(labeled)={value!r}")

    # ===== Pass2: コロンなし「ラベル<空白>値」=====
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```") or rule_re.fullmatch(line):
            continue
        m_sp = label_space_re.match(line)
        if not m_sp:
            continue
        label = _norm_label(m_sp.group(1))
        value = _norm_value(m_sp.group(2))
        norm_key = label_map.get(label)
        if not norm_key or not value:
            continue
        if norm_key == "plan_period":
            pp = _split_period(value)
            if pp and not result["plan_period"]["start"]:
                result["plan_period"]["start"] = pp[0]
                result["plan_period"]["end"] = pp[1]
                captured.append(
                    f"plan_period(label-space)={pp[0]!r}~{pp[1]!r}"
                )
        else:
            if not result.get(norm_key):
                result[norm_key] = value
                captured.append(f"{norm_key}(label-space)={value!r}")

    # ===== Pass3: ラベルなし heuristics =====
    # plan_period が未取得なら、期間セパレータを含む行を探す
    if not result["plan_period"]["start"]:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("```") or rule_re.fullmatch(line):
                continue
            if not has_period_sep_re.search(line):
                continue
            pp = _split_period(line)
            if pp:
                result["plan_period"]["start"] = pp[0]
                result["plan_period"]["end"] = pp[1]
                captured.append(
                    f"plan_period(unlabeled-heuristic)={pp[0]!r}~{pp[1]!r}"
                )
                break
    elif not result["plan_period"]["end"]:
        # start のみある状態は推定せず、ログで顕在化
        captured.append(
            f"WARN plan_period.end MISSING "
            f"(start_only={result['plan_period']['start']!r})"
        )

    # created_date が未取得なら、日付のみ行を探す
    if not result["created_date"]:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("```"):
                continue
            m_d = date_only_re.match(line)
            if m_d:
                result["created_date"] = m_d.group(1).strip()
                captured.append(
                    f"created_date(unlabeled-date-only)={result['created_date']!r}"
                )
                break

    if captured:
        logger.info(
            "[Extractor] enhanced salvage captured (%d items): %s",
            len(captured),
            "; ".join(captured),
        )
    else:
        logger.info("[Extractor] enhanced salvage captured nothing")

    return result
