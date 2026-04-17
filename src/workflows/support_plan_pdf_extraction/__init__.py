"""運営監査課: 個別支援計画関連書類 PDF 抽出ワークフロー。

対象フォルダ内の PDF を書類種別判定し、Claude (ブラウザ自動化) で
構造化抽出を行い、Google Sheets へ追記する。

詳細は README.md を参照。
"""

from src.workflows.support_plan_pdf_extraction.workflow import (
    run_support_plan_workflow,
)

__all__ = ["run_support_plan_workflow"]
