"""対象フォルダ走査モジュール (最小版)。

現時点ではローカルに同期された Google Drive のパスを対象とする。
将来的に Drive API 直接化する場合は本モジュール内を差し替える。
"""

from pathlib import Path

from src.common import get_logger

logger = get_logger(__name__)


def list_pdfs(folder: Path) -> list[Path]:
    """指定フォルダ配下の PDF を再帰的に列挙する。

    Args:
        folder: 対象フォルダのパス。

    Returns:
        PDF ファイルのパスリスト (ソート済み)。
    """
    if not folder.exists():
        logger.error("Folder not found: %s", folder)
        return []
    if not folder.is_dir():
        logger.error("Not a directory: %s", folder)
        return []

    pdfs = sorted(p for p in folder.rglob("*.pdf") if p.is_file())
    logger.info("Scanned %s → %d PDF files", folder, len(pdfs))
    return pdfs
