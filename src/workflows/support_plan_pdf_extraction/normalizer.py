"""抽出結果の正規化モジュール (最小版)。

役割:
- 日付を YYYY-MM-DD に統一する (和暦・スラッシュ表記に対応)
- 計画期間を start / end に分離する
- 参加者を読点区切りの1文字列に統一する (配列を受けた場合も)
- 署名・捺印は "○" / "×" / "" の3値に正規化する
- None / null は空文字に統一する

プロンプトで既に所望形式を指示しているが、モデルのゆらぎに備えて後処理する。
"""

import re
import unicodedata
from typing import Any

from src.common import get_logger

logger = get_logger(__name__)


# 元号変換 (令和・平成・昭和のみ最小対応)
_ERA_TABLE = {
    "令和": 2018,  # 令和1年 = 2019
    "平成": 1988,  # 平成1年 = 1989
    "昭和": 1925,  # 昭和1年 = 1926
}


def _normalize_date(value: Any) -> str:
    """日付を YYYY-MM-DD に正規化する。変換不能時は元値を返す。"""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""

    # 全角 → 半角
    s = unicodedata.normalize("NFKC", s)

    # すでに YYYY-MM-DD ?
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    # YYYY/MM/DD, YYYY.MM.DD
    m = re.fullmatch(r"(\d{4})[/\.](\d{1,2})[/\.](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    # YYYY年M月D日
    m = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    # 和暦: 令和5年4月1日 等
    m = re.fullmatch(r"(令和|平成|昭和)(\d{1,2})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        era, y, mo, d = m.groups()
        year = _ERA_TABLE[era] + int(y)
        return f"{year:04d}-{int(mo):02d}-{int(d):02d}"

    # YYYY-MM (年月のみ) は次回モニタリング時期で許容
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)
    if m:
        y, mo = m.groups()
        return f"{int(y):04d}-{int(mo):02d}"

    # 変換不能: 元値を返す (呼び出し側で review_required 判定)
    return s


def _normalize_plan_period(value: Any) -> dict:
    """計画期間を {"start": "...", "end": "..."} に統一する。"""
    if isinstance(value, dict):
        return {
            "start": _normalize_date(value.get("start")),
            "end": _normalize_date(value.get("end")),
        }
    if value is None:
        return {"start": "", "end": ""}

    s = str(value).strip()
    if not s:
        return {"start": "", "end": ""}

    # 「YYYY-MM-DD 〜 YYYY-MM-DD」「A～B」「A-B」等
    parts = re.split(r"\s*[〜～~\-ー－]\s*", s, maxsplit=1)
    if len(parts) == 2:
        return {"start": _normalize_date(parts[0]), "end": _normalize_date(parts[1])}
    return {"start": _normalize_date(s), "end": ""}


def _normalize_participants(value: Any) -> str:
    """参加者を読点区切りの1文字列に統一する。"""
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(x).strip() for x in value if str(x).strip())
    s = str(value).strip()
    # カンマや半角読点を全角読点に統一
    s = s.replace(",", "、").replace("､", "、")
    return s


def _normalize_mark(value: Any) -> str:
    """署名・捺印を "○" / "×" / "" の3値に正規化する。"""
    if value is None:
        return ""
    s = str(value).strip()
    if s in ("○", "〇", "o", "O", "有", "あり"):
        return "○"
    if s in ("×", "x", "X", "無", "なし", "ｘ"):
        return "×"
    return ""


def _s(value: Any) -> str:
    """None → "" 変換しつつ文字列化する。"""
    if value is None:
        return ""
    return str(value).strip()


def normalize(document_type: str, raw: dict) -> dict:
    """書類種別ごとに抽出結果を正規化する。

    Args:
        document_type: 書類種別 ID
        raw: Claude 応答の dict

    Returns:
        正規化済み dict (キー構成は schema.yaml に準拠)
    """
    result: dict = {
        "document_type": document_type,
        "review_required": bool(raw.get("review_required", False)),
        "review_comment": _s(raw.get("review_comment")),
    }

    if document_type == "assessment":
        result["date"] = _normalize_date(raw.get("date"))
        result["user_name"] = _s(raw.get("user_name"))
        result["home_name"] = _s(raw.get("home_name"))

    elif document_type == "plan_draft":
        result["home_name"] = _s(raw.get("home_name"))
        result["created_date"] = _normalize_date(raw.get("created_date"))
        result["plan_period"] = _normalize_plan_period(raw.get("plan_period"))
        result["author"] = _s(raw.get("author"))

    elif document_type == "meeting_record":
        result["meeting_date"] = _normalize_date(raw.get("meeting_date"))
        result["meeting_time"] = _s(raw.get("meeting_time"))
        result["recorder"] = _s(raw.get("recorder"))
        result["location"] = _s(raw.get("location"))
        result["participants"] = _normalize_participants(raw.get("participants"))
        result["plan_period"] = _normalize_plan_period(raw.get("plan_period"))
        result["user_name"] = _s(raw.get("user_name"))

    elif document_type == "plan_final":
        result["home_name"] = _s(raw.get("home_name"))
        result["created_date"] = _normalize_date(raw.get("created_date"))
        result["plan_period"] = _normalize_plan_period(raw.get("plan_period"))
        result["author"] = _s(raw.get("author"))
        result["consent_date"] = _normalize_date(raw.get("consent_date"))
        result["signature"] = _normalize_mark(raw.get("signature"))
        result["seal"] = _normalize_mark(raw.get("seal"))

    elif document_type == "monitoring":
        result["author"] = _s(raw.get("author"))
        result["implementation_date"] = _normalize_date(raw.get("implementation_date"))
        result["participants"] = _normalize_participants(raw.get("participants"))
        result["plan_period"] = _normalize_plan_period(raw.get("plan_period"))
        result["next_monitoring_date"] = _normalize_date(raw.get("next_monitoring_date"))

    else:
        # unknown 等: そのまま raw を残す
        logger.warning("Unknown document_type in normalize: %s", document_type)
        result["raw"] = raw

    return result
