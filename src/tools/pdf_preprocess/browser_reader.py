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
import shutil
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


# =====================================================================
# 新方式: Chrome で PDF を開いてテキスト抽出 + Claude に送信
# =====================================================================
# PDF 添付方式 (upload_pdf) を排除し、以下の流れで処理する:
#   1. CDP 接続済み Chrome に新タブを開く
#   2. file:///... で PDF を開かせ Chrome の PDF ビューアで表示
#   3. document.body.innerText / embed / iframe 経由でテキスト抽出
#   4. 抽出テキストを Claude の既存チャット UI に貼り付けて送信
#   5. 応答 JSON を取得
#
# Chrome は手動起動された CDP 9222 接続先を再利用する (新たな Chrome は起動しない)

CDP_ENDPOINT = os.getenv("BROWSER_CDP_ENDPOINT", "http://127.0.0.1:9222")
CLAUDE_URL = os.getenv("CLAUDE_URL", "https://claude.ai/new")
PDF_OPEN_TIMEOUT_MS = int(os.getenv("PDF_OPEN_TIMEOUT_MS", "30000"))
PDF_RENDER_WAIT_MS = int(os.getenv("PDF_RENDER_WAIT_MS", "3000"))
CLAUDE_RESPONSE_TIMEOUT_MS = int(os.getenv("CLAUDE_RESPONSE_TIMEOUT_MS", "180000"))
CLAUDE_POLL_INTERVAL_MS = 2000
STABLE_THRESHOLD = 3


async def _extract_text_from_pdf_page(page) -> str:
    """PDF 表示ページからテキストを抽出する (強化版)。

    優先順位:
      1. document.body.innerText
      2. embed / iframe の存在確認・コンテンツ抽出
      3. スクロールしながら全文取得 (body.scrollHeight が変わらなくなるまで)
      4. それでも空なら WorkflowError
    """
    # 1. body.innerText
    try:
        text = await page.evaluate("() => document.body.innerText")
        if text and len(text.strip()) >= 50:
            logger.info("[PDF] text length=%d (method=body.innerText)",
                        len(text.strip()))
            return text.strip()
        else:
            logger.info("[PDF] body.innerText が空/不十分 (len=%d) → embed 確認へ",
                        len(text.strip()) if text else 0)
    except Exception as e:
        logger.debug("[PDF] body.innerText 取得失敗: %s", e)

    # 2. embed / iframe 存在確認・コンテンツ抽出
    try:
        has_embed = await page.evaluate(
            "() => !!document.querySelector('embed')"
        )
        has_iframe = await page.evaluate(
            "() => !!document.querySelector('iframe')"
        )
        logger.info("[PDF] embed存在=%s, iframe存在=%s", has_embed, has_iframe)

        if has_embed or has_iframe:
            logger.info("[PDF] fallback used: embed/iframe 内テキスト抽出")
            text = await page.evaluate(
                """() => {
                    const embed = document.querySelector('embed');
                    if (embed && embed.contentDocument) {
                        return embed.contentDocument.body.innerText;
                    }
                    const iframe = document.querySelector('iframe');
                    if (iframe && iframe.contentDocument) {
                        return iframe.contentDocument.body.innerText;
                    }
                    return '';
                }"""
            )
            if text and len(text.strip()) >= 50:
                logger.info("[PDF] text length=%d (method=embed/iframe)",
                            len(text.strip()))
                return text.strip()
    except Exception as e:
        logger.debug("[PDF] embed/iframe 抽出失敗: %s", e)

    # 3. スクロールしながら全文収集 (scrollHeight が安定するまで)
    logger.info("[PDF] fallback used: scroll extraction")
    try:
        scroll_script = """
            async () => {
                let lastHeight = 0;
                let lastText = '';
                let stable = 0;
                for (let i = 0; i < 50; i++) {
                    window.scrollBy(0, window.innerHeight);
                    await new Promise(r => setTimeout(r, 500));
                    const h = document.body.scrollHeight;
                    const t = document.body.innerText;
                    if (h === lastHeight && t === lastText) {
                        stable++;
                        if (stable >= 2) break;
                    } else {
                        stable = 0;
                    }
                    lastHeight = h;
                    lastText = t;
                }
                return document.body.innerText;
            }
        """
        text = await page.evaluate(scroll_script)
        if text and len(text.strip()) >= 50:
            logger.info("[PDF] text length=%d (method=scroll)",
                        len(text.strip()))
            return text.strip()
        else:
            logger.warning(
                "[PDF] スクロール抽出後も空/不十分: len=%d",
                len(text.strip()) if text else 0,
            )
    except Exception as e:
        logger.debug("[PDF] スクロール後抽出失敗: %s", e)

    # 4. 全手段で失敗 → 明示的にエラー
    logger.error("[PDF] extract failed: empty text")
    raise WorkflowError(
        "PDFテキスト抽出失敗: 空または不十分 (body.innerText / embed / スクロール 全滅)"
    )


async def extract_text_from_pdf_via_chrome(pdf_path: str) -> str:
    """CDP 接続済み Chrome で PDF を開いてテキストを抽出する。

    Args:
        pdf_path: PDF ファイルのローカル絶対パス。

    Returns:
        抽出テキスト。

    Raises:
        WorkflowError: 接続・抽出に失敗した場合。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise WorkflowError(
            "playwright がインストールされていません。pip install playwright"
        ) from e

    pdf_abs = str(Path(pdf_path).resolve())
    if not Path(pdf_abs).exists():
        raise WorkflowError(f"PDF が見つかりません: {pdf_abs}")

    # --- CloudStorage / GoogleDrive 配下は file:// で直接開けないため ---
    # ローカル一時ディレクトリにコピーしてから開く。
    # Drive は仮想ファイルで copy2 だとタイムアウトするため、
    # 1MB ずつのストリームコピーで Drive 側ダウンロードの遅延に耐える。
    tmp_dir = Path("/tmp/pdf_work")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / Path(pdf_abs).name
    logger.info("[PDF] streaming copy start: %s -> %s", pdf_abs, tmp_path)
    total_bytes = 0
    chunk_size = 1024 * 1024  # 1MB
    with open(pdf_abs, "rb") as src, open(tmp_path, "wb") as dst:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            dst.write(chunk)
            total_bytes += len(chunk)
    logger.info("[PDF] streaming copy done: size=%d bytes", total_bytes)
    logger.info("[PDF] copy to tmp: %s -> %s", pdf_abs, tmp_path)

    file_url = f"file://{tmp_path}"
    logger.info("[PDF] open: path=%s", pdf_abs)
    logger.info("[PDF] open tmp file: %s", file_url)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_ENDPOINT)
        except Exception as e:
            # tmp ファイルの清掃 (CDP 接続失敗時にも残さない)
            try:
                tmp_path.unlink(missing_ok=True)
                logger.info("[PDF] cleanup tmp: %s", tmp_path)
            except Exception:
                pass
            raise WorkflowError(
                f"CDP 接続失敗 ({CDP_ENDPOINT}): {e}\n"
                f"Chrome を --remote-debugging-port=9222 で起動してください。"
            ) from e

        if not browser.contexts:
            try:
                tmp_path.unlink(missing_ok=True)
                logger.info("[PDF] cleanup tmp: %s", tmp_path)
            except Exception:
                pass
            raise WorkflowError("CDP 接続先 Chrome に context がありません")

        context = browser.contexts[0]
        pdf_page = await context.new_page()
        try:
            await pdf_page.goto(file_url, timeout=PDF_OPEN_TIMEOUT_MS)
            # PDF ビューアの描画待ち
            await pdf_page.wait_for_timeout(PDF_RENDER_WAIT_MS)

            text = await _extract_text_from_pdf_page(pdf_page)
            if not text:
                raise WorkflowError(
                    f"PDF からテキストを抽出できませんでした: {pdf_abs}"
                )

            logger.info("[PDF] text length=%d", len(text))
            logger.info("[PDF] extract success")
            return text
        finally:
            try:
                await pdf_page.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
            # tmp ファイル削除
            try:
                tmp_path.unlink(missing_ok=True)
                logger.info("[PDF] cleanup tmp: %s", tmp_path)
            except Exception as e:
                logger.debug("[PDF] tmp cleanup 失敗: %s", e)


async def _send_text_to_claude_and_get_response(prompt_text: str) -> str:
    """Claude UI (CDP 接続 Chrome) にテキストを送信して応答を取得する。

    既存の Claude タブを探し、なければ新規に /new を開く。
    応答はテキスト安定化 (連続 STABLE_THRESHOLD 回同一) で完了判定。
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_ENDPOINT)
        except Exception as e:
            raise WorkflowError(f"CDP 接続失敗 ({CDP_ENDPOINT}): {e}") from e

        if not browser.contexts:
            raise WorkflowError("CDP 接続先 Chrome に context がありません")

        context = browser.contexts[0]

        # ログインチェック: 既存ページの URL を確認
        claude_page = None
        for p in context.pages:
            if "claude.ai" in p.url and "login" not in p.url:
                claude_page = p
                break

        if claude_page is None:
            # 既存 Claude ページなし → 新タブで /new
            claude_page = await context.new_page()
            await claude_page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=30_000)

        if "login" in claude_page.url:
            raise RuntimeError(
                "Claude にログインされていません。ブラウザでログインしてください"
            )

        # /new でなければ /new へ
        if "claude.ai/new" not in claude_page.url:
            await claude_page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=30_000)

        # 入力欄待機
        await claude_page.wait_for_selector(
            "div.ProseMirror[contenteditable='true']", timeout=10_000
        )

        # 入力
        input_el = claude_page.locator("div.ProseMirror[contenteditable='true']").first
        await input_el.click(timeout=5_000)
        # 長文対応: clipboard 経由ではなく keyboard.type (確実)
        await claude_page.keyboard.type(prompt_text, delay=0)
        await claude_page.wait_for_timeout(500)

        # 送信
        try:
            send_btn = claude_page.locator('button[aria-label*="送信"]').first
            if await send_btn.count() > 0:
                await send_btn.click(timeout=3_000)
            else:
                await claude_page.keyboard.press("Enter")
        except Exception:
            await claude_page.keyboard.press("Enter")

        # 応答取得 (テキスト安定化判定)
        await claude_page.wait_for_timeout(3_000)

        response_selectors = [
            "div.font-claude-response",
            "[class*='font-claude-response']",
            "[class*='font-claude']",
        ]
        streaming_selectors = [
            'button[aria-label*="Stop"]',
            'button[aria-label*="停止"]',
            "[data-is-streaming='true']",
        ]

        prev_text = ""
        stable = 0
        max_polls = CLAUDE_RESPONSE_TIMEOUT_MS // CLAUDE_POLL_INTERVAL_MS

        for _ in range(int(max_polls)):
            cur_text = ""
            for sel in response_selectors:
                try:
                    loc = claude_page.locator(sel)
                    if await loc.count() > 0:
                        cur_text = (await loc.last.inner_text(timeout=3_000)).strip()
                        if cur_text:
                            break
                except Exception:
                    continue

            # ストリーミング中?
            is_streaming = False
            for sel in streaming_selectors:
                try:
                    el = claude_page.locator(sel)
                    if await el.count() > 0 and await el.first.is_visible(timeout=1_000):
                        is_streaming = True
                        break
                except Exception:
                    continue

            if is_streaming:
                stable = 0
            elif cur_text and cur_text == prev_text:
                stable += 1
                if stable >= STABLE_THRESHOLD:
                    return cur_text
            else:
                stable = 0

            prev_text = cur_text
            await claude_page.wait_for_timeout(CLAUDE_POLL_INTERVAL_MS)

        if prev_text:
            logger.warning("[Claude] 応答タイムアウト。取得済みテキストを返します")
            return prev_text

        raise WorkflowError("Claude 応答を取得できませんでした")


def _adapt_text_prompt(prompt_template: str, extracted_text: str) -> str:
    """API 用プロンプトテンプレートに抽出テキストを埋め込む。

    {ocr_text} プレースホルダがあればそこに挿入。
    無ければ末尾に「【入力テキスト】」として追加。
    """
    if "{ocr_text}" in prompt_template:
        return prompt_template.replace("{ocr_text}", extracted_text)
    # プレースホルダが無い場合は末尾に付加
    return f"{prompt_template.strip()}\n\n【入力テキスト】\n{extracted_text}"


def extract_json_via_text(pdf_path: str, prompt_template: str) -> dict:
    """新方式: PDF を Chrome で開いてテキスト抽出 → Claude UI に送信 → JSON 取得。

    Args:
        pdf_path: PDF ファイルのパス。
        prompt_template: 既存プロンプト (assessment.md 等の Markdown 本文)。

    Returns:
        パース済み JSON dict。

    Raises:
        WorkflowError: 抽出または応答取得に失敗した場合。
    """
    import asyncio

    logger.info("Claude ブラウザ方式 (テキスト抽出): %s", pdf_path)

    # 1. Chrome で PDF を開いてテキスト抽出
    extracted_text = asyncio.run(extract_text_from_pdf_via_chrome(pdf_path))

    # 2. プロンプトに抽出テキストを埋め込み
    full_prompt = _adapt_text_prompt(prompt_template, extracted_text)
    logger.debug("送信プロンプト長: %d 文字", len(full_prompt))

    # 3. Claude UI に送信して応答取得
    response_text = asyncio.run(_send_text_to_claude_and_get_response(full_prompt))
    logger.info("Claude 応答長: %d 文字", len(response_text))

    # 4. JSON パース (既存ロジックと同様)
    json_text = response_text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", json_text, re.DOTALL)
    if m:
        json_text = m.group(1).strip()

    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.error("JSON パース失敗: %s", e)
        logger.error("応答先頭200文字: %s", json_text[:200])
        raise WorkflowError(
            f"Claude 応答の JSON パースに失敗: {e}\n応答先頭: {json_text[:100]}"
        ) from e
