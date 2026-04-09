"""モニタリング記録の抽出結果スキーマ定義モジュール。

Claude による PDF テキスト抽出結果のデータ構造を定義する。
"""

from pydantic import BaseModel, Field


class MonitoringRecord(BaseModel):
    """モニタリング記録の抽出結果スキーマ。"""

    document_type: str = Field(..., description="文書種別（モニタリング記録）")
    person_name: str | None = Field(None, description="氏名")
    implementation_date: str | None = Field(None, description="実施日")
    participants: list[str] | None = Field(None, description="参加者リスト")
    next_monitoring_date: str | None = Field(None, description="次回モニタリング時期")
    author: str | None = Field(None, description="モニタリング実施者")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="信頼度スコア (0.0〜1.0)"
    )
