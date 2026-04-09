"""バリデーションルールモジュール。

抽出結果に対する簡易バリデーションルールを提供する。
"""

from src.common import get_logger, ValidationError
from src.schemas import ExtractionResult

logger = get_logger(__name__)


def validate_extraction_result(result: ExtractionResult) -> None:
    """抽出結果のバリデーションを行う。

    以下のルールを検証する:
    - items が 1 件以上存在すること
    - 全項目の confidence が 0.5 以上であること

    Args:
        result: 検証対象の抽出結果。

    Raises:
        ValidationError: バリデーションに失敗した場合。
    """
    logger.info("Validating extraction result for: %s", result.source_file)

    if len(result.items) == 0:
        raise ValidationError("Extraction result has no items")

    low_confidence_items = [
        item for item in result.items if item.confidence < 0.5
    ]
    if low_confidence_items:
        keys = [item.key for item in low_confidence_items]
        raise ValidationError(
            f"Low confidence items detected: {keys}"
        )

    logger.info("Validation passed (%d items)", len(result.items))
