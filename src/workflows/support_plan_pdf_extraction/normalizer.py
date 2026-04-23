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

    # YYYY年M月 (年月のみ、日なし)
    m = re.fullmatch(r"(\d{4})年(\d{1,2})月", s)
    if m:
        y, mo = m.groups()
        return f"{int(y):04d}-{int(mo):02d}"

    # 変換不能: 元値を返す (呼び出し側で review_required 判定)
    return s


def _normalize_plan_period(value: Any) -> dict:
    """計画期間を {"start": "...", "end": "..."} に統一する。

    Claude が start/end の代わりに start_date/end_date や日本語キー
    (開始/終了) を返すケースがあるため、dict 入力時は複数キー候補を受け付ける。
    """
    if isinstance(value, dict):
        def _pick(*keys):
            for k in keys:
                if k in value and value[k] not in (None, ""):
                    return value[k]
            return None

        start_val = _pick("start", "start_date", "開始", "開始日")
        end_val = _pick("end", "end_date", "終了", "終了日")
        return {
            "start": _normalize_date(start_val),
            "end": _normalize_date(end_val),
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
    """署名・捺印を "○" / "×" / "" の3値に正規化する。

    boolean (True/False) も「あり/なし」として扱う
    (Claude が「押印: true」のような形式で返すケース対応)。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "○" if value else "×"
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


def _first(raw: dict, *keys: str) -> Any:
    """raw から先頭にヒットしたキーの値を返す (別名キー吸収用)。

    Claude が JSON のキー名をプロンプト指定どおりに返さず、
    類似名 (group_home_name, creation_month, service_manager 等) を
    使うケースがあるため、優先順位つきで別名キーを受け付ける。

    Args:
        raw: Claude 応答 dict。
        keys: 優先順のキー名リスト。

    Returns:
        最初にヒットしたキーの値。どれも無ければ None。
    """
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    return None


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
        # Claude が日本語キー (作成日 / 利用者名 / 事業所 等) で返すケースを吸収
        result["date"] = _normalize_date(_first(
            raw,
            "date", "creation_date", "assessment_date",
            "作成日", "記入日", "実施日", "アセスメント日",
        ))
        result["user_name"] = _s(_first(
            raw,
            "user_name", "client_name",
            "利用者名", "氏名", "対象者", "利用者氏名",
        ))
        result["home_name"] = _s(_first(
            raw,
            "home_name", "group_home_name", "office_name",
            "ホーム名", "事業所", "事業所名", "グループホーム名",
        ))

        # 主要項目が全て空なら review_required=true に強制
        if (
            not result["date"]
            and not result["user_name"]
            and not result["home_name"]
        ):
            result["review_required"] = True
            if not result.get("review_comment"):
                result["review_comment"] = (
                    "主要項目が全て空 (Claude キー名不一致の可能性)"
                )

    elif document_type == "plan_draft":
        # Claude がプロンプト指定と違うキー名 (英語別名 / 日本語キー) で返す
        # ケースを吸収。主要項目が全て空なら review_required を強制する。
        result["home_name"] = _s(_first(
            raw,
            "home_name", "group_home_name",
            "グループホーム名", "ホーム名", "事業所名",
        ))
        result["created_date"] = _normalize_date(_first(
            raw,
            "created_date", "creation_month", "creation_date",
            "作成月", "作成日",
        ))
        # 計画期間: 英語キー "plan_period" / 日本語キー "計画期間" 両対応
        result["plan_period"] = _normalize_plan_period(
            _first(raw, "plan_period", "計画期間")
        )
        result["author"] = _s(_first(
            raw,
            "author", "service_manager", "creator",
            "サービス管理責任者", "作成者", "記載者",
        ))

        # 主要項目が全て空なら review_required=true に強制
        # (Claude がキー名不一致の別形式で返し、値を拾えなかった場合の救済)
        if (
            not result["home_name"]
            and not result["created_date"]
            and not result["plan_period"].get("start")
            and not result["plan_period"].get("end")
            and not result["author"]
        ):
            result["review_required"] = True
            if not result.get("review_comment"):
                result["review_comment"] = (
                    "主要項目が全て空 (Claude キー名不一致の可能性)"
                )

    elif document_type == "meeting_record":
        # Claude が日本語キーで返すケースを吸収
        # 開催時間は dict {開始, 終了}、会議出席者は list of dict {職種, 氏名}
        # という複合構造で返ることがある

        result["meeting_date"] = _normalize_date(_first(
            raw, "meeting_date", "開催年月日", "開催日"
        ))
        result["recorder"] = _s(_first(
            raw, "recorder", "記入者", "記録者", "作成者", "記載者"
        ))
        result["location"] = _s(_first(raw, "location", "開催場所"))
        result["user_name"] = _s(_first(raw, "user_name", "利用者名", "対象者"))

        # 開催時間: dict {開始, 終了} / str どちらでも受ける
        mt_val = _first(raw, "meeting_time", "開催時間")
        if isinstance(mt_val, dict):
            s = mt_val.get("開始") or mt_val.get("start") or ""
            e = mt_val.get("終了") or mt_val.get("end") or ""
            if s and e:
                result["meeting_time"] = f"{_s(s)}〜{_s(e)}"
            else:
                result["meeting_time"] = _s(s or e)
        else:
            result["meeting_time"] = _s(mt_val)

        # 参加者: list of dict {職種, 氏名} / list of str / str
        part_val = _first(raw, "participants", "会議出席者", "参加者")
        if isinstance(part_val, list):
            names = []
            for p in part_val:
                if isinstance(p, dict):
                    nm = p.get("氏名") or p.get("name") or ""
                    if nm:
                        names.append(str(nm).strip())
                else:
                    nm = str(p).strip()
                    if nm:
                        names.append(nm)
            result["participants"] = "、".join(names)
        else:
            result["participants"] = _normalize_participants(part_val)

        # 計画期間 (既存ヘルパーが start/end / start_date/end_date /
        # 開始/終了 / 開始日/終了日 を吸収)
        result["plan_period"] = _normalize_plan_period(
            _first(raw, "plan_period", "計画期間")
        )

        # 主要項目が全て空なら review_required=true を強制
        if (
            not result["meeting_date"]
            and not result["recorder"]
            and not result["location"]
            and not result["user_name"]
            and not result["meeting_time"]
            and not result["participants"]
            and not result["plan_period"].get("start")
            and not result["plan_period"].get("end")
        ):
            result["review_required"] = True
            if not result.get("review_comment"):
                result["review_comment"] = (
                    "主要項目が全て空 (Claude キー名不一致の可能性)"
                )

    elif document_type == "plan_final":
        # Claude が日本語キー + ネスト構造で返すケースを吸収
        # 作成者が dict {役職, 氏名, 押印}、同意が dict {同意日, 利用者確認, ...}
        # という複合構造で返ることがある

        result["home_name"] = _s(_first(
            raw,
            "home_name", "group_home_name",
            "グループホーム名", "ホーム名", "事業所名",
        ))
        result["created_date"] = _normalize_date(_first(
            raw,
            "created_date", "creation_date", "creation_month",
            "作成日", "作成月",
        ))
        result["plan_period"] = _normalize_plan_period(
            _first(raw, "plan_period", "計画期間")
        )

        # 作成者: dict {役職, 氏名, 押印} / {役職名: 氏名} / str を吸収
        author_val = _first(
            raw,
            "author", "service_manager", "creator",
            "作成者", "サービス管理責任者", "記載者",
        )
        author_dict = author_val if isinstance(author_val, dict) else None
        if author_dict is not None:
            # 氏名/name を優先、無ければ最初の非空文字列値 (役職名キーで氏名が値)
            author_name = author_dict.get("氏名") or author_dict.get("name")
            if not author_name:
                for v in author_dict.values():
                    if isinstance(v, str) and v.strip() and v.strip() not in (
                        "あり", "なし", "○", "×", "〇", "ｘ",
                    ):
                        author_name = v
                        break
            result["author"] = _s(author_name or "")
        else:
            result["author"] = _s(author_val)

        # 同意関連: dict {同意日, 利用者確認, 署名, 押印, ...} or フラット
        # キー候補: 同意確認 / 同意 / consent (同意確認 を最優先)
        consent_val = _first(raw, "同意確認", "同意", "consent")
        consent_dict = consent_val if isinstance(consent_val, dict) else None
        if consent_dict is not None:
            consent_date_val = (
                consent_dict.get("同意日")
                or consent_dict.get("consent_date")
                or consent_dict.get("日付")
                or ""
            )
            user_confirm = _s(consent_dict.get("利用者確認") or "")
        else:
            consent_date_val = _first(raw, "consent_date", "同意日")
            # フラット構造でも 利用者確認 キーを拾う
            user_confirm = _s(
                _first(raw, "利用者確認", "consent_confirmation") or ""
            )
        result["consent_date"] = _normalize_date(consent_date_val)

        def _pick_nested(*keys):
            """raw 直下 → 同意確認/同意/consent dict 内 → 作成者 dict 内
            の優先順で最初にヒットした値を返す。bool/False も尊重するため
            「キーが存在するか」で判定する (None/"" のみ未設定扱い)。
            """
            for k in keys:
                if k in raw and raw[k] not in (None, ""):
                    return raw[k]
            for src in (consent_dict, author_dict):
                if not src:
                    continue
                for k in keys:
                    if k in src and src[k] not in (None, ""):
                        return src[k]
            # bool False を取りこぼさないため、最後にもう一度 False 許容で探索
            for src in (raw, consent_dict or {}, author_dict or {}):
                for k in keys:
                    if k in src and isinstance(src[k], bool):
                        return src[k]
            return None

        # signature: 明示キー or 同意.利用者確認 の記述から判定
        # 利用者確認 が「氏名（押印あり）」形式の場合、氏名部分が署名相当のため ○
        # (なし/未確認 等の否定語のみ含む場合は署名なし扱い)
        sig_raw = _pick_nested("signature", "署名", "署名有無")
        if sig_raw is not None:
            result["signature"] = _normalize_mark(sig_raw)
        elif user_confirm:
            uc_lower = user_confirm.replace(" ", "").replace("　", "")
            if "署名" in user_confirm or "押印" in user_confirm or "捺印" in user_confirm:
                # 「久島広司（署名・押印あり）」「久島広司（押印あり）」等
                result["signature"] = "○"
            elif uc_lower in ("なし", "無", "未確認", "確認なし", "未"):
                result["signature"] = "×"
            else:
                # 氏名のみ等、非空であれば署名相当とみなす
                result["signature"] = "○"
        else:
            result["signature"] = ""

        # seal: 明示キー or 同意.利用者確認 の記述から判定
        seal_raw = _pick_nested("seal", "捺印", "押印", "捺印有無", "押印有無")
        if seal_raw is not None:
            result["seal"] = _normalize_mark(seal_raw)
        elif user_confirm and ("押印" in user_confirm or "捺印" in user_confirm):
            result["seal"] = "○"
        else:
            result["seal"] = ""

        # 主要項目が全て空なら review_required=true を強制
        if (
            not result["home_name"]
            and not result["created_date"]
            and not result["plan_period"].get("start")
            and not result["plan_period"].get("end")
            and not result["author"]
            and not result["consent_date"]
            and not result["signature"]
            and not result["seal"]
        ):
            result["review_required"] = True
            if not result.get("review_comment"):
                result["review_comment"] = (
                    "主要項目が全て空 (Claude キー名不一致の可能性)"
                )

    elif document_type == "monitoring":
        # Claude が深くネストした日本語構造 (文書情報.xxx) を返すケースを吸収。
        # 文書情報 dict があれば、外側を優先しつつ中身を raw にマージする
        # (フラット化してから通常の別名マッピングで拾う)
        if "文書情報" in raw and isinstance(raw["文書情報"], dict):
            raw = {**raw["文書情報"], **{k: v for k, v in raw.items() if k != "文書情報"}}

        # モニタリング実施者 は dict ({氏名, 役職}) で返ることがあるので氏名を取り出す。
        # extractor の kv-line salvage 経由では括弧付きキー
        # (例:「モニタリング実施者(サービス管理責任者)」「モニタリング実施者（サビ管）」)
        # が現れるため、明示的な variation を追加 + prefix 一致での fallback も用意する。
        monitoring_person = _first(
            raw,
            "モニタリング実施者",
            "モニタリング実施者(サービス管理責任者)",
            "モニタリング実施者（サービス管理責任者）",
            "モニタリング実施者(サビ管)",
            "モニタリング実施者（サビ管）",
            "サービス管理責任者", "実施者",
        )
        # 上記で拾えない場合、"モニタリング実施者" で始まるキーを prefix 一致で探す
        # (括弧の文字バリエーションに頑健な fallback)
        if monitoring_person is None:
            for k, v in raw.items():
                if isinstance(k, str) and k.startswith("モニタリング実施者") and v not in (None, ""):
                    monitoring_person = v
                    break
        if isinstance(monitoring_person, dict):
            monitoring_person = monitoring_person.get("氏名") or ""

        # author の優先順位 (上から順):
        # 1) 既存 author/英語キー
        # 2) 「作成者」「（作成者）」「担当者」「記入者」「実施者」「記載者」
        # 3) モニタリング実施者(...) prefix 一致 (上で計算済 monitoring_person)
        result["author"] = _s(_first(
            raw,
            "author", "service_manager", "creator",
            "作成者", "（作成者）", "(作成者)",
            "担当者", "記入者", "実施者", "記載者",
        )) or _s(monitoring_person)

        # 実施日: 「実施日」「実施年月日」「実施日時」 等を吸収
        result["implementation_date"] = _normalize_date(_first(
            raw,
            "implementation_date",
            "実施日", "実施年月日", "実施日時", "モニタリング実施日",
        ))

        # 参加者: 「参加者」「出席者」「出席者一覧」 等を吸収
        result["participants"] = _normalize_participants(_first(
            raw,
            "participants",
            "参加者", "出席者", "出席者一覧",
        ))

        # 計画期間: plan_period / 計画実施期間 / 計画期間 のいずれか
        # (内部の start/end のキー揺れは _normalize_plan_period が
        #  start/start_date/開始/開始日 / end/end_date/終了/終了日 を吸収する)
        result["plan_period"] = _normalize_plan_period(_first(
            raw, "plan_period", "計画実施期間", "計画期間"
        ))

        # 次回モニタリング: 「次回モニタリング時期/予定」「次回予定」「次回実施予定」 等
        result["next_monitoring_date"] = _normalize_date(_first(
            raw,
            "next_monitoring_date",
            "次回モニタリング時期", "次回モニタリング予定", "次回モニタリング",
            "次回予定", "次回実施予定", "次回実施日", "次回実施時期",
        ))

        # 主要項目が全て空なら review_required=true に強制
        if (
            not result["author"]
            and not result["implementation_date"]
            and not result["participants"]
            and not result["plan_period"].get("start")
            and not result["plan_period"].get("end")
            and not result["next_monitoring_date"]
        ):
            result["review_required"] = True
            if not result.get("review_comment"):
                result["review_comment"] = (
                    "主要項目が全て空 (Claude キー名不一致の可能性)"
                )

    else:
        # unknown 等: そのまま raw を残す
        logger.warning("Unknown document_type in normalize: %s", document_type)
        result["raw"] = raw

    return result
