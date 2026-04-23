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
        "日付", "利用者名", "ホーム名",
        "抽出できなかった項目", "備考",
    ],
    "plan_draft": [
        "拠点", "処理日時", "ファイル名", "ファイルID", "書類種別",
        "ホーム名", "作成日", "計画期間_開始日", "計画期間_終了日", "作成者",
        "抽出できなかった項目", "備考",
    ],
    "meeting_record": [
        "拠点", "処理日時", "ファイル名", "ファイルID", "書類種別",
        "開催日", "開催時間", "記録者", "開催場所", "参加者",
        "計画期間_開始日", "計画期間_終了日", "利用者名",
        "抽出できなかった項目", "備考",
    ],
    "plan_final": [
        "拠点", "処理日時", "ファイル名", "ファイルID", "書類種別",
        "ホーム名", "作成日", "計画期間_開始日", "計画期間_終了日",
        "作成者", "同意日", "利用者の署名の有無", "利用者の捺印の有無",
        "抽出できなかった項目", "備考",
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


def _mark_to_yes_no_unclear(value: Any) -> str:
    """plan_final の署名・捺印列出力用: ○/× → 有/無、空 → 判定不能 に変換。

    normalizer は内部表現として ○/× を返す既存契約。
    Sheets 出力時のみ「有 / 無 / 判定不能」表記に統一する。
    既に「有」「無」「判定不能」「あり」「なし」の文字列が来ても
    安全に正規化する。
    """
    s = _to_str(value)
    if s in ("○", "〇", "有", "あり", "true", "True"):
        return "有"
    if s in ("×", "ｘ", "無", "なし", "false", "False"):
        return "無"
    if s in ("判定不能",):
        return "判定不能"
    # 空 / 不明 / 未確認 / 上記以外 → 判定不能 に倒す
    return "判定不能"


def _derive_site(pdf_path: Path) -> str:
    """拠点列の値を導出する (システム補完、PDF 本文には依拠しない)。

    PDF 格納パスは典型的に
        .../GoogleDrive-xxx/共有ドライブ/<拠点名>/<案件フォルダ>/<file>.pdf
    という構造のため、ファイルの「親の親」フォルダ名 (= parents[1]) を
    拠点とみなす。一致するフォルダが取れない場合は空文字。

    例: "/.../共有ドライブ/001_100_001_GH平塚/032_個別支援計画関連PDF格納フォルダ/x.pdf"
        → "001_100_001_GH平塚"
    """
    try:
        parents = pdf_path.resolve().parents
    except Exception:
        parents = pdf_path.parents
    if len(parents) >= 2:
        candidate = parents[1].name
        # 共有ドライブ 直下の文字列など想定外の場合は空に倒す
        if candidate and candidate not in ("/", "共有ドライブ", "Shared drives", ""):
            return candidate
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

    # 仕上げフラグ: 全 5 帳票で同等品質 (現時点で対象外の doc_type は無し)
    # - 日付列は YYYY/MM(/DD) に厳密一致しない値を空にして missing 化
    # - participants は str 入力時も区切りを「、」に正規化
    # - 拠点 は PDF 親フォルダから補完
    is_monitoring = (doc_type == "monitoring")
    is_meeting_record = (doc_type == "meeting_record")
    is_plan_draft = (doc_type == "plan_draft")
    is_plan_final = (doc_type == "plan_final")
    is_assessment = (doc_type == "assessment")
    strict_format = (
        is_monitoring or is_meeting_record or is_plan_draft
        or is_plan_final or is_assessment
    )

    # 共通メタ列
    if col_name == "拠点":
        # 拠点補完は全 5 帳票で適用 (PDF 親フォルダから導出)
        if (is_monitoring or is_meeting_record or is_plan_draft
                or is_plan_final or is_assessment):
            return _derive_site(ctx["pdf_path"])
        return ""
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
        return _format_date(norm.get("created_date"), strict=strict_format)
    if col_name == "計画期間_開始日":
        return _format_date(plan_period.get("start"), strict=strict_format)
    if col_name == "計画期間_終了日":
        return _format_date(plan_period.get("end"), strict=strict_format)
    if col_name in ("作成者", "作成者名"):
        return _to_str(norm.get("author"))
    if col_name == "開催日":
        return _format_date(norm.get("meeting_date"), strict=strict_format)
    if col_name == "開催時間":
        return _to_str(norm.get("meeting_time"))
    if col_name in ("記録者", "記載者"):
        # 「記載者」は旧ヘッダ互換 (現行スキーマは「記録者」)
        return _to_str(norm.get("recorder"))
    if col_name == "開催場所":
        return _to_str(norm.get("location"))
    if col_name == "参加者":
        return _to_participants(norm.get("participants"),
                                normalize_separators=strict_format)
    if col_name == "同意日":
        return _format_date(norm.get("consent_date"), strict=strict_format)
    if col_name == "利用者の署名の有無":
        return _mark_to_yes_no_unclear(norm.get("signature"))
    if col_name == "利用者の捺印の有無":
        return _mark_to_yes_no_unclear(norm.get("seal"))
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
# 重複検出 (monitoring 限定)
# ============================================================================

def _is_duplicate_monitoring(worksheet, columns: list[str], row: list[str]) -> bool:
    """monitoring シートで「拠点 + ファイル名」一致行が既に存在するか判定。

    呼び出し元で doc_type == "monitoring" を確認してから呼ぶこと。

    Args:
        worksheet: gspread Worksheet (monitoring シート)
        columns:   今回の row に対応する列名リスト
        row:       これから書き込む値リスト

    Returns:
        True なら既存に重複あり (append しないこと)。
        False なら重複なし。
    """
    try:
        site_idx = columns.index("拠点")
        file_idx = columns.index("ファイル名")
    except ValueError:
        # 列定義漏れ時は安全側に倒して「重複なし」扱い (= 通常 append)
        return False

    new_key = (row[site_idx], row[file_idx])
    if not new_key[1]:
        # ファイル名が空の場合は判定不能 → append を許可 (誤検知防止)
        return False

    try:
        existing = worksheet.get_values("A:C")  # 拠点 / 処理日時 / ファイル名
    except Exception as e:
        logger.warning("[sheets_writer] dup-check fetch failed: %s (proceed to append)", e)
        return False

    # 1 行目はヘッダなのでスキップ
    for i, ex in enumerate(existing[1:], start=2):
        ex_site = ex[0] if len(ex) > 0 else ""
        ex_file = ex[2] if len(ex) > 2 else ""
        if (ex_site, ex_file) == new_key:
            logger.info(
                "[sheets_writer] duplicate skipped: monitoring "
                "(拠点=%r, ファイル名=%r) already at row %d",
                new_key[0], new_key[1], i,
            )
            return True
    return False


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

        # 重複防止 (monitoring 限定): 拠点 + ファイル名 一致なら append しない
        if normalized.get("document_type") == "monitoring":
            if _is_duplicate_monitoring(worksheet, columns, row):
                return

        worksheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Sheets append success: %s → %s (%s)",
                    sheet_name, sheet_id, pdf_path.name)
    except Exception as e:
        logger.error("Sheets append failed for %s: %s", pdf_path.name, e)
