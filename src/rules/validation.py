"""バリデーションルールモジュール。

抽出結果に対する簡易バリデーションルールを提供する。
"""

from src.common import get_logger, ValidationError
from src.schemas import MonitoringRecord

logger = get_logger(__name__)


def validate_monitoring_record(record: MonitoringRecord) -> None:
    """モニタリング記録の抽出結果を検証する。

    以下のルールを検証する:
    - document_type が「モニタリング記録」であること
    - confidence が 0.5 以上であること

    Args:
        record: 検証対象のモニタリング記録。

    Raises:
        ValidationError: バリデーションに失敗した場合。
    """
    logger.info("Validating monitoring record: %s", record.person_name)

    if record.document_type != "モニタリング記録":
        raise ValidationError(
            f"Invalid document_type: expected 'モニタリング記録', got '{record.document_type}'"
        )

    if record.confidence < 0.5:
        raise ValidationError(
            f"Low confidence: {record.confidence}"
        )

    logger.info("Validation passed (confidence=%.2f)", record.confidence)
