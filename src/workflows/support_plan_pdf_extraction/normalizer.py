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


def _split_date_and_time(value: Any) -> tuple[str, str]:
    """meeting_record 用: 「2025年3月24日 13:30〜14:30」のように
    開催日キー値に時間が混入しているケースを (date_part, time_part) に分離する。

    - 値が文字列でない / 空 / 時間表記がない → (元値の文字列, "")
    - 時間表記 (HH:MM[:SS] 〜 HH:MM[:SS] など) を検出した場合のみ分離
    """
    if value is None:
        return "", ""
    s = str(value).strip()
    if not s:
        return "", ""
    # HH:MM[:SS] 〜/～/~/-/− HH:MM[:SS] (前後に空白可)
    m = re.search(
        r"(\d{1,2}:\d{2}(?::\d{2})?\s*[〜～~\-ー－]\s*\d{1,2}:\d{2}(?::\d{2})?)",
        s,
    )
    if m:
        time_part = m.group(1).strip()
        date_part = s[: m.start()].strip()
        # 末尾の助詞「、」「・」「(」 等を削る
        date_part = re.sub(r"[、,\s\(（]+$", "", date_part)
        return date_part, time_part
    return s, ""


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

        # Fallback: Claude が「氏名 作成日: 2025/3/24 事業所: AMANEKU平塚 …」
        # のように利用者名セルの値に他フィールドを詰め込んで返すケースを救済。
        # ラベル付きセグメントを切り出して date / home_name を取り出し、
        # user_name は氏名部分だけに整形する。
        if result["user_name"] and re.search(
            r"(?:作成日|記入日|実施日|事業所(?:名)?|ホーム名|グループホーム名|"
            r"作成者(?:名)?|担当者|サービス管理責任者)\s*[:：]",
            result["user_name"],
        ):
            label_pat = (
                r"(作成日|記入日|実施日|事業所(?:名)?|ホーム名|"
                r"グループホーム名|作成者(?:名)?|担当者|サービス管理責任者)"
            )
            parts = re.split(rf"\s*{label_pat}\s*[:：]\s*", result["user_name"])
            # parts[0] = 氏名、以降は (label, value) ペアで交互に並ぶ
            if len(parts) >= 3:
                name_only = parts[0].strip()
                if name_only:
                    result["user_name"] = name_only
                # 後続の (label, value) を順次処理
                for i in range(1, len(parts) - 1, 2):
                    label = parts[i]
                    value = parts[i + 1].strip()
                    # 末尾は次のラベルまでなので、空白で区切られた最初の語まで
                    # を value とする (例: "AMANEKU平塚 作成者名" → "AMANEKU平塚")
                    if " " in value:
                        value = value.split()[0]
                    if not value:
                        continue
                    if label in ("作成日", "記入日", "実施日") and not result["date"]:
                        result["date"] = _normalize_date(value)
                    elif label in (
                        "事業所", "事業所名", "ホーム名", "グループホーム名"
                    ) and not result["home_name"]:
                        result["home_name"] = value

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
        # 計画期間: 「plan_period」「計画期間」「計画実施期間」「実施期間」
        # 「支援期間」「サービス提供期間」「契約期間」「期間」
        period_raw = _first(
            raw,
            "plan_period",
            "計画期間", "計画実施期間", "実施期間", "支援期間",
            "サービス提供期間", "契約期間", "期間",
        )
        # Fallback: 明示的な計画期間キーが無い場合、トップレベル文字列値の中から
        # 「YYYY...〜YYYY...」形式を含むものを採用 (右上 free-text 期間想定)。
        # 先に「実施日」「開催日」「作成日」等の単一日付キーは除外する。
        if period_raw is None:
            _SKIP_KEYS = {
                "meeting_date", "implementation_date", "created_date",
                "consent_date", "next_monitoring_date", "date",
                "実施日", "実施年月日", "開催日", "開催年月日",
                "作成日", "作成月", "同意日", "次回モニタリング時期",
            }
            _PERIOD_RE = re.compile(
                r"\d{4}[-/年].{0,12}[〜～~\-ー－].{0,12}\d{1,2}[-/月]"
            )
            for k, v in raw.items():
                if k in _SKIP_KEYS:
                    continue
                if isinstance(v, str) and _PERIOD_RE.search(v):
                    period_raw = v
                    logger.info(
                        "[normalizer/plan_draft] plan_period free-text fallback: "
                        "key=%r value=%r", k, v[:80],
                    )
                    break
        result["plan_period"] = _normalize_plan_period(period_raw)
        # 作成者: 優先順位 (spec)
        #   サービス管理責任者 > 作成者 > 記入者 > 担当者 > 記載者
        # 英語別名 (author / service_manager / creator) は最優先の上位互換として
        # 最前段に置く (Claude がプロンプトどおりに author を返した場合に即採用)。
        # 末尾の「（印）」「(印)」「（押印）」「（印影）」等の捺印注記は剥がす。
        author_raw = _s(_first(
            raw,
            "author", "service_manager", "creator",
            "サービス管理責任者", "サビ管",
            "作成者", "記入者", "担当者", "記載者",
        ))
        if author_raw:
            # 末尾の (印)/（印）/(押印)/（押印あり）/(印影)/（印影） を剥がす
            author_raw = re.sub(
                r"\s*[（(](印|押印|押印あり|印影)\s*[)）]\s*$",
                "",
                author_raw,
            ).strip()
        result["author"] = author_raw

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
        # Claude が「開催情報」dict で開催年月日/開催時間/開催場所をネストして
        # 返すケースを吸収。文書情報 と同様、外側を優先しつつ raw に flat マージ。
        if "開催情報" in raw and isinstance(raw["開催情報"], dict):
            raw = {**raw["開催情報"],
                   **{k: v for k, v in raw.items() if k != "開催情報"}}

        # Claude が日本語キーで返すケースを吸収
        # 開催時間は dict {開始, 終了}、会議出席者は list of dict {職種, 氏名}
        # という複合構造で返ることがある

        # 開催日: 「開催日」「開催年月日」「実施日」「実施日時」 等を吸収。
        # 値が「2025年3月24日 13:30〜14:30」のように日付+時間混在の場合は、
        # 先頭の日付部分のみ meeting_date に渡し、時間部分は後段の
        # meeting_time fallback で利用する。
        date_raw = _first(
            raw, "meeting_date",
            "開催年月日", "開催日",
            "実施日", "実施日時",
        )
        date_part, time_tail = _split_date_and_time(date_raw)
        result["meeting_date"] = _normalize_date(date_part)

        # 記録者: 「記録者」「作成者」「担当者」「記入者」「記載者」 等を吸収
        # (spec 優先順: 記録者 > 作成者 > 担当者; 旧 「記入者/記載者」 は後方)
        result["recorder"] = _s(_first(
            raw, "recorder",
            "記録者", "作成者", "担当者",
            "記入者", "記載者",
        ))
        # 開催場所: 「開催場所」「場所」「会場」 等を吸収
        result["location"] = _s(_first(
            raw, "location",
            "開催場所", "場所", "会場",
        ))
        result["user_name"] = _s(_first(raw, "user_name", "利用者名", "対象者"))

        # 開催時間: 「開催時間」「会議時間」「実施時間」 等を吸収
        # dict {開始, 終了} / str どちらでも受ける。空なら開催日に混入していた
        # 時間部分 (上で分離済) を利用。
        mt_val = _first(
            raw, "meeting_time",
            "開催時間", "会議時間", "実施時間",
        )
        if isinstance(mt_val, dict):
            s = mt_val.get("開始") or mt_val.get("start") or ""
            e = mt_val.get("終了") or mt_val.get("end") or ""
            if s and e:
                result["meeting_time"] = f"{_s(s)}〜{_s(e)}"
            else:
                result["meeting_time"] = _s(s or e)
        else:
            result["meeting_time"] = _s(mt_val)
        if not result["meeting_time"] and time_tail:
            result["meeting_time"] = time_tail

        # 参加者: list of dict {職種, 氏名} / list of str / str
        # キー揺れ: 「参加者」「出席者」「出席者一覧」「会議出席者」 等
        part_val = _first(
            raw,
            "participants",
            "参加者", "出席者", "出席者一覧", "会議出席者",
        )
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

        # Fallback: participants が空の時、Claude が「会議出席者」表の各行を
        # 個別の「職種 → 氏名」トップレベルキーに分解して返したケースを救済。
        # 既知の職種キーを raw から拾い集めて、上から順に「、」連結する。
        if not result["participants"]:
            _MEETING_ROLE_KEYS = (
                "サービス管理責任者", "サビ管",
                "管理者", "施設長", "ホーム長",
                "生活支援員", "支援員", "世話人",
                "看護師", "医師",
                "相談員", "相談支援員", "相談支援専門員",
                "本人", "ご本人", "利用者",
                "保護者", "家族", "親族",
            )

            def _is_real_name(s: str) -> bool:
                """メタ注記 (例:「（記載なし）」「（空欄）」「（出席あり／氏名欄空欄）」)
                を氏名候補から除外する簡易判定。"""
                t = s.strip()
                if not t:
                    return False
                # 全角/半角の括弧で囲まれた注記を除外
                if (t.startswith("（") and t.endswith("）")) or \
                   (t.startswith("(") and t.endswith(")")):
                    return False
                # 注記フレーズを含む値も除外
                if any(kw in t for kw in ("記載なし", "空欄", "不明", "該当なし", "未記入")):
                    return False
                return True

            collected: list[str] = []
            for k in _MEETING_ROLE_KEYS:
                v = raw.get(k)
                if isinstance(v, str) and _is_real_name(v):
                    collected.append(v.strip())
                elif isinstance(v, list):
                    for x in v:
                        if isinstance(x, str) and _is_real_name(x):
                            collected.append(x.strip())
            if collected:
                result["participants"] = "、".join(collected)

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
        # 計画期間: 「支援計画期間」「計画実施期間」「実施期間」「支援期間」も吸収
        result["plan_period"] = _normalize_plan_period(_first(
            raw,
            "plan_period",
            "計画期間", "支援計画期間", "計画実施期間", "実施期間", "支援期間",
        ))

        # 作成者: dict {役職, 氏名, 押印} / {役職名: 氏名} / str を吸収
        # 優先順: サービス管理責任者 > 作成者 > 記入者 > 担当者 > 記載者
        # 加えて、Claude が「作成者（サービス管理責任者）」のように括弧付き
        # キーで返すケースに対応するため、prefix 一致 fallback も持つ。
        author_val = _first(
            raw,
            "author", "service_manager", "creator",
            "サービス管理責任者", "サビ管",
            "作成者", "記入者", "担当者", "記載者",
        )
        # Fallback: 上記で拾えない場合、「作成者」「サービス管理責任者」で
        # 始まるキー (例:「作成者（サービス管理責任者）」「サービス管理責任者(印)」)
        # を prefix-match で探す
        if author_val is None:
            for k, v in raw.items():
                if not isinstance(k, str):
                    continue
                if (k.startswith("作成者") or k.startswith("サービス管理責任者")) \
                        and v not in (None, ""):
                    author_val = v
                    break

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

        # 末尾の「（印）」「(印)」「（押印）」「（押印あり）」「（印影）」 等の
        # 捺印注記は剥がして氏名のみ残す (plan_draft と同じ規則)
        if result["author"]:
            result["author"] = re.sub(
                r"\s*[（(](印|押印|押印あり|印影)\s*[)）]\s*$",
                "",
                result["author"],
            ).strip()

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

        # 参加者の生値を先に取得 (author 汚染チェックに使用)。
        # 後段で _normalize_participants で正規化されるが、ここでは生値が必要。
        _participants_raw = _first(
            raw,
            "participants",
            "参加者", "出席者", "出席者一覧", "関係者",
        )

        def _is_participant_contaminated(name: str) -> bool:
            """値が参加者欄由来 (役割語混入/「、」区切り/参加者名一致) か判定。

            monitoring の author に参加者欄の支援員名・施設長名・本人 を採用しないための
            防御フィルタ。author 候補として明らかに不適切な値を除外する。
            """
            if not isinstance(name, str):
                return False
            s = name.strip()
            if not s:
                return False
            # 「、」「,」区切り → 複数名 = 参加者リスト
            if "、" in s or "," in s:
                return True
            # 役割語が混入 → 参加者欄ラベル
            for marker in ("支援員", "施設長", "本人", "保護者", "家族", "ご本人"):
                if marker in s:
                    return True
            # 参加者リストに含まれる氏名と完全一致 → 参加者由来
            if isinstance(_participants_raw, list):
                for p in _participants_raw:
                    if isinstance(p, str) and p.strip() == s:
                        return True
            elif isinstance(_participants_raw, str):
                for p in re.split(r"[、,]\s*", _participants_raw):
                    if p.strip() == s:
                        return True
            return False

        def _resolve_author_value(value):
            """raw value を str author 候補に変換 (dict なら 氏名 を取り出す)。"""
            if isinstance(value, dict):
                v = value.get("氏名") or value.get("name") or ""
                if not v:
                    for vv in value.values():
                        if isinstance(vv, str) and vv:
                            v = vv
                            break
                return _s(v)
            return _s(value)

        # author の優先順位 (上位ほど優先):
        #   1位: モニタリング実施者（サービス管理責任者） / (サビ管)
        #        ↑ 帳票上明示の実施者氏名
        #   2位: モニタリング実施者 (素キー or 1位以外のプレフィックス一致)
        #   3位: サービス管理責任者
        #   4位: author / service_manager / creator / 作成者 (Claude canonical キー)
        #   5位: 記入者 / 記載者
        #   6位: 担当者 / 実施者
        # 各候補に対し、参加者欄由来 (支援員/施設長/本人/「、」区切り/
        # 参加者リスト一致) を _is_participant_contaminated で除外。
        # 監査帳票の上部押印欄ラベル (管理者/サビ管 単独) は元々候補に入っていない。
        _p1_keys = {
            "モニタリング実施者(サービス管理責任者)",
            "モニタリング実施者（サービス管理責任者）",
            "モニタリング実施者(サビ管)",
            "モニタリング実施者（サビ管）",
        }
        _p1 = _first(
            raw,
            "モニタリング実施者(サービス管理責任者)",
            "モニタリング実施者（サービス管理責任者）",
            "モニタリング実施者(サビ管)",
            "モニタリング実施者（サビ管）",
        )
        _p2 = _first(raw, "モニタリング実施者")
        if _p2 is None:
            # プレフィックス一致 fallback (priority 1 で拾うキーは除外)
            for k, v in raw.items():
                if not isinstance(k, str):
                    continue
                if k in _p1_keys or k == "モニタリング実施者":
                    continue
                if k.startswith("モニタリング実施者") and v not in (None, ""):
                    _p2 = v
                    break
        _p3 = _first(raw, "サービス管理責任者")
        _p4 = _first(
            raw,
            "author", "service_manager", "creator",
            "作成者", "（作成者）", "(作成者)",
        )
        _p5 = _first(raw, "記入者", "記載者")
        _p6 = _first(raw, "担当者", "実施者")

        # 上位から順に評価し、参加者汚染されていない最初のものを採用
        result["author"] = ""
        for _cand in (_p1, _p2, _p3, _p4, _p5, _p6):
            _v = _resolve_author_value(_cand)
            if _v and not _is_participant_contaminated(_v):
                result["author"] = _v
                break

        # 実施日: 「実施日」「実施年月日」「実施日時」 等を吸収
        result["implementation_date"] = _normalize_date(_first(
            raw,
            "implementation_date",
            "実施日", "実施年月日", "実施日時", "モニタリング実施日",
        ))

        # 参加者: 「参加者」「出席者」「出席者一覧」「関係者」 等を吸収
        result["participants"] = _normalize_participants(_first(
            raw,
            "participants",
            "参加者", "出席者", "出席者一覧", "関係者",
        ))

        # 計画期間: plan_period / 計画実施期間 / 実施期間 / 支援期間 / 計画期間 のいずれか
        # (内部の start/end のキー揺れは _normalize_plan_period が
        #  start/start_date/開始/開始日 / end/end_date/終了/終了日 を吸収する)
        # monitoring 限定: 「2026年3月31日まで」のように末尾の自由語
        # (まで/迄/頃/予定) が付くケースを _normalize_date 前に除去する
        period_raw = _first(
            raw,
            "plan_period",
            "計画実施期間", "実施期間", "支援期間", "計画期間",
        )
        _SUFFIXES = ("まで", "迄", "頃", "予定")

        def _strip_period_suffix(v):
            if not isinstance(v, str):
                return v
            s = v.strip()
            for suf in _SUFFIXES:
                if s.endswith(suf):
                    s = s[:-len(suf)].strip()
                    break
            return s

        if isinstance(period_raw, str):
            # 「A 〜 B」の前後に suffix が付くケース: 分割→各端 strip→再結合
            parts = re.split(r"\s*[〜～~\-ー－]\s*", period_raw, maxsplit=1)
            parts = [_strip_period_suffix(p) for p in parts]
            period_raw = "〜".join(parts) if len(parts) == 2 else parts[0]
        elif isinstance(period_raw, dict):
            period_raw = {k: _strip_period_suffix(v) for k, v in period_raw.items()}

        result["plan_period"] = _normalize_plan_period(period_raw)

        # 次回モニタリング: 「次回モニタリング時期/予定」「次回予定」「次回実施予定」「次回確認」 等
        result["next_monitoring_date"] = _normalize_date(_first(
            raw,
            "next_monitoring_date",
            "次回モニタリング時期", "次回モニタリング予定", "次回モニタリング",
            "次回予定", "次回実施予定", "次回確認",
            "次回実施日", "次回実施時期",
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
