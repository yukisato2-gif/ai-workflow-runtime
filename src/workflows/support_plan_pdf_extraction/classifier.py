"""書類種別判定モジュール (最小版)。

ファイル名に含まれるキーワードで書類種別を判定する。
判定基準は cowork-assets schema.yaml の document_types に準拠。

判定不能は "unknown" を返す。unknown は上位で review_required=true となる。
"""

from pathlib import Path

from src.common import get_logger

logger = get_logger(__name__)


# schema.yaml の document_types に対応
# より特徴的なキーワードを持つ種別を先に評価すること
# (例: 「本案」は plan_final が優先 / 「案」は plan_draft)
CLASSIFICATION_RULES: list[tuple[str, list[str]]] = [
    ("assessment", ["アセスメント"]),
    ("meeting_record", ["担当者会議録", "サービス担当者会議", "会議記録"]),
    ("monitoring", ["モニタリング"]),
    ("plan_draft", ["個別支援計画書（案）", "計画書案", "原案", "計画書(案)"]),
    ("plan_final", ["個別支援計画書"]),  # 本案 (案が付かない)
]


def classify(pdf_path: Path) -> str:
    """PDF ファイル名から書類種別を判定する。

    Args:
        pdf_path: PDF ファイルのパス。

    Returns:
        書類種別 ID: assessment / plan_draft / meeting_record /
                     plan_final / monitoring / unknown
    """
    name = pdf_path.name

    # 計画書案は plan_final と前方一致で区別する必要があるため先に判定
    for type_id, keywords in CLASSIFICATION_RULES:
        for kw in keywords:
            if kw in name:
                logger.info("Classified %s as %s (keyword: %s)", name, type_id, kw)
                return type_id

    logger.warning("Cannot classify %s → unknown", name)
    return "unknown"
