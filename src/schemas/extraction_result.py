"""抽出結果のスキーマ定義モジュール。

Claude による PDF テキスト抽出結果のデータ構造を定義する。
"""

from pydantic import BaseModel, Field


class ExtractedItem(BaseModel):
    """抽出された個別項目。"""

    key: str = Field(..., description="項目名")
    value: str = Field(..., description="抽出された値")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="信頼度スコア (0.0〜1.0)"
    )


class ExtractionResult(BaseModel):
    """抽出結果全体のスキーマ。"""

    source_file: str = Field(..., description="元ファイルパス")
    items: list[ExtractedItem] = Field(default_factory=list, description="抽出項目リスト")
    raw_text: str = Field(default="", description="元テキスト（参考用）")
