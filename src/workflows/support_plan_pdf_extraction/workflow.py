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
import time
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

# Claude 呼出後の待機秒数 (連続実行時の navigation abort 防止用)
# 環境変数 SUPPORT_PLAN_POST_CLAUDE_SLEEP で上書き可能
POST_CLAUDE_SLEEP_SEC = int(os.getenv("SUPPORT_PLAN_POST_CLAUDE_SLEEP", "5"))

# 自然文応答になった場合の 1 回だけ再送するリトライプロンプト
# 既存の load_prompt() 経由のプロンプトが JSON 化されなかったケース向け。
# (browser_reader の契約上、厳密な「同一チャット内再送」は困難なため、
#  新しいチャットを開いて強力な JSON-only プロンプトで再添付して再送する)
RETRY_JSON_ONLY_PROMPT = """前回の内容を、説明・挨拶・コードフェンスなしで、JSONのみで出力し直してください。
回答は必ず { で始まる単一のJSONオブジェクトだけにしてください。
不明値は null にしてください。
JSON以外の文字を1文字も含めないでください。
"""


def run_support_plan_workflow(
    folder: Path | None = None,
    state_file: Path | None = None,
    pdf_file: Path | None = None,
) -> dict:
    """対象の PDF を処理する。

    folder / pdf_file のいずれかを指定する (両方省略時は環境変数 SUPPORT_PLAN_INPUT_DIR)。
    pdf_file が指定された場合はそのファイル 1 件のみを処理する (単一 PDF 完走検証用)。

    Args:
        folder: 対象フォルダ。配下の PDF を再帰的に列挙して処理する。
        state_file: 状態ファイルのパス。None の場合は既定を使用。
        pdf_file: 単一 PDF ファイルのパス。指定時は folder よりも優先。

    Returns:
        {"processed": n, "skipped": m, "failed": f, "unknown": u}
    """
    # 処理対象 PDF の解決
    if pdf_file is not None:
        if not pdf_file.exists() or not pdf_file.is_file():
            raise RuntimeError(f"PDF ファイルが見つかりません: {pdf_file}")
        pdfs = [pdf_file.resolve()]
        target_label = f"single PDF: {pdf_file}"
    else:
        if folder is None:
            env_dir = os.getenv("SUPPORT_PLAN_INPUT_DIR", "")
            if not env_dir:
                raise RuntimeError(
                    "対象が指定されていません。pdf_file / folder / "
                    "環境変数 SUPPORT_PLAN_INPUT_DIR のいずれかを設定してください。"
                )
            folder = Path(env_dir)
        pdfs = list_pdfs(folder)
        target_label = f"folder: {folder}"

    logger.info("=" * 60)
    logger.info("  Support Plan PDF Extraction Workflow")
    logger.info("  (方式: ローカルPDFを Claude ブラウザへ添付して読む)")
    logger.info("=" * 60)
    logger.info("Target: %s", target_label)

    store = StateStore(state_file=state_file)

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

            # 3. Claude 実行 (ブラウザ自動化: PDF を Web UI に添付)
            logger.info("Claude ブラウザへ PDF 添付処理を開始: %s (type=%s)",
                        pdf_path.name, doc_type)
            response = run_claude_on_pdf(pdf_path, prompt)

            # 3.5 連続実行時の navigation abort 防止のため待機
            # (Playwright CDP の前の遷移が完了する前に次の goto が走るのを防ぐ)
            logger.info("sleep %d 秒開始 (次のPDF処理までのクールダウン)",
                        POST_CLAUDE_SLEEP_SEC)
            time.sleep(POST_CLAUDE_SLEEP_SEC)
            logger.info("sleep 終了")

            # 4. JSON パース (失敗時は 1 回だけ強力 JSON プロンプトで再送)
            try:
                raw = parse_claude_response(response)
            except Exception as parse_err:
                logger.warning(
                    "JSON パース失敗 → 強力プロンプトで 1 回だけ再送: %s",
                    parse_err,
                )
                time.sleep(POST_CLAUDE_SLEEP_SEC)
                retry_response = run_claude_on_pdf(pdf_path, RETRY_JSON_ONLY_PROMPT)
                logger.info("sleep %d 秒開始 (リトライ後クールダウン)",
                            POST_CLAUDE_SLEEP_SEC)
                time.sleep(POST_CLAUDE_SLEEP_SEC)
                logger.info("sleep 終了")
                # 2 回目も失敗したら例外を再送 (外側の except Exception で捕捉 →
                # 従来の review_required=true 追記パスへ)
                raw = parse_claude_response(retry_response)
                logger.info("リトライで JSON 化成功")

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
