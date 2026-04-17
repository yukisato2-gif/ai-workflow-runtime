"""運営監査課: 個別支援計画関連書類 PDF 抽出ワークフロー (最小 PoC)。

処理フロー:
  1. 対象フォルダから PDF 一覧を取得 (drive_scanner)
  2. 処理済みファイルをスキップ (state_store)
  3. 書類種別判定 (classifier)
  4. 種別に応じたプロンプトを読込 (extractor.load_prompt)
  5. Claude ブラウザ自動化で PDF → 応答テキスト取得 (claude_runner)
  6. 応答テキストから JSON パース (extractor.parse_claude_response)
  7. 正規化 (normalizer)
  8. Google Sheets へ1行追記 (sheets_writer)
  9. state_store に記録

1件失敗しても全体は止めず、次の PDF へ進む。
"""

from __future__ import annotations

import os
import traceback
from pathlib import Path

from src.common import get_logger

from src.workflows.support_plan_pdf_extraction.drive_scanner import list_pdfs
from src.workflows.support_plan_pdf_extraction.classifier import classify
from src.workflows.support_plan_pdf_extraction.claude_runner import run_claude_on_pdf
from src.workflows.support_plan_pdf_extraction.extractor import (
    load_prompt,
    parse_claude_response,
)
from src.workflows.support_plan_pdf_extraction.normalizer import normalize
from src.workflows.support_plan_pdf_extraction.sheets_writer import append_row
from src.workflows.support_plan_pdf_extraction.state_store import StateStore

logger = get_logger(__name__)


def run_support_plan_workflow(
    folder: Path | None = None,
    state_file: Path | None = None,
) -> dict:
    """対象フォルダ内の PDF を順に処理する。

    Args:
        folder: 対象フォルダ。None の場合は環境変数 SUPPORT_PLAN_INPUT_DIR を参照。
        state_file: 状態ファイルのパス。None の場合は既定を使用。

    Returns:
        {"processed": n, "skipped": m, "failed": f, "unknown": u}
    """
    # 対象フォルダ解決
    if folder is None:
        env_dir = os.getenv("SUPPORT_PLAN_INPUT_DIR", "")
        if not env_dir:
            raise RuntimeError(
                "対象フォルダが指定されていません。引数 folder か "
                "環境変数 SUPPORT_PLAN_INPUT_DIR を設定してください。"
            )
        folder = Path(env_dir)

    logger.info("=" * 60)
    logger.info("  Support Plan PDF Extraction Workflow")
    logger.info("=" * 60)
    logger.info("Target folder: %s", folder)

    store = StateStore(state_file=state_file)

    pdfs = list_pdfs(folder)
    logger.info("Total PDFs found: %d", len(pdfs))

    stats = {"processed": 0, "skipped": 0, "failed": 0, "unknown": 0}

    for idx, pdf_path in enumerate(pdfs, start=1):
        key = str(pdf_path.resolve())
        logger.info("-" * 60)
        logger.info("[%d/%d] %s", idx, len(pdfs), pdf_path.name)

        if store.is_processed(key):
            logger.info("Skip (already processed): %s", pdf_path.name)
            stats["skipped"] += 1
            continue

        try:
            # 1. 書類種別判定
            doc_type = classify(pdf_path)

            if doc_type == "unknown":
                logger.warning("Unknown document type: %s", pdf_path.name)
                normalized = {
                    "document_type": "unknown",
                    "review_required": True,
                    "review_comment": "書類種別を自動判定できませんでした (ファイル名から不明)",
                }
                append_row(pdf_path, normalized)
                store.mark_processed(key)
                stats["unknown"] += 1
                continue

            # 2. プロンプト読込
            prompt = load_prompt(doc_type)

            # 3. Claude 実行 (ブラウザ自動化)
            response = run_claude_on_pdf(pdf_path, prompt)

            # 4. JSON パース
            raw = parse_claude_response(response)

            # 5. 正規化
            normalized = normalize(doc_type, raw)

            # 6. シート追記
            append_row(pdf_path, normalized)

            # 7. 状態保存
            store.mark_processed(key)
            stats["processed"] += 1
            logger.info("Done: %s (type=%s, review=%s)",
                        pdf_path.name, doc_type, normalized.get("review_required"))

        except Exception as e:
            logger.error("Failed to process %s: %s", pdf_path.name, e)
            logger.error(traceback.format_exc())
            # 失敗時にも review_required でシートに記録 (運用追跡用)
            try:
                append_row(
                    pdf_path,
                    {
                        "document_type": "unknown",
                        "review_required": True,
                        "review_comment": f"処理失敗: {e}",
                    },
                )
            except Exception as e2:
                logger.error("Also failed to append error row: %s", e2)
            stats["failed"] += 1
            # 失敗は処理済みにしない (再実行可能にする)

    logger.info("=" * 60)
    logger.info(
        "Summary: processed=%d, skipped=%d, unknown=%d, failed=%d",
        stats["processed"], stats["skipped"], stats["unknown"], stats["failed"],
    )
    logger.info("=" * 60)
    return stats
