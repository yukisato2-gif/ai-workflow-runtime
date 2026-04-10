"""Google Sheets 追記モジュール。

OCR 抽出結果を Google Sheets に1行追記する。
サービスアカウント認証を使用する。
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from src.common import get_logger

logger = get_logger(__name__)

# 転記先スプレッドシート設定
SPREADSHEET_ID = "1vIH3jmt647SQ0AixWCOt6iJYsL__4UF9w25gu7-AxMc"
SHEET_NAME = "OCR_モニタリング記録"


def _extract_site_name(pdf_path: str) -> str:
    """PDFパスの親フォルダ名から拠点名（GH○○）を抽出する。

    想定形式: 001_100_001_GH○○ → GH○○
    取得できない場合は「不明」を返す。

    Args:
        pdf_path: PDFファイルのフルパス。

    Returns:
        拠点名（GH○○）。取得不可時は「不明」。
    """
    try:
        parent_name = Path(pdf_path).parent.name
        match = re.search(r"(GH.+)", parent_name)
        if match:
            site_name = match.group(1)
            logger.info("拠点抽出: %s", site_name)
            return site_name
    except Exception as e:
        logger.warning("拠点名の抽出に失敗: %s", e)

    logger.info("拠点抽出: 不明")
    return "不明"


def append_result_to_sheet(
    pdf_path: str,
    result_path: str = "output/result.json",
    ocr_cleaned_path: str = "output/ocr_cleaned.txt",
) -> None:
    """抽出結果を Google Sheets に1行追記する。

    失敗してもエラーログを出すのみで、例外は送出しない。

    Args:
        pdf_path: 処理対象PDFのフルパス。
        result_path: result.json のパス。
        ocr_cleaned_path: ocr_cleaned.txt のパス。
    """
    try:
        pdf_filename = Path(pdf_path).name

        # 拠点名を抽出
        site_name = _extract_site_name(pdf_path)

        # result.json 読み込み
        result_file = Path(result_path)
        if not result_file.exists():
            logger.error("Sheets append skipped: %s not found", result_path)
            return
        result = json.loads(result_file.read_text(encoding="utf-8"))

        # ocr_cleaned.txt 読み込み（なければ空文字で続行）
        ocr_file = Path(ocr_cleaned_path)
        ocr_text = ""
        if ocr_file.exists():
            ocr_text = ocr_file.read_text(encoding="utf-8")
        else:
            logger.warning("ocr_cleaned.txt not found, using empty string")

        # 列マッピングに従って1行分のデータを構成
        review_flags = result.get("_review_flags", [])
        participants = result.get("participants")
        participants_str = "、".join(participants) if participants else ""

        row = [
            site_name,                                             # A: 拠点
            pdf_filename,                                          # B: ファイル名
            "monitoring_record",                                   # C: 書類種別
            result.get("person_name", "") or "",                   # D: モニタリング対象者
            result.get("implementation_date", "") or "",           # E: 実施日
            participants_str,                                      # F: 参加者
            result.get("implementation_period", "") or "",         # G: 実施期間
            result.get("next_monitoring_date", "") or "",          # H: 次回モニタリング時期
            "OK" if len(review_flags) == 0 else "要確認",          # I: 要確認フラグ
            "、".join(review_flags) if review_flags else "",       # J: 要確認内容
            ocr_text,                                              # K: OCRテキスト
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),          # L: 登録日時
        ]

        # Google Sheets に追記
        import gspread
        from google.oauth2.service_account import Credentials

        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if not creds_path:
            logger.error("Sheets append skipped: GOOGLE_APPLICATION_CREDENTIALS not set")
            return

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)

        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.worksheet(SHEET_NAME)
        worksheet.append_row(row, value_input_option="USER_ENTERED")

        logger.info("Sheets append success: %s → %s (row for %s)", SHEET_NAME, SPREADSHEET_ID, pdf_filename)

    except Exception as e:
        logger.error("Sheets append failed: %s", e)


# 担当者会議録用スプレッドシート設定
MEETING_SHEET_NAME = "OCR_担当者会議録"


def append_meeting_result_to_sheet(
    pdf_path: str,
    result_path: str = "output/result.json",
    ocr_cleaned_path: str = "output/ocr_cleaned.txt",
) -> None:
    """担当者会議録の抽出結果を Google Sheets に1行追記する。

    失敗してもエラーログを出すのみで、例外は送出しない。

    Args:
        pdf_path: 処理対象PDFのフルパス。
        result_path: result.json のパス。
        ocr_cleaned_path: ocr_cleaned.txt のパス。
    """
    try:
        pdf_filename = Path(pdf_path).name

        # 拠点名を抽出
        site_name = _extract_site_name(pdf_path)

        # result.json 読み込み
        result_file = Path(result_path)
        if not result_file.exists():
            logger.error("Sheets append skipped: %s not found", result_path)
            return
        result = json.loads(result_file.read_text(encoding="utf-8"))

        # ocr_cleaned.txt 読み込み（なければ空文字で続行）
        ocr_file = Path(ocr_cleaned_path)
        ocr_text = ""
        if ocr_file.exists():
            ocr_text = ocr_file.read_text(encoding="utf-8")
        else:
            logger.warning("ocr_cleaned.txt not found, using empty string")

        # 列マッピングに従って1行分のデータを構成（担当者会議録用）
        review_flags = result.get("_review_flags", [])
        participants = result.get("participants")
        participants_str = "、".join(participants) if participants else ""

        row = [
            site_name,                                             # A: 拠点
            pdf_filename,                                          # B: ファイル名
            "meeting_record",                                      # C: 書類種別
            result.get("person_name", "") or "",                   # D: 利用者名
            result.get("meeting_date", "") or "",                  # E: 開催日
            result.get("meeting_time", "") or "",                  # F: 開催時間
            result.get("recorder", "") or "",                      # G: 記載者
            result.get("location", "") or "",                      # H: 開催場所
            participants_str,                                      # I: 参加者
            result.get("plan_period", "") or "",                   # J: 計画期間
            "OK" if len(review_flags) == 0 else "要確認",          # K: 要確認フラグ
            "、".join(review_flags) if review_flags else "",       # L: 要確認内容
            ocr_text,                                              # M: OCRテキスト
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),          # N: 登録日時
        ]

        # Google Sheets に追記
        import gspread
        from google.oauth2.service_account import Credentials

        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if not creds_path:
            logger.error("Sheets append skipped: GOOGLE_APPLICATION_CREDENTIALS not set")
            return

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)

        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.worksheet(MEETING_SHEET_NAME)

        # 重複チェック: B列（ファイル名）で判定
        existing_filenames = worksheet.col_values(2)  # B列
        if pdf_filename in existing_filenames:
            logger.info("Sheets append skipped (duplicate): %s is already registered", pdf_filename)
            return

        worksheet.append_row(row, value_input_option="USER_ENTERED")

        logger.info("Sheets append success: %s → %s (row for %s)", MEETING_SHEET_NAME, SPREADSHEET_ID, pdf_filename)

    except Exception as e:
        logger.error("Sheets append failed (meeting): %s", e)
