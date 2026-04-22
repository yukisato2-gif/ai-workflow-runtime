"""書類種別判定モジュール (最小版)。

ファイル名に含まれるキーワードで書類種別を判定する。
判定基準は cowork-assets schema.yaml の document_types に準拠。

判定不能は "unknown" を返す。unknown は上位で review_required=true となる。

注意:
- macOS APFS は日本語ファイル名を NFD (decomposed) 形式で返すため、
  キーワード (NFC) との substring 一致が成立しない (例: "グ" = "ク+゙")。
  判定前に NFC 正規化を適用する。
"""

import unicodedata
from pathlib import Path

from src.common import get_logger

logger = get_logger(__name__)


# schema.yaml の document_types に対応
# より特徴的なキーワードを持つ種別を先に評価すること
# (例: 「本案」は plan_final が優先 / 「案」は plan_draft)
# plan_draft の「原案」は plan_final の「本案」より先には書かない
# (本案/原案 ともに「案」を含むが、現ルールは前方一致ではなく substring のため、
#  plan_final 側に「本案」を入れて plan_draft より上に置けば優先される)
CLASSIFICATION_RULES: list[tuple[str, list[str]]] = [
    ("assessment", ["アセスメント"]),
    ("meeting_record", ["担当者会議録", "サービス担当者会議", "会議記録", "担当者会議"]),
    ("monitoring", ["モニタリング"]),
    # plan_draft (案/原案系) を plan_final より先に評価:
    # 「個別支援計画書（案）」が plan_final の「個別支援計画書」より優先される必要があるため
    ("plan_draft", ["個別支援計画書（案）", "計画書案", "原案", "計画書(案)"]),
    # plan_final (本案系) は plan_draft の後に評価
    ("plan_final", ["個別支援計画書", "個別支援計画（本案）", "本案"]),
]


def classify(pdf_path: Path) -> str:
    """PDF ファイル名から書類種別を判定する。

    Args:
        pdf_path: PDF ファイルのパス。

    Returns:
        書類種別 ID: assessment / plan_draft / meeting_record /
                     plan_final / monitoring / unknown
    """
    # macOS の NFD 形式 (例: グ = ク + ゙) を NFC に揃えてから比較する
    name = unicodedata.normalize("NFC", pdf_path.name)

    for type_id, keywords in CLASSIFICATION_RULES:
        for kw in keywords:
            if kw in name:
                logger.info("Classified %s as %s (keyword: %s)", name, type_id, kw)
                return type_id

    logger.warning("Cannot classify %s → unknown", name)
    return "unknown"
