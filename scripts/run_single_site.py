"""1拠点処理スクリプト。

指定したGH拠点フォルダ配下の「032_個別支援計画関連PDF格納フォルダ」を探し、
5帳票のバッチ処理を順番に実行する。

1つのバッチが失敗した場合はその時点で停止する（安全優先）。

使い方:
    python scripts/run_single_site.py "<GH拠点フォルダパス>"

例:
    python scripts/run_single_site.py "G:\\共有ドライブ\\001_100_003_GH千葉花見川"
"""

import subprocess
import sys
from pathlib import Path

# 実行するバッチスクリプト（この順番で実行）
BATCH_SCRIPTS = [
    ("担当者会議録",         "batch_meeting.py"),
    ("モニタリング",         "batch_monitoring.py"),
    ("個別支援計画書案",     "batch_draft_plan.py"),
    ("個別支援計画書本案",   "batch_final_plan.py"),
    ("アセスメント",         "batch_assessment.py"),
]

# 対象サブフォルダ名
TARGET_SUBFOLDER = "032_個別支援計画関連PDF格納フォルダ"

# scripts ディレクトリの絶対パス
SCRIPTS_DIR = Path(__file__).resolve().parent

# プロジェクトルート
PROJECT_ROOT = SCRIPTS_DIR.parent


def find_pdf_folder(site_path: Path) -> list[Path]:
    """GH拠点フォルダ配下から PDF格納フォルダを探す。

    直下を優先し、1段下までは探索する。

    Args:
        site_path: GH拠点フォルダのパス。

    Returns:
        見つかった PDF 格納フォルダのリスト。
    """
    candidates = []

    # 直下を確認
    direct = site_path / TARGET_SUBFOLDER
    if direct.exists() and direct.is_dir():
        candidates.append(direct)
        return candidates

    # 1段下を確認
    for child in site_path.iterdir():
        if child.is_dir():
            sub = child / TARGET_SUBFOLDER
            if sub.exists() and sub.is_dir():
                candidates.append(sub)

    return candidates


def run_batch(script_name: str, folder_path: Path) -> bool:
    """バッチスクリプトを subprocess で実行する。

    Args:
        script_name: スクリプトファイル名（batch_xxx.py）。
        folder_path: 対象フォルダパス。

    Returns:
        True: 正常終了、False: 失敗。
    """
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        print(f"  [ERROR] スクリプトが見つかりません: {script_path}")
        return False

    result = subprocess.run(
        [sys.executable, str(script_path), str(folder_path)],
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode == 0


def main() -> None:
    """メイン関数。"""
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_single_site.py <GH拠点フォルダパス>")
        print()
        print("例:")
        print('  python scripts/run_single_site.py "G:\\共有ドライブ\\001_100_003_GH千葉花見川"')
        sys.exit(1)

    site_path = Path(sys.argv[1])

    # 拠点フォルダ存在確認
    if not site_path.exists():
        print(f"[ERROR] 拠点フォルダが見つかりません: {site_path}")
        sys.exit(1)

    if not site_path.is_dir():
        print(f"[ERROR] 指定パスはフォルダではありません: {site_path}")
        sys.exit(1)

    print(f"[INFO] 対象拠点フォルダ: {site_path}")
    print()

    # PDF格納フォルダ探索
    candidates = find_pdf_folder(site_path)

    if len(candidates) == 0:
        print(f"[INFO] {TARGET_SUBFOLDER} が見つかりませんでした。")
        print(f"[INFO] 対象フォルダ内に {TARGET_SUBFOLDER} が存在するか確認してください。")
        sys.exit(0)

    if len(candidates) > 1:
        print(f"[WARNING] {TARGET_SUBFOLDER} が複数見つかりました。安全のため停止します。")
        for c in candidates:
            print(f"  - {c}")
        print("[INFO] 対象フォルダを1つに限定してから再実行してください。")
        sys.exit(1)

    pdf_folder = candidates[0]
    print(f"[INFO] PDF格納フォルダ: {pdf_folder}")
    print()

    # 5帳票バッチを順番に実行
    print("=" * 60)
    print("5帳票バッチ処理を開始します")
    print("=" * 60)
    print()

    completed = []
    failed_batch = None

    for label, script_name in BATCH_SCRIPTS:
        print(f"--- [{label}] {script_name} 開始 ---")

        success = run_batch(script_name, pdf_folder)

        if success:
            print(f"--- [{label}] 成功 ---")
            print()
            completed.append(label)
        else:
            print(f"--- [{label}] 失敗 ---")
            print()
            failed_batch = label
            break

    # 最終結果
    print("=" * 60)
    if failed_batch is None:
        print(f"[結果] 全バッチ成功 ({len(completed)}/{len(BATCH_SCRIPTS)})")
    else:
        print(f"[結果] {failed_batch} で停止しました")
        print(f"  完了: {', '.join(completed) if completed else 'なし'}")
        print(f"  失敗: {failed_batch}")
        remaining = [label for label, _ in BATCH_SCRIPTS if label not in completed and label != failed_batch]
        if remaining:
            print(f"  未実行: {', '.join(remaining)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
