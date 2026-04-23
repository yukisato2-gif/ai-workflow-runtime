"""個別支援計画 PDF 抽出 workflow 専用 Sheets 追記。

5 帳票を doc_type 別シートに振り分けて append する。
列構成は **シート毎に異なる** 固定スキーマで、
COLUMN_MAPPINGS を「単一の真実の源」として保持する。

設計原則:
- row 生成は必ず固定順配列で行う (dict 順依存禁止)
- doc_type → 出力シート名 → 列スキーマ の対応を1箇所に集約
- 列名 (シート列) → normalized キー の対応も1箇所に集約 (_extract_value)
- 抽出できなかった項目は「備考」に明示的に残す (黙って捨てない)

前提:
- 環境変数 GOOGLE_APPLICATION_CREDENTIALS: サービスアカウント JSON のパス
- 環境変数 SUPPORT_PLAN_SHEET_ID: 追記先スプレッドシート ID

廃止: 旧 SUPPORT_PLAN_SHEET_NAME (単一シート時代の振り分け先) は読まれない。
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.common import get_logger

logger = get_logger(__name__)


# ============================================================================
# 振り分けマッピング (単一の真実の源)
# ============================================================================

# 内部 doc_type → 出力先シート名 (日本語)
SHEET_NAME_MAP: dict[str, str] = {
    "assessment":     "アセスメント",
    "plan_draft":     "個別支援計画書案",
    "meeting_record": "担当者会議録",
    "plan_final":     "個別支援計画書本案",
    "monitoring":     "モニタリング",
}

# unknown 種別 / 例外失敗時はここに集約
ERROR_SHEET_NAME = "エラーログ"

# 「書類種別」列に出す日本語ラベル (内部 doc_type は変更しない)
DOC_TYPE_DISPLAY: dict[str, str] = {
    "assessment":     "アセスメント",
    "plan_draft":     "個別支援計画書案",
    "meeting_record": "担当者会議録",
    "plan_final":     "個別支援計画書本案",
    "monitoring":     "モニタリング",
    "unknown":        "未分類",
}

# 各 doc_type の出力シートに並ぶ列 (順序固定・実シートのヘッダと完全一致)。
# ここを編集する以外で列を増減・並び替えしないこと。
COLUMN_MAPPINGS: dict[str, list[str]] = {
    "assessment": [
        "拠点", "処理日時", "ファイル名", "ファイルID", "書類種別",
        "日付", "利用者名", "ホーム名", "備考",
    ],
    "plan_draft": [
        "拠点", "処理日時", "ファイル名", "ファイルID", "書類種別",
        "ホーム名", "作成日", "計画期間_開始日", "計画期間_終了日", "作成者", "備考",
    ],
    "meeting_record": [
        "拠点", "処理日時", "ファイル名", "ファイルID", "書類種別",
        "開催日", "開催時間", "記載者", "開催場所", "参加者",
        "計画期間_開始日", "計画期間_終了日", "利用者名", "備考",
    ],
    "plan_final": [
        "拠点", "処理日時", "ファイル名", "ファイルID", "書類種別",
        "ホーム名", "作成日", "計画期間_開始日", "計画期間_終了日",
        "作成者名", "同意日", "利用者の署名の有無", "利用者の捺印の有無", "備考",
    ],
    "monitoring": [
        "拠点", "処理日時", "ファイル名", "ファイルID", "書類種別",
        "作成者", "実施日", "参加者",
        "計画期間_開始日", "計画期間_終了日", "次回モニタリング時期",
        "抽出できなかった項目", "備考",
    ],
}

# エラーログシート (unknown / 例外失敗用)
ERROR_LOG_COLUMNS: list[str] = [
    "拠点", "処理日時", "ファイル名", "ファイルID", "書類種別", "備考",
]

# 備考列で「抽出できなかった項目」として列挙する対象列
# (拠点/処理日時/ファイル名/ファイルID/書類種別/備考 自体は対象外)
_REMARKS_TRACKABLE_COLS: set[str] = {
    "日付", "利用者名", "ホーム名", "作成日",
    "計画期間_開始日", "計画期間_終了日",
    "作成者", "作成者名",
    "開催日", "開催時間", "記載者", "開催場所", "参加者",
    "同意日", "利用者の署名の有無", "利用者の捺印の有無",
    "実施日", "次回モニタリング時期",
}


# ============================================================================
# 値の正規化ヘルパ (Sheets 書込前の安全な文字列化)
# ============================================================================

def _to_str(value: Any) -> str:
    """None / 数値 / bool / str を安全に文字列化。空白除去のみ。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _to_participants(value: Any, normalize_separators: bool = False) -> str:
    """list / str を「、」区切り文字列に整形する。

    - list: None / 空文字要素は除去して 「、」 join
      (str(None) = 'None' リテラルが混入する事故を防ぐ)
    - str + normalize_separators=True (monitoring 用):
      `,` `, ` `，` `；` `; ` `;` ` | ` `|` を全て 「、」 に統一し、
      連続「、」/前後の「、」を整理する
    - str + normalize_separators=False (デフォルト・他帳票互換):
      従来どおり _to_str(value) を返す
    """
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(
            str(x).strip()
            for x in value
            if x is not None and str(x).strip()
        )
    if not normalize_separators:
        return _to_str(value)
    s = _to_str(value)
    if not s:
        return ""
    # 区切り正規化 (長いものから順に置換)
    for sep in (", ", "，", ",", "； ", "; ", "；", ";", " | ", "|"):
        s = s.replace(sep, "、")
    return "、".join(p.strip() for p in s.split("、") if p.strip())


# 厳密モード用: YYYY/MM/DD または YYYY/MM のみ許容
_STRICT_DATE_RE = re.compile(r"^\d{4}/\d{1,2}(?:/\d{1,2})?$")


def _format_date(value: Any, strict: bool = False) -> str:
    """YYYY-MM-DD / YYYY-MM 等を YYYY/MM/DD / YYYY/MM にスラッシュ表記化。

    既存シートが「2025/04/01」形式で書かれているため整合させる。

    - strict=False (デフォルト・他帳票互換): 変換不能な自由文 (例:
      「2025年9月頃」「判定不能」) は **そのまま返す**
    - strict=True (monitoring 用): 上記いずれの形式にも合致しない値は
      **空文字** を返し、missing_fields 検出に委ねる
    """
    s = _to_str(value)
    if not s:
        return ""
    # YYYY-MM-DD / YYYY-MM (normalizer 出力) → スラッシュに置換
    if "-" in s and "/" not in s:
        s = s.replace("-", "/")
    if strict and not _STRICT_DATE_RE.match(s):
        return ""
    return s


def _derive_site(pdf_path: Path) -> str:
    """拠点列の値を導出する。

    既存実装で確実な拠点情報を持っていないため、現時点では空文字を返す
    (既存シートでも空のまま運用されているため整合)。
    将来的に親フォルダ名等から導出する場合はここを差し替える。
    """
    return ""


# ============================================================================
# 列名 → 値 抽出 (シート列名と normalized キーの対応を1箇所に集約)
# ============================================================================

def _extract_value(col_name: str, doc_type: str, ctx: dict) -> str:
    """1 つのシート列に対する値を返す。

    sheet 列名と normalized 内部キーの対応 + フォーマットを1箇所に集約。
    分岐は明示的 if/elif で書き、隠れた魔法的挙動を避ける。

    Args:
        col_name: シート上の列名 (例: "計画期間_開始日")
        doc_type: 内部 doc_type (例: "monitoring")
        ctx: { "pdf_path", "normalized", "processed_at" } を含む dict

    Returns:
        Sheets セルに書き込む文字列 (空文字 OK)。
    """
    norm: dict = ctx["normalized"]
    plan_period = norm.get("plan_period") or {}
    if not isinstance(plan_period, dict):
        plan_period = {}

    # monitoring の最終仕上げ用フラグ:
    # - 日付列は YYYY/MM(/DD) に厳密一致しない値を空にして missing 化
    # - participants は str 入力時も区切りを「、」に正規化
    is_monitoring = (doc_type == "monitoring")

    # 共通メタ列
    if col_name == "拠点":
        return _derive_site(ctx["pdf_path"])
    if col_name == "処理日時":
        return ctx["processed_at"]
    if col_name == "ファイル名":
        return ctx["pdf_path"].name
    if col_name == "ファイルID":
        # workflow はローカル PDF を入力に取るため、Drive 側のファイル ID は
        # 持たない。空文字を返し、推測値を入れない。
        return ""
    if col_name == "書類種別":
        return DOC_TYPE_DISPLAY.get(doc_type, doc_type)
    if col_name == "抽出できなかった項目":
        # 専用列がある場合: 接頭語なし・読点(、)区切り・field 名のみ
        return "、".join(ctx.get("missing_fields", []))
    if col_name == "備考":
        return ctx.get("remarks", "")

    # データ列 (sheet 列名 → normalized キーの明示マッピング)
    if col_name == "日付":
        return _format_date(norm.get("date"))
    if col_name == "ホーム名":
        return _to_str(norm.get("home_name"))
    if col_name == "利用者名":
        return _to_str(norm.get("user_name"))
    if col_name == "作成日":
        return _format_date(norm.get("created_date"))
    if col_name == "計画期間_開始日":
        return _format_date(plan_period.get("start"), strict=is_monitoring)
    if col_name == "計画期間_終了日":
        return _format_date(plan_period.get("end"), strict=is_monitoring)
    if col_name in ("作成者", "作成者名"):
        return _to_str(norm.get("author"))
    if col_name == "開催日":
        return _format_date(norm.get("meeting_date"))
    if col_name == "開催時間":
        return _to_str(norm.get("meeting_time"))
    if col_name == "記載者":
        return _to_str(norm.get("recorder"))
    if col_name == "開催場所":
        return _to_str(norm.get("location"))
    if col_name == "参加者":
        return _to_participants(norm.get("participants"),
                                normalize_separators=is_monitoring)
    if col_name == "同意日":
        return _format_date(norm.get("consent_date"))
    if col_name == "利用者の署名の有無":
        return _to_str(norm.get("signature"))
    if col_name == "利用者の捺印の有無":
        return _to_str(norm.get("seal"))
    if col_name == "実施日":
        return _format_date(norm.get("implementation_date"), strict=is_monitoring)
    if col_name == "次回モニタリング時期":
        return _format_date(norm.get("next_monitoring_date"), strict=is_monitoring)

    # 未知の列 (定義漏れ): 空文字 + ログ
    logger.warning("[sheets_writer] unknown column %r for doc_type=%s", col_name, doc_type)
    return ""


def _compute_missing_fields(columns: list[str], doc_type: str, ctx: dict) -> list[str]:
    """各列のうち、データ列で値が空のものを列名のリストとして返す。

    拠点 / 処理日時 / ファイル名 / ファイルID / 書類種別 / 抽出できなかった項目 /
    備考 などのメタ列は除外。返り値は列定義順を保つ。
    """
    missing: list[str] = []
    for col in columns:
        if col not in _REMARKS_TRACKABLE_COLS:
            continue
        v = _extract_value(col, doc_type, ctx)
        if not v:
            missing.append(col)
    return missing


def _build_remarks(columns: list[str], doc_type: str, ctx: dict) -> str:
    """「備考」列の文字列を生成する。

    - 専用列「抽出できなかった項目」が COLUMN_MAPPINGS にある場合は
      備考にはそれを含めず、review_comment のみ
    - 専用列がない場合は従来どおり「抽出できなかった項目: a, b, c」を含める
    - 末尾に review_comment を追記
    """
    has_dedicated_missing_col = "抽出できなかった項目" in columns
    parts: list[str] = []
    if not has_dedicated_missing_col:
        missing = ctx.get("missing_fields", [])
        if missing:
            parts.append("抽出できなかった項目: " + ", ".join(missing))
    review_comment = _to_str(ctx["normalized"].get("review_comment"))
    if review_comment:
        parts.append(review_comment)
    return " / ".join(parts)


# ============================================================================
# row 生成 (固定順配列・dict 順依存なし)
# ============================================================================

def _resolve_sheet_name(normalized: dict) -> str:
    """doc_type に応じた出力先シート名を解決する (単一の振り分け関数)。"""
    doc_type = normalized.get("document_type", "unknown")
    return SHEET_NAME_MAP.get(doc_type, ERROR_SHEET_NAME)


def _resolve_columns(normalized: dict) -> list[str]:
    """doc_type に応じた列順 (シート列名のリスト) を返す。"""
    doc_type = normalized.get("document_type", "unknown")
    return COLUMN_MAPPINGS.get(doc_type, ERROR_LOG_COLUMNS)


def _build_row(pdf_path: Path, normalized: dict) -> tuple[list[str], list[str], str]:
    """指定シートに対して順序固定の行を生成する。

    Returns:
        (columns, row, sheet_name)
        - columns: シート列名リスト (順序固定)
        - row:     上記順序に厳密一致する値リスト (同じ長さ)
        - sheet_name: 出力先シート名

    dict 順依存を完全排除するため、columns を先頭から順に走査して
    row を組み立てる。row 長は必ず len(columns) と一致する。
    """
    doc_type = normalized.get("document_type", "unknown")
    sheet_name = _resolve_sheet_name(normalized)
    columns = _resolve_columns(normalized)
    processed_at = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    ctx: dict = {
        "pdf_path": pdf_path,
        "normalized": normalized,
        "processed_at": processed_at,
        "missing_fields": [],   # 仮置き、後段で上書き
        "remarks": "",          # 仮置き、後段で上書き
    }
    # データ列の空項目を先に集計 → missing_fields 専用列 / 備考の両方で利用
    ctx["missing_fields"] = _compute_missing_fields(columns, doc_type, ctx)
    ctx["remarks"] = _build_remarks(columns, doc_type, ctx)

    row = [_extract_value(col, doc_type, ctx) for col in columns]
    assert len(row) == len(columns), (
        f"row/column length mismatch: {len(row)} vs {len(columns)}"
    )
    return columns, row, sheet_name


# ============================================================================
# Sheets 追記 (公開 API)
# ============================================================================

def append_row(pdf_path: Path, normalized: dict) -> None:
    """正規化済み結果を Google Sheets に1行追記する。

    出力先シートは normalized["document_type"] から自動振り分け。
    シートが無ければそのシート用の列ヘッダで新規作成する。
    失敗してもワークフロー全体は止めず、ログに残す。

    Args:
        pdf_path: 処理対象の PDF ファイル。
        normalized: normalize() で整形済みの dict。
    """
    sheet_id = os.getenv("SUPPORT_PLAN_SHEET_ID", "")
    if not sheet_id:
        logger.error("Sheets append skipped: SUPPORT_PLAN_SHEET_ID not set")
        return

    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds_path:
        logger.error("Sheets append skipped: GOOGLE_APPLICATION_CREDENTIALS not set")
        return

    columns, row, sheet_name = _build_row(pdf_path, normalized)

    # 列ズレ検証用の最小ログ (過剰にしない)
    logger.info("[sheets_writer] doc_type=%s sheet=%s row_len=%d/%d",
                normalized.get("document_type", "unknown"),
                sheet_name, len(row), len(columns))

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)
        spreadsheet = gc.open_by_key(sheet_id)
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except Exception:
            logger.info("Worksheet '%s' not found. Creating with headers.", sheet_name)
            worksheet = spreadsheet.add_worksheet(
                title=sheet_name, rows=1000, cols=len(columns)
            )
            worksheet.append_row(columns, value_input_option="USER_ENTERED")

        worksheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Sheets append success: %s → %s (%s)",
                    sheet_name, sheet_id, pdf_path.name)
    except Exception as e:
        logger.error("Sheets append failed for %s: %s", pdf_path.name, e)
