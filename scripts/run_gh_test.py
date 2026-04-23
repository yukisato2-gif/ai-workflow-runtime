"""GH 全拠点本番展開前のテスト実行スクリプト (フェーズ 1+2+3)。

フォルダ構造前提 (ハードコード):
    <DRIVE_ROOT>/共有ドライブ/001_100_0XX_GH<拠点名>/
                                  └ 032_個別支援計画関連PDF格納フォルダ/
                                      └ *.pdf

スキャンルール:
- 「001_100_」で始まるフォルダのみ対象 (000_/共通フォルダ等は除外)
- その直下の「032_個別支援計画関連PDF格納フォルダ」のみ対象
- 配下の *.pdf のみ対象
- 上記以外は無視

3 つのフェーズ:
- Phase 1 (--scan): 全拠点を列挙してスキャン結果のみ表示。workflow は実行しない
- Phase 2 (--limit): N 拠点 × M ファイル に絞って既存 workflow を実行
- Phase 3: Phase 2 完了後に集計サマリ表示 (拠点別/帳票別/retry/duplicate)

使い方:
    # フェーズ 1: スキャンのみ
    python scripts/run_gh_test.py --scan

    # フェーズ 2: 3拠点 × 各10ファイルに限定して実行 (既定値と一致)
    python scripts/run_gh_test.py --limit --max-sites 3 --max-files 10

    # 別パラメータ例
    python scripts/run_gh_test.py --limit --max-sites 1 --max-files 5
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv  # noqa: E402

from src.common import get_logger  # noqa: E402

# 既存のワークフロー部品を再利用 (新規ロジックは作らない)
from src.workflows.support_plan_pdf_extraction.classifier import classify  # noqa: E402
from src.workflows.support_plan_pdf_extraction.claude_runner import (  # noqa: E402
    run_claude_on_pdf,
)
from src.workflows.support_plan_pdf_extraction.extractor import (  # noqa: E402
    load_prompt,
    parse_claude_response,
)
from src.workflows.support_plan_pdf_extraction.normalizer import normalize  # noqa: E402
from src.workflows.support_plan_pdf_extraction.sheets_writer import append_row  # noqa: E402
from src.workflows.support_plan_pdf_extraction.state_store import StateStore  # noqa: E402
from src.workflows.support_plan_pdf_extraction.workflow import (  # noqa: E402
    POST_CLAUDE_SLEEP_SEC,
    RETRY_JSON_ONLY_PROMPT,
    _missing_required_fields,
)

logger = get_logger(__name__)


# === ハードコード仕様 ===
SHARED_DRIVE_ROOT = Path(
    "/Users/administrator/Library/CloudStorage/"
    "GoogleDrive-yuki.sato2@amatuhi.co.jp/共有ドライブ"
)
SITE_FOLDER_RE = re.compile(r"^001_100_\d{3}_")
TARGET_SUBFOLDER = "032_個別支援計画関連PDF格納フォルダ"


def discover_sites() -> list[tuple[str, Path]]:
    """共有ドライブ直下から「001_100_XXX_*」拠点フォルダを抽出。

    Returns:
        [(拠点フォルダ名, target_subfolder の Path), ...] (ソート済)
        target_subfolder が存在しない拠点も含めて返す。
    """
    if not SHARED_DRIVE_ROOT.exists():
        logger.error("共有ドライブが見つかりません: %s", SHARED_DRIVE_ROOT)
        return []
    sites: list[tuple[str, Path]] = []
    for child in sorted(SHARED_DRIVE_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if not SITE_FOLDER_RE.match(child.name):
            continue
        sites.append((child.name, child / TARGET_SUBFOLDER))
    return sites


def list_pdfs_in_target(target_dir: Path) -> list[Path]:
    """指定 target フォルダ直下の *.pdf のみ列挙 (再帰しない)。"""
    if not target_dir.exists() or not target_dir.is_dir():
        return []
    return sorted(p for p in target_dir.glob("*.pdf") if p.is_file())


# === Phase 1 ===

def phase1_scan() -> dict[str, list[Path]]:
    """全拠点を列挙してスキャン結果を返す + 標準出力に表示。"""
    sites = discover_sites()
    site_pdfs: dict[str, list[Path]] = {}
    for name, target in sites:
        pdfs = list_pdfs_in_target(target)
        site_pdfs[name] = pdfs

    total_pdfs = sum(len(v) for v in site_pdfs.values())
    sites_with_pdfs = [n for n, v in site_pdfs.items() if v]

    print("=" * 70)
    print("  Phase 1: スキャン結果")
    print("=" * 70)
    print(f"  対象拠点総数 (001_100_): {len(sites)}")
    print(f"  対象フォルダがある拠点  : {len(sites_with_pdfs)}")
    print(f"  対象フォルダ無しの拠点  : {len(sites) - len(sites_with_pdfs)}")
    print(f"  総 PDF 数              : {total_pdfs}")
    print()
    print("  --- 拠点別 PDF 件数 ---")
    for name, pdfs in site_pdfs.items():
        marker = " " if pdfs else "✗"
        print(f"  [{marker}] {name:40s} {len(pdfs):4d} PDF")
    print()
    print("  --- サンプルファイル名 (各拠点先頭1件まで、最大5件) ---")
    sampled = 0
    for name, pdfs in site_pdfs.items():
        if not pdfs:
            continue
        if sampled >= 5:
            break
        print(f"  - {name} / {pdfs[0].name}")
        sampled += 1
    print("=" * 70)
    return site_pdfs


# === Phase 2 + 3 ===

def _process_one_pdf(pdf_path: Path, store: StateStore, stats: dict) -> None:
    """1 件の PDF を既存ワークフロー流儀で処理する。

    workflow.run_support_plan_workflow と同じ手順を踏むが、状態管理 +
    集計だけテスト用 stats に書き込む。Claude 呼出回数は PDF あたり最大 2 回
    (parse retry または REQUIRED 欠損 retry のいずれか) を維持。
    """
    key = str(pdf_path.resolve())
    stats["claude_calls_per_pdf"] = 0  # 個別 PDF のクレジット (報告用)

    if store.is_processed(key):
        logger.info("Skip (already processed): %s", pdf_path.name)
        stats["skipped"] += 1
        return

    try:
        # 1. 書類種別判定
        doc_type = classify(pdf_path)
        stats["by_type"][doc_type] += 1

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
            return

        # 2. プロンプト読込
        prompt = load_prompt(doc_type)

        # 3. Claude 実行
        logger.info("Claude ブラウザへ PDF 添付処理を開始: %s (type=%s)",
                    pdf_path.name, doc_type)
        response = run_claude_on_pdf(pdf_path, prompt)
        stats["claude_calls_per_pdf"] += 1
        time.sleep(POST_CLAUDE_SLEEP_SEC)

        # 4. JSON parse (parse 失敗 → 1 回だけ強力プロンプトで再送)
        retried_once = False
        try:
            raw = parse_claude_response(response)
        except Exception as parse_err:
            logger.warning("JSON パース失敗 → retry: %s", parse_err)
            time.sleep(POST_CLAUDE_SLEEP_SEC * 3)
            retry_response = run_claude_on_pdf(pdf_path, RETRY_JSON_ONLY_PROMPT)
            stats["claude_calls_per_pdf"] += 1
            time.sleep(POST_CLAUDE_SLEEP_SEC)
            raw = parse_claude_response(retry_response)
            retried_once = True
            stats["retry_parse"] += 1

        # 5. 正規化
        normalized = normalize(doc_type, raw)

        # 5.5 共通 retry_once: REQUIRED_FIELDS 欠損で 1 回 retry (budget 共有)
        if not retried_once:
            missing = _missing_required_fields(doc_type, normalized)
            if missing:
                logger.info(
                    "retry_once: required fields missing → retry (%s)",
                    ", ".join(missing),
                )
                time.sleep(POST_CLAUDE_SLEEP_SEC * 3)
                try:
                    retry_response = run_claude_on_pdf(pdf_path, RETRY_JSON_ONLY_PROMPT)
                    stats["claude_calls_per_pdf"] += 1
                    time.sleep(POST_CLAUDE_SLEEP_SEC)
                    raw_retry = parse_claude_response(retry_response)
                    normalized_retry = normalize(doc_type, raw_retry)
                    normalized = normalized_retry
                    stats["retry_required"] += 1
                    missing_after = _missing_required_fields(doc_type, normalized_retry)
                    if not missing_after:
                        logger.info("retry_once: required fields fulfilled")
                    else:
                        logger.info("retry_once: still missing → %s",
                                    ", ".join(missing_after))
                except Exception as retry_err:
                    logger.warning(
                        "retry_once failed: %s → fallback to first result",
                        retry_err,
                    )

        # 6. シート追記 (既存 sheets_writer が monitoring 限定で重複検知)
        # 重複検知はシートサイドの応答ログでカウントする。ここでは append 結果を信頼。
        before_calls = stats.get("_dup_probe", 0)
        append_row(pdf_path, normalized)
        # duplicate skip カウントは sheets_writer のログを別途集計する設計。
        # 簡易判定: review=False で append 後も同一 (拠点+ファイル名) を 2 度処理した時に発火。
        # ここでは sheets_writer 側の log line に頼る (read_log で counter を回す)。

        # 7. 状態保存
        store.mark_processed(key)
        stats["processed"] += 1
        if normalized.get("review_required"):
            stats["review_required"] += 1
        logger.info("Done: %s (type=%s, review=%s)",
                    pdf_path.name, doc_type, normalized.get("review_required"))

    except Exception as e:
        logger.error("Failed to process %s: %s", pdf_path.name, e)
        logger.error(traceback.format_exc())
        try:
            append_row(pdf_path, {
                "document_type": "unknown",
                "review_required": True,
                "review_comment": f"処理失敗: {e}",
            })
        except Exception as e2:
            logger.error("Also failed to append error row: %s", e2)
        stats["failed"] += 1


def phase2_limited_run(
    site_pdfs: dict[str, list[Path]],
    max_sites: int,
    max_files: int,
) -> dict:
    """先頭から max_sites 拠点 × 各 max_files ファイル に限定して実行。"""
    stats = {
        "processed": 0,
        "skipped": 0,
        "unknown": 0,
        "failed": 0,
        "review_required": 0,
        "retry_parse": 0,
        "retry_required": 0,
        "by_type": Counter(),
        "by_site": Counter(),
        "sites_run": [],
    }
    store = StateStore()

    target_sites = [n for n, v in site_pdfs.items() if v][:max_sites]
    print()
    print("=" * 70)
    print(f"  Phase 2: 限定実行 (max_sites={max_sites}, max_files={max_files})")
    print("=" * 70)
    print(f"  対象拠点 ({len(target_sites)}):")
    for name in target_sites:
        n_take = min(len(site_pdfs[name]), max_files)
        print(f"    - {name:40s} {n_take} / {len(site_pdfs[name])} PDF を処理")
    print("=" * 70)

    for site_name in target_sites:
        pdfs = site_pdfs[site_name][:max_files]
        stats["sites_run"].append(site_name)
        for idx, pdf in enumerate(pdfs, 1):
            logger.info("-" * 60)
            logger.info("[%s] [%d/%d] %s", site_name, idx, len(pdfs), pdf.name)
            _process_one_pdf(pdf, store, stats)
            stats["by_site"][site_name] += 1
    return stats


def phase3_report(stats: dict, log_path: Path | None = None) -> None:
    """集計サマリ表示。duplicate skip は sheets_writer のログから別途数える。"""
    dup_skips = 0
    if log_path and log_path.exists():
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    if "duplicate skipped: monitoring" in line:
                        dup_skips += 1
        except Exception:
            pass

    print()
    print("=" * 70)
    print("  Phase 3: 実行サマリ")
    print("=" * 70)
    print(f"  総処理対象 (新規 PDF)   : {stats['processed'] + stats['failed'] + stats['unknown'] + stats['skipped']}")
    print(f"  ├ 成功 (processed)      : {stats['processed']}")
    print(f"  ├ unknown 種別          : {stats['unknown']}")
    print(f"  ├ failed                : {stats['failed']}")
    print(f"  └ skip (state 済)       : {stats['skipped']}")
    print()
    print(f"  review_required = true  : {stats['review_required']}")
    print(f"  retry 発火 (parse 失敗) : {stats['retry_parse']}")
    print(f"  retry 発火 (REQUIRED)   : {stats['retry_required']}")
    print(f"  duplicate skip (monitoring): {dup_skips}")
    print()
    print("  --- 帳票別 (classify 結果) ---")
    for k in ["assessment", "plan_draft", "meeting_record", "plan_final",
              "monitoring", "unknown"]:
        print(f"    {k:18s} {stats['by_type'].get(k, 0)}")
    print()
    print("  --- 拠点別 (Phase 2 で処理した件数) ---")
    for site in stats["sites_run"]:
        print(f"    {site:40s} {stats['by_site'][site]}")
    print("=" * 70)


def main() -> int:
    p = argparse.ArgumentParser(description="GH PDF テスト実行 (Phase 1/2/3)")
    p.add_argument("--scan", action="store_true",
                   help="Phase 1 のみ (workflow は実行しない)")
    p.add_argument("--limit", action="store_true",
                   help="Phase 2+3: 限定実行 + サマリ")
    p.add_argument("--max-sites", type=int, default=3,
                   help="Phase 2 で処理する拠点数の上限 (default: 3)")
    p.add_argument("--max-files", type=int, default=10,
                   help="Phase 2 で各拠点から処理するファイル数の上限 (default: 10)")
    args = p.parse_args()

    if not args.scan and not args.limit:
        p.error("--scan か --limit のいずれかを指定してください")

    env_path = _project_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

    site_pdfs = phase1_scan()

    if args.limit:
        log_path = _project_root / "output" / "gh_test_run.log"
        # ログを別ファイルにも書き出す (集計用)
        fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)

        stats = phase2_limited_run(site_pdfs, args.max_sites, args.max_files)
        phase3_report(stats, log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
