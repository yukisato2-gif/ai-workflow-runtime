"""ブラウザ方式 PDF 読取アダプタモジュール。

browser-pdf-test/ の run_test.py をサブプロセスとして呼び出し、
Claude Web UI 経由で PDF を読み取って構造化データを取得する。

既存の API 方式 (extractor.py + client.py) とは独立した経路。
PDF_READ_MODE=browser の場合にのみ、ワークフローから呼び出される。

既存ファイル (extractor.py, client.py 等) には一切依存しない。
browser-pdf-test/ 側のコードもコピーしていない。
サブプロセス呼出でブリッジするだけの薄いアダプタ。
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from src.common import get_logger, WorkflowError

logger = get_logger(__name__)

# =====================================================================
# パス設定
# =====================================================================

# ai-workflow-runtime/ のルート
_RUNTIME_ROOT = Path(__file__).resolve().parents[3]

# browser-pdf-test/ のディレクトリ
# デフォルト: リポジトリ内の browser-pdf-test/ (GitHub 同梱構成)
# 環境変数で外部パスに変更可能 (ローカル兄弟ディレクトリ構成等)
BROWSER_TEST_DIR = Path(os.getenv(
    "BROWSER_PDF_TEST_DIR",
    str(_RUNTIME_ROOT / "browser-pdf-test"),
))

_RUN_SCRIPT = BROWSER_TEST_DIR / "run_test.py"

# サブプロセスタイムアウト (秒)
BROWSER_TIMEOUT_SEC = int(os.getenv("BROWSER_TIMEOUT_SEC", "300"))

# サブプロセス終了後の cooldown (秒)
# CDP 接続先 Chrome のタブ遷移が完了する前に次の run_test.py が
# 起動するのを防ぐ。
BROWSER_POST_WAIT_SEC = int(os.getenv("BROWSER_POST_WAIT_SEC", "2"))


# =====================================================================
# プロンプト変換
# =====================================================================

def _adapt_prompt_for_browser(prompt_template: str) -> str:
    """API 用プロンプトテンプレートをブラウザ PDF 読取用に変換する。

    変換内容:
    - OCR テキスト埋込み用プレースホルダ ({ocr_text}) を除去
    - 前文を「添付 PDF を読み取る」指示に書き換え
    - 【入力テキスト】セクションを除去
    - 抽出ルール・出力形式はそのまま保持

    Args:
        prompt_template: API 用プロンプトテンプレート。

    Returns:
        ブラウザ用に変換されたプロンプト。
    """
    prompt = prompt_template

    # 前文の書き換え (モニタリング記録)
    prompt = prompt.replace(
        "以下のテキストはモニタリング記録PDFからOCRで抽出したものです。",
        "添付されたPDFはモニタリング記録です。PDFの内容を読み取り、",
    )
    # 前文の書き換え (担当者会議録)
    prompt = prompt.replace(
        "以下のテキストは担当者会議録PDFからOCRで抽出したものです。",
        "添付されたPDFは担当者会議録です。PDFの内容を読み取り、",
    )

    # {ocr_text} プレースホルダを除去
    prompt = prompt.replace("{ocr_text}", "")

    # 【入力テキスト】セクションを除去
    prompt = re.sub(r"\n*【入力テキスト】\s*$", "", prompt, flags=re.MULTILINE)

    return prompt.strip()


# =====================================================================
# Python 実行パス解決
# =====================================================================

def _resolve_python() -> str:
    """browser-pdf-test 実行用の Python パスを解決する。

    優先順位:
    1. BROWSER_PYTHON 環境変数
    2. browser-pdf-test/.venv 内の python
    3. 現在の Python
    """
    # 1. 環境変数で明示指定
    env_py = os.getenv("BROWSER_PYTHON")
    if env_py and Path(env_py).exists():
        logger.info("BROWSER_PYTHON 使用: %s", env_py)
        return env_py

    # 2. browser-pdf-test の venv (macOS/Linux/Windows)
    for rel in [".venv/bin/python3", ".venv/bin/python", ".venv/Scripts/python.exe"]:
        venv_py = BROWSER_TEST_DIR / rel
        if venv_py.exists():
            logger.info("browser-pdf-test venv 使用: %s", venv_py)
            return str(venv_py)

    # 3. 現在の Python (playwright がインストール済みであること)
    logger.info("現在の Python 使用: %s", sys.executable)
    return sys.executable


# =====================================================================
# 公開関数
# =====================================================================

def read_pdf_via_browser(pdf_path: str, prompt: str) -> str:
    """ブラウザ操作で PDF を Claude に渡し、応答テキストを取得する。

    browser-pdf-test/run_test.py をサブプロセスとして実行し、
    output/result.txt の内容を返す。

    Args:
        pdf_path: PDF ファイルのパス。
        prompt: Claude に送信するプロンプト。

    Returns:
        Claude の応答テキスト。

    Raises:
        WorkflowError: 実行に失敗した場合。
    """
    if not _RUN_SCRIPT.exists():
        raise WorkflowError(
            f"browser-pdf-test が見つかりません: {_RUN_SCRIPT}\n"
            f"BROWSER_PDF_TEST_DIR 環境変数でパスを指定できます。\n"
            f"現在の設定: {BROWSER_TEST_DIR}"
        )

    pdf_abs = str(Path(pdf_path).resolve())
    python_path = _resolve_python()

    logger.info("Claude ブラウザへ PDF 添付・読取開始 (Web UI 方式)")
    logger.info("  PDF: %s", pdf_abs)
    logger.info("  スクリプト: %s", _RUN_SCRIPT)
    logger.info("  Python: %s", python_path)
    logger.info("  タイムアウト: %d 秒", BROWSER_TIMEOUT_SEC)
    logger.info("  (失敗時は browser-pdf-test/output/error.log と fail_*.png を参照)")

    # --- 名前付き引数で呼び出す (CLI 契約に基づく安定呼出) ---
    # プロンプトはファイル経由で渡す (長文プロンプトの引数長上限を回避)
    prompt_file = None
    output_file = None
    try:
        # プロンプトを一時ファイルに書き出し
        prompt_fd, prompt_path = tempfile.mkstemp(suffix=".txt", prefix="prompt_")
        prompt_file = Path(prompt_path)
        with os.fdopen(prompt_fd, "w", encoding="utf-8") as f:
            f.write(prompt)

        # 結果保存先の一時ファイル
        output_fd, output_path = tempfile.mkstemp(suffix=".txt", prefix="result_")
        output_file = Path(output_path)
        os.close(output_fd)

        cmd = [
            python_path, str(_RUN_SCRIPT),
            "--pdf", pdf_abs,
            "--output", str(output_file),
            "--prompt-file", str(prompt_file),
        ]
        logger.debug("実行コマンド: %s", " ".join(cmd))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=BROWSER_TIMEOUT_SEC,
                cwd=str(BROWSER_TEST_DIR),
            )
        except subprocess.TimeoutExpired:
            raise WorkflowError(
                f"browser-pdf-test タイムアウト ({BROWSER_TIMEOUT_SEC}秒)。\n"
                f"BROWSER_TIMEOUT_SEC 環境変数で延長できます。"
            )
        except FileNotFoundError as e:
            raise WorkflowError(
                f"Python 実行ファイルが見つかりません: {python_path}\n"
                f"BROWSER_PYTHON 環境変数で指定するか、\n"
                f"browser-pdf-test/.venv を作成してください。\n"
                f"詳細: {e}"
            )

        # --- 成功/失敗判定 (CLI 契約に基づく) ---
        # 契約: exit 0 = 成功, exit 1 = 失敗, exit 130 = 中断
        if proc.returncode != 0:
            stderr_tail = proc.stderr[-500:] if proc.stderr else "(empty)"
            # browser-pdf-test 側の error.log も確認
            error_log = BROWSER_TEST_DIR / "output" / "error.log"
            error_detail = ""
            if error_log.exists():
                error_detail = error_log.read_text(encoding="utf-8")[-500:]
            logger.error("browser-pdf-test 失敗 (exit=%d)", proc.returncode)
            logger.error("stderr: %s", stderr_tail)
            if error_detail:
                logger.error("error.log: %s", error_detail)
            raise WorkflowError(
                f"browser-pdf-test 失敗 (exit={proc.returncode})。\n"
                f"stderr: {stderr_tail}"
            )

        # 契約: exit 0 AND 結果ファイルが存在 AND 中身が空でない → 成功
        if not output_file.exists():
            raise WorkflowError(
                f"結果ファイルが見つかりません: {output_file}\n"
                f"browser-pdf-test/test.log を確認してください。"
            )

        response_text = output_file.read_text(encoding="utf-8").strip()
        if not response_text:
            raise WorkflowError(
                "browser-pdf-test の結果が空です。\n"
                "browser-pdf-test/test.log を確認してください。"
            )

        logger.info("Claude ブラウザ添付・読取完了 (%d 文字)", len(response_text))
        # 成功時でも run_test.py の stdout 末尾をデバッグ出力 (追跡用)
        if proc.stdout:
            stdout_tail = proc.stdout[-300:]
            logger.debug("run_test.py stdout (tail): %s", stdout_tail)

        # サブプロセス終了後の cooldown (連続実行時の navigation abort 防止)
        if BROWSER_POST_WAIT_SEC > 0:
            logger.info("subprocess 後 cooldown %d 秒開始", BROWSER_POST_WAIT_SEC)
            time.sleep(BROWSER_POST_WAIT_SEC)
            logger.info("subprocess 後 cooldown 終了")
        return response_text

    finally:
        # 一時ファイル清掃
        if prompt_file and prompt_file.exists():
            try:
                prompt_file.unlink()
            except OSError:
                pass
        if output_file and output_file.exists():
            try:
                output_file.unlink()
            except OSError:
                pass


def extract_json_via_browser(pdf_path: str, prompt_template: str) -> dict:
    """ブラウザ操作で PDF から構造化 JSON を取得する。

    内部で以下を行う:
    1. API 用プロンプトをブラウザ用に変換
    2. browser-pdf-test 経由で Claude に PDF を渡す
    3. 応答テキストから JSON をパース

    Args:
        pdf_path: PDF ファイルのパス。
        prompt_template: API 用プロンプトテンプレート ({ocr_text} 含む)。

    Returns:
        パース済みの dict。

    Raises:
        WorkflowError: 読取または JSON パースに失敗した場合。
    """
    # プロンプト変換
    browser_prompt = _adapt_prompt_for_browser(prompt_template)
    logger.debug("ブラウザ用プロンプト (先頭200文字): %s", browser_prompt[:200])

    # ブラウザで PDF を読み取り
    response_text = read_pdf_via_browser(pdf_path, browser_prompt)

    # JSON パース (```json ... ``` コードブロックの除去を含む)
    json_text = response_text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", json_text, re.DOTALL)
    if match:
        json_text = match.group(1).strip()
        logger.debug("コードブロックから JSON を抽出")

    try:
        parsed = json.loads(json_text)
        logger.info("JSON パース成功 (キー数: %d)", len(parsed))
        return parsed
    except json.JSONDecodeError as e:
        logger.error("JSON パース失敗: %s", e)
        logger.error("応答テキスト先頭200文字: %s", json_text[:200])
        raise WorkflowError(
            f"ブラウザ応答の JSON パースに失敗: {e}\n"
            f"応答先頭: {json_text[:100]}"
        ) from e
