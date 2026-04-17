"""個別支援計画 PDF 抽出 workflow の CLI 入口 (PoC)。

使い方:
    # 環境変数で対象フォルダを指定
    export SUPPORT_PLAN_INPUT_DIR="/path/to/folder"
    python scripts/run_support_plan.py

    # または引数で指定
    python scripts/run_support_plan.py /path/to/folder
"""

from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv  # noqa: E402

from src.common import get_logger  # noqa: E402
from src.workflows.support_plan_pdf_extraction import run_support_plan_workflow  # noqa: E402

logger = get_logger(__name__)


def main() -> None:
    # .env 読込 (環境変数を優先しないよう override=False)
    env_path = _project_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

    folder: Path | None = None
    if len(sys.argv) >= 2:
        folder = Path(sys.argv[1])

    try:
        stats = run_support_plan_workflow(folder=folder)
    except Exception as e:
        logger.error("Workflow aborted: %s", e)
        sys.exit(1)

    # 1件でも失敗があれば非0で終了
    if stats["failed"] > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
