"""個別支援計画 PDF 抽出 workflow 専用 Sheets 追記 (最小版)。

既存の src/tools/sheets_writer.py とは独立。5帳票を1シートに統合して
append する。列構成は schema.yaml を参考にしたフラット構造。

前提:
- 環境変数 GOOGLE_APPLICATION_CREDENTIALS: サービスアカウント JSON のパス
- 環境変数 SUPPORT_PLAN_SHEET_ID: 追記先スプレッドシート ID
- 環境変数 SUPPORT_PLAN_SHEET_NAME: シート名 (既定 OCR_個別支援計画関連)
"""

import os
from datetime import datetime
from pathlib import Path

from src.common import get_logger

logger = get_logger(__name__)


DEFAULT_SHEET_NAME = "OCR_個別支援計画関連"

# 列構成 (ヘッダ)
# 5帳票共通の列 + 各帳票固有の列をまとめたフラットスキーマ
HEADERS = [
    "登録日時",                   # A
    "ファイル名",                 # B
    "ホーム名",                   # C
    "書類種別",                   # D
    "利用者名",                   # E
    # アセスメント / 会議録 / モニタリング の主日付系
    "日付_アセスメント",          # F
    "開催日_会議録",              # G
    "実施日_モニタリング",        # H
    "作成日_計画書",              # I
    # 計画期間
    "計画期間_開始",              # J
    "計画期間_終了",              # K
    # 会議録系
    "開催時間",                   # L
    "記載者",                     # M
    "開催場所",                   # N
    # 共通 (作成者 / 参加者)
    "作成者",                     # O
    "参加者",                     # P
    # 本案固有
    "同意日",                     # Q
    "署名",                       # R
    "捺印",                       # S
    # モニタリング固有
    "次回モニタリング時期",       # T
    # 共通
    "review_required",            # U
    "review_comment",             # V
]


def _get(d: dict, *keys: str, default: str = "") -> str:
    """dict から key を順に試し、最初に見つかった値を返す。"""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return str(d[k])
    return default


def _build_row(pdf_path: Path, normalized: dict) -> list[str]:
    """正規化済み結果から1行データを構築する。"""
    doc_type = normalized.get("document_type", "unknown")
    plan_period = normalized.get("plan_period") or {}
    if not isinstance(plan_period, dict):
        plan_period = {}

    return [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),      # A 登録日時
        pdf_path.name,                                     # B ファイル名
        _get(normalized, "home_name"),                     # C ホーム名
        doc_type,                                          # D 書類種別
        _get(normalized, "user_name"),                     # E 利用者名
        _get(normalized, "date") if doc_type == "assessment" else "",           # F
        _get(normalized, "meeting_date"),                  # G 開催日_会議録
        _get(normalized, "implementation_date"),           # H 実施日_モニタリング
        _get(normalized, "created_date"),                  # I 作成日_計画書
        str(plan_period.get("start", "")),                 # J
        str(plan_period.get("end", "")),                   # K
        _get(normalized, "meeting_time"),                  # L
        _get(normalized, "recorder"),                      # M
        _get(normalized, "location"),                      # N
        _get(normalized, "author"),                        # O
        _get(normalized, "participants"),                  # P
        _get(normalized, "consent_date"),                  # Q
        _get(normalized, "signature"),                     # R
        _get(normalized, "seal"),                          # S
        _get(normalized, "next_monitoring_date"),          # T
        "true" if normalized.get("review_required") else "false",  # U
        _get(normalized, "review_comment"),                # V
    ]


def append_row(pdf_path: Path, normalized: dict) -> None:
    """正規化済み結果を Google Sheets に1行追記する。

    失敗してもワークフロー全体は止めず、ログに残す。

    Args:
        pdf_path: 処理対象の PDF ファイル。
        normalized: normalize() で整形済みの dict。
    """
    sheet_id = os.getenv("SUPPORT_PLAN_SHEET_ID", "")
    sheet_name = os.getenv("SUPPORT_PLAN_SHEET_NAME", DEFAULT_SHEET_NAME)

    if not sheet_id:
        logger.error("Sheets append skipped: SUPPORT_PLAN_SHEET_ID not set")
        return

    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds_path:
        logger.error("Sheets append skipped: GOOGLE_APPLICATION_CREDENTIALS not set")
        return

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
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=len(HEADERS))
            worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")

        row = _build_row(pdf_path, normalized)
        worksheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Sheets append success: %s → %s (%s)",
                    sheet_name, sheet_id, pdf_path.name)
    except Exception as e:
        logger.error("Sheets append failed for %s: %s", pdf_path.name, e)
