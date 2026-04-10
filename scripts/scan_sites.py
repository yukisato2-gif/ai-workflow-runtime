"""複数拠点巡回スクリプト（担当者会議録用）。

指定した親フォルダ配下の拠点フォルダを順に巡回し、
各拠点の「032_個別支援計画関連PDF格納フォルダ」内の
担当者会議録PDFを処理して Google Sheets へ書き込む。

想定フォルダ構造:
    <親フォルダ>/
    ├── 001_100_001_GH横浜青葉/
    │   └── 032_個別支援計画関連PDF格納フォルダ/
    │       ├── 担当者会議録_A.pdf
    │       └── 担当者会議録_B.pdf
    ├── 001_100_002_GH熊谷妻沼/
    │   └── 032_個別支援計画関連PDF格納フォルダ/
    │       └── 担当者会議録_C.pdf
    └── ...

使い方:
    python scripts/scan_sites.py <親フォルダパス>

例:
    python scripts/scan_sites.py "G:\\共有ドライブ"
"""

import os
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv

from src.common import get_logger
from src.clients.claude import ClaudeClient

# batch_meeting.py の処理関数を再利用
from scripts.batch_meeting import process_single_pdf

logger = get_logger(__name__)

# 対象サブフォルダ名
TARGET_SUBFOLDER = "032_個別支援計画関連PDF格納フォルダ"


def find_site_folders(parent_path: Path) -> list[Path]:
    """親フォルダ配下から拠点フォルダ（GH を含むフォルダ）を取得する。

    Args:
        parent_path: 親フォルダのパス。

    Returns:
        拠点フォルダのパスリスト（ソート済み）。
    """
    sites = []
    for item in sorted(parent_path.iterdir()):
        if item.is_dir() and "GH" in item.name:
            sites.append(item)
    return sites


def main() -> None:
    """メイン関数。複数拠点を巡回して担当者会議録PDFをバッチ処理する。"""
    if len(sys.argv) < 2:
        print("Usage: python scripts/scan_sites.py <parent_folder_path>")
        sys.exit(1)

    parent_path = Path(sys.argv[1])
    if not parent_path.exists():
        logger.error("Parent folder not found: %s", parent_path)
        sys.exit(1)

    # 環境変数読み込み
    env_path = _project_root / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")

    if not api_key:
        logger.error("ANTHROPIC_API_KEY is not set")
        sys.exit(1)

    # 拠点フォルダ一覧取得
    sites = find_site_folders(parent_path)
    if not sites:
        logger.info("No site folders (containing 'GH') found in %s", parent_path)
        return

    logger.info("Scan start: %d site folders in %s", len(sites), parent_path)

    claude_client = ClaudeClient(api_key=api_key, model=model)

    # 全体カウンタ
    total_pdfs = 0
    total_success = 0
    total_skip = 0
    total_fail = 0
    sites_with_pdfs = 0
    sites_skipped = 0

    for site in sites:
        site_name = site.name

        try:
            target_folder = site / TARGET_SUBFOLDER

            if not target_folder.exists():
                logger.info("  [%s] %s not found, skipping", site_name, TARGET_SUBFOLDER)
                sites_skipped += 1
                continue

            pdfs = sorted(target_folder.glob("*.pdf"))
            if not pdfs:
                logger.info("  [%s] No PDFs in %s", site_name, TARGET_SUBFOLDER)
                sites_skipped += 1
                continue

            sites_with_pdfs += 1
            logger.info("  [%s] Found %d PDFs", site_name, len(pdfs))

            site_total = len(pdfs)
            site_success = 0
            site_skip = 0
            site_fail = 0

            for pdf in pdfs:
                total_pdfs += 1
                result = process_single_pdf(str(pdf), claude_client)
                if result:
                    site_success += 1
                    total_success += 1
                else:
                    site_fail += 1
                    total_fail += 1

            logger.info(
                "  [%s] Done: total=%d, success=%d, fail=%d",
                site_name, site_total, site_success, site_fail,
            )

        except Exception as e:
            logger.error("  [%s] Site processing failed: %s", site_name, e)
            total_fail += 1

    # 全体サマリ
    logger.info(
        "Scan complete: sites=%d (with PDFs=%d, skipped=%d), total_pdfs=%d, success=%d, fail=%d",
        len(sites), sites_with_pdfs, sites_skipped, total_pdfs, total_success, total_fail,
    )


if __name__ == "__main__":
    main()
