"""
Claude ブラウザ PDF 読取テスト (browser-pdf-test)
===================================================
Claude API を使わず、Playwright でブラウザを自動操作し、
Claude Web UI に PDF を渡して内容を読み取るテストスクリプト。

既存の ai-workflow-runtime (Claude API 方式) とは完全に独立。
browser-pdf-test/ 配下だけで完結し、既存コードには一切影響しない。

使い方 (単体テスト):
    python run_test.py                           # input/ 内の最初のPDFを使用
    python run_test.py sample.pdf                # input/sample.pdf を使用
    python run_test.py /path/to/file.pdf         # 絶対パス指定

使い方 (名前付き引数 — browser_reader.py からの呼出契約):
    python run_test.py --pdf /path/to/file.pdf --output /tmp/result.txt --prompt-file /tmp/prompt.txt

CLI 契約:
    成功時: exit code 0, --output 先にテキストが書き出される
    失敗時: exit code 1, output/error.log にエラー詳細が追記される
    中断時: exit code 130 (Ctrl+C)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path

# =====================================================================
# 定数（すべてここに集約。コード中にベタ書きしない）
# =====================================================================

# --- パス ---
SCRIPT_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = SCRIPT_DIR / ".browser_data"
OUTPUT_DIR = SCRIPT_DIR / "output"
INPUT_DIR = SCRIPT_DIR / "input"
RESULT_FILE = OUTPUT_DIR / "result.txt"
ERROR_LOG_FILE = OUTPUT_DIR / "error.log"
RUN_LOG_FILE = SCRIPT_DIR / "test.log"

# --- URL ---
CLAUDE_URL = "https://claude.ai/new"

# --- タイムアウト (ミリ秒) ---
LOGIN_TIMEOUT_MS = 300_000       # 手動ログイン待ち: 5分
UPLOAD_TIMEOUT_MS = 30_000       # ファイルアップロード待ち: 30秒
RESPONSE_TIMEOUT_MS = 180_000    # Claude 応答待ち: 3分
POLL_INTERVAL_MS = 2_000         # 応答ポーリング間隔: 2秒

# --- 応答安定判定 ---
STABLE_THRESHOLD = 3  # この回数連続でテキスト同一なら応答完了とみなす

# --- Claude への送信文面 (定数として分離) ---
DEFAULT_PROMPT = (
    "このPDFの内容を日本語で要約してください。"
    "主要な情報を箇条書きで整理してください。"
)

# =====================================================================
# セレクタ候補 (配列で保持。1つの固定セレクタに依存しない)
# Claude.ai の UI 変更時はここだけ更新すればよい
# =====================================================================

# ログイン済み判定: チャット入力欄の存在で判定
LOGIN_CHECK_SELECTORS = [
    "div.ProseMirror[contenteditable='true']",
    "div[contenteditable='true']",
    "textarea",
    "[data-placeholder]",
]

# 添付ボタン: ファイルアップロード用ボタン
ATTACH_BUTTON_SELECTORS = [
    'button[aria-label*="Attach"]',
    'button[aria-label*="attach"]',
    'button[aria-label*="file"]',
    'button[aria-label*="File"]',
    'button[aria-label*="Upload"]',
    'button[aria-label*="upload"]',
    'button[data-testid*="attach"]',
    'button[data-testid*="upload"]',
    'button[data-testid*="file"]',
]

# チャット入力欄
INPUT_FIELD_SELECTORS = [
    "div.ProseMirror[contenteditable='true']",
    "div[contenteditable='true']",
    "textarea",
    "[data-placeholder]",
]

# 送信ボタン
SEND_BUTTON_SELECTORS = [
    'button[aria-label*="Send"]',
    'button[aria-label*="send"]',
    'button[aria-label*="送信"]',
    'button[data-testid*="send"]',
    'button[data-testid*="submit"]',
    'button[type="submit"]',
]

# 応答本文: Claude の返答テキストが入る要素
RESPONSE_TEXT_SELECTORS = [
    "[data-is-streaming]",
    "[class*='font-claude']",
    "[class*='assistant-']",
    "[class*='response-']",
    "[class*='message-content']",
    "[data-testid*='bot-message']",
    "[data-testid*='message-content']",
    "[data-testid*='assistant']",
    "[data-testid*='response']",
]

# ストリーミング中インジケータ (これが消えたら応答完了)
STREAMING_INDICATORS = [
    'button[aria-label*="Stop"]',
    'button[aria-label*="stop"]',
    'button[aria-label*="停止"]',
    'button[data-testid*="stop"]',
    "[data-is-streaming='true']",
]


# =====================================================================
# ログ設定
# =====================================================================

def _setup_logger() -> logging.Logger:
    """ログ設定を構築する。ファイル + 標準出力の二重出力。"""
    logger = logging.getLogger("browser-pdf-test")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # 標準出力 (INFO 以上)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # ファイル (DEBUG 以上)
    fh = logging.FileHandler(str(RUN_LOG_FILE), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


log = _setup_logger()


# =====================================================================
# エラーログ保存
# =====================================================================

def write_error_log(message: str, exc: Exception | None = None) -> Path:
    """output/error.log にエラー内容を書き出す。output/ がなければ自動作成。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"=== Error at {timestamp} ===",
        f"Message: {message}",
    ]
    if exc:
        lines.append(f"Exception: {type(exc).__name__}: {exc}")
        lines.append("Traceback:")
        lines.append(traceback.format_exc())
    lines.append("")

    with open(str(ERROR_LOG_FILE), "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("エラーログ保存: %s", ERROR_LOG_FILE)
    return ERROR_LOG_FILE


# =====================================================================
# スクリーンショット保存
# =====================================================================

async def save_screenshot(page, label: str = "screenshot") -> Path | None:
    """output/ にスクリーンショットを保存する。output/ がなければ自動作成。"""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = OUTPUT_DIR / f"{label}_{ts}.png"
        await page.screenshot(path=str(path), full_page=False)
        log.info("スクリーンショット保存: %s", path)
        return path
    except Exception as e:
        log.warning("スクリーンショット保存失敗: %s", e)
        return None


# =====================================================================
# セレクタ全滅時のデバッグ情報収集
# =====================================================================

async def collect_debug_info(page, step_name: str, tried_selectors: list[str]) -> str:
    """全セレクタ失敗時にデバッグ情報を収集しログに残す。"""
    info_lines = [f"--- Debug info for failed step: {step_name} ---"]

    # ページタイトル
    try:
        title = await page.title()
        info_lines.append(f"Page title: {title}")
    except Exception:
        info_lines.append("Page title: (取得失敗)")

    # 現在の URL
    try:
        url = page.url
        info_lines.append(f"Current URL: {url}")
    except Exception:
        info_lines.append("Current URL: (取得失敗)")

    # 試行したセレクタ一覧
    info_lines.append(f"Tried selectors ({len(tried_selectors)}):")
    for i, sel in enumerate(tried_selectors, 1):
        info_lines.append(f"  {i}. {sel}")

    # スクリーンショット
    ss_path = await save_screenshot(page, f"fail_{step_name}")
    if ss_path:
        info_lines.append(f"Screenshot: {ss_path}")

    debug_text = "\n".join(info_lines)
    log.error(debug_text)
    return debug_text


# =====================================================================
# PDF パス解決
# =====================================================================

def resolve_pdf_path(arg: str | None) -> Path:
    """PDF パスを解決する。"""
    if arg:
        # 絶対パスならそのまま
        p = Path(arg)
        if p.is_absolute() and p.exists():
            return p
        # input/ 配下を探す
        p_in_input = INPUT_DIR / arg
        if p_in_input.exists():
            return p_in_input.resolve()
        # 相対パスとして解決
        if p.exists():
            return p.resolve()
        raise FileNotFoundError(
            f"PDF が見つかりません: {arg}\n"
            f"  試したパス:\n"
            f"    - {Path(arg).resolve()}\n"
            f"    - {p_in_input.resolve()}"
        )

    # 引数なし → input/ 内の最初の PDF
    INPUT_DIR.mkdir(exist_ok=True)
    pdfs = sorted(INPUT_DIR.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(
            f"PDF が見つかりません。以下のいずれかを実行してください:\n"
            f"  1. {INPUT_DIR} に PDF を配置してから再実行\n"
            f"  2. python run_test.py <PDFファイル名 or パス>"
        )
    log.info("input/ から PDF を自動選択: %s", pdfs[0].name)
    return pdfs[0].resolve()


# =====================================================================
# 結果保存
# =====================================================================

def save_result(text: str, output_path: Path | None = None) -> Path:
    """Claude の応答テキストを保存する。

    Args:
        text: 保存するテキスト。
        output_path: 保存先パス。None なら RESULT_FILE (output/result.txt)。
    """
    dest = output_path or RESULT_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    log.info("結果保存: %s (%d 文字)", dest, len(text))
    return dest


# =====================================================================
# ブラウザ操作: ログイン確認 / 待機
# =====================================================================

async def wait_for_login(page) -> None:
    """ログイン済みか確認し、未ログインなら手動ログインを待機する。"""
    log.info("[Login] ログイン状態を確認中...")

    # 短いタイムアウトで各セレクタを試す
    for selector in LOGIN_CHECK_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=8_000)
            log.info("[Login] ログイン済み (成功セレクタ: %s)", selector)
            return
        except Exception:
            log.debug("[Login] セレクタ不一致: %s", selector)
            continue

    # 未ログイン
    log.info("=" * 55)
    log.info("  *** 手動ログインが必要です ***")
    log.info("  ブラウザ画面で Claude にログインしてください")
    log.info("  制限時間: %d 秒", LOGIN_TIMEOUT_MS // 1000)
    log.info("=" * 55)

    for selector in LOGIN_CHECK_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=LOGIN_TIMEOUT_MS)
            log.info("[Login] ログイン完了 (成功セレクタ: %s)", selector)
            return
        except Exception:
            log.debug("[Login] 待機タイムアウト: %s", selector)
            continue

    debug = await collect_debug_info(page, "login", LOGIN_CHECK_SELECTORS)
    write_error_log(f"ログインタイムアウト\n{debug}")
    raise TimeoutError("ログインがタイムアウトしました。ブラウザで手動ログイン後、再実行してください。")


# =====================================================================
# ブラウザ操作: PDF アップロード
# =====================================================================

async def upload_pdf(page, pdf_path: Path) -> None:
    """PDF を Claude のチャットにアップロードする。"""
    log.info("[Upload] PDF アップロード開始: %s (%d bytes)",
             pdf_path.name, pdf_path.stat().st_size)

    uploaded = False
    tried: list[str] = []

    # --- 方法 1: input[type=file] を直接操作 (最も安定) ---
    method_name = "input[type='file'] 直接操作"
    tried.append(method_name)
    log.info("[Upload] 方法1 試行: %s", method_name)
    try:
        file_inputs = page.locator('input[type="file"]')
        count = await file_inputs.count()
        log.debug("[Upload] input[type=file] 要素数: %d", count)
        if count > 0:
            for i in range(count):
                input_el = file_inputs.nth(i)
                accept_attr = await input_el.get_attribute("accept") or ""
                if "pdf" in accept_attr.lower() or accept_attr == "" or "*" in accept_attr:
                    await input_el.set_input_files(str(pdf_path))
                    uploaded = True
                    log.info("[Upload] 成功 (方法1: input#%d, accept='%s')", i, accept_attr)
                    break
            if not uploaded and count > 0:
                await file_inputs.first.set_input_files(str(pdf_path))
                uploaded = True
                log.info("[Upload] 成功 (方法1: input first, フォールバック)")
    except Exception as e:
        log.debug("[Upload] 方法1 失敗: %s", e)

    # --- 方法 2: 添付ボタンクリック + file_chooser イベント ---
    if not uploaded:
        log.info("[Upload] 方法2 試行: 添付ボタン + file_chooser")
        for selector in ATTACH_BUTTON_SELECTORS:
            tried.append(f"file_chooser + {selector}")
            try:
                async with page.expect_file_chooser(timeout=5_000) as fc_info:
                    await page.click(selector, timeout=3_000)
                file_chooser = await fc_info.value
                await file_chooser.set_files(str(pdf_path))
                uploaded = True
                log.info("[Upload] 成功 (方法2: %s)", selector)
                break
            except Exception as e:
                log.debug("[Upload] 方法2 失敗 (%s): %s", selector, e)
                continue

    # --- 方法 3: ページ上のボタンを順に探索 (最終手段) ---
    if not uploaded:
        log.info("[Upload] 方法3 試行: ボタン総当たり探索")
        tried.append("ボタン総当たり探索")
        try:
            buttons = page.locator("button")
            btn_count = await buttons.count()
            for i in range(min(btn_count, 15)):
                try:
                    async with page.expect_file_chooser(timeout=2_000) as fc_info:
                        await buttons.nth(i).click(timeout=1_000)
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(str(pdf_path))
                    uploaded = True
                    log.info("[Upload] 成功 (方法3: button#%d)", i)
                    break
                except Exception:
                    continue
        except Exception as e:
            log.debug("[Upload] 方法3 失敗: %s", e)

    # --- 全方法失敗 ---
    if not uploaded:
        debug = await collect_debug_info(page, "upload", tried)
        write_error_log(f"PDF アップロード失敗\n{debug}")
        raise RuntimeError(
            "PDF のアップロードに失敗しました。\n"
            "全てのセレクタ候補で失敗。詳細は output/error.log を確認してください。"
        )

    # アップロード処理の安定待ち
    log.info("[Upload] アップロード完了待機中...")
    await page.wait_for_timeout(4_000)
    log.info("[Upload] 完了")


# =====================================================================
# ブラウザ操作: プロンプト送信
# =====================================================================

async def send_prompt(page, prompt: str) -> None:
    """プロンプトを入力して送信する。"""
    log.info("[Send] プロンプト入力開始: '%s'",
             prompt[:50] + ("..." if len(prompt) > 50 else ""))

    # --- 入力欄にテキストを入力 ---
    input_ok = False
    tried_input: list[str] = []

    for selector in INPUT_FIELD_SELECTORS:
        tried_input.append(selector)
        try:
            input_el = page.locator(selector).first
            await input_el.click(timeout=5_000)
            # contenteditable では .fill() が使えないため keyboard.type() を使用
            await page.keyboard.type(prompt, delay=10)
            input_ok = True
            log.info("[Send] 入力成功 (セレクタ: %s)", selector)
            break
        except Exception as e:
            log.debug("[Send] 入力失敗 (%s): %s", selector, e)
            continue

    if not input_ok:
        debug = await collect_debug_info(page, "send_input", tried_input)
        write_error_log(f"プロンプト入力失敗\n{debug}")
        raise RuntimeError("プロンプトの入力に失敗しました。詳細は output/error.log を確認。")

    await page.wait_for_timeout(500)

    # --- 送信 ---
    sent = False
    tried_send: list[str] = []

    # 方法A: 送信ボタンをクリック
    for selector in SEND_BUTTON_SELECTORS:
        tried_send.append(f"button: {selector}")
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=2_000):
                await btn.click(timeout=3_000)
                sent = True
                log.info("[Send] 送信成功 (ボタン: %s)", selector)
                break
        except Exception as e:
            log.debug("[Send] 送信ボタン失敗 (%s): %s", selector, e)
            continue

    # 方法B: Enter キー
    if not sent:
        tried_send.append("Enter キー")
        try:
            await page.keyboard.press("Enter")
            sent = True
            log.info("[Send] 送信成功 (Enter キー)")
        except Exception as e:
            log.debug("[Send] Enter キー失敗: %s", e)

    if not sent:
        debug = await collect_debug_info(page, "send_submit", tried_send)
        write_error_log(f"プロンプト送信失敗\n{debug}")
        raise RuntimeError("プロンプトの送信に失敗しました。詳細は output/error.log を確認。")


# =====================================================================
# ブラウザ操作: 応答テキスト取得
# =====================================================================

async def _try_extract_response(page) -> tuple[str, str]:
    """現在のページから Claude の最新応答テキストを取得する。
    Returns: (テキスト, 成功セレクタ名)
    """
    # 方法1: 既知セレクタ
    for selector in RESPONSE_TEXT_SELECTORS:
        try:
            elements = page.locator(selector)
            count = await elements.count()
            if count > 0:
                text = await elements.last.inner_text(timeout=3_000)
                if text and text.strip():
                    return text.strip(), selector
        except Exception:
            continue

    # 方法2: main 配下の最後のテキストブロック群 (フォールバック)
    try:
        containers = page.locator("main, [role='main'], #__next")
        if await containers.count() > 0:
            paragraphs = containers.first.locator("div > p, div > ul, div > ol, div > pre")
            p_count = await paragraphs.count()
            if p_count > 0:
                texts = []
                start_idx = max(0, p_count - 30)
                for i in range(start_idx, p_count):
                    try:
                        t = await paragraphs.nth(i).inner_text(timeout=1_000)
                        if t and t.strip():
                            texts.append(t.strip())
                    except Exception:
                        continue
                if texts:
                    return "\n".join(texts), "main>div>p (フォールバック)"
    except Exception:
        pass

    return "", ""


async def _is_streaming(page) -> bool:
    """Claude がストリーミング中か判定する。"""
    for selector in STREAMING_INDICATORS:
        try:
            el = page.locator(selector)
            if await el.count() > 0 and await el.first.is_visible(timeout=1_000):
                return True
        except Exception:
            continue
    return False


async def wait_for_response(page) -> str:
    """Claude の応答が完了するのを待ち、テキストを返す。"""
    log.info("[Response] 応答待機開始 (最大 %d 秒)...", RESPONSE_TIMEOUT_MS // 1000)

    # 応答開始を少し待つ
    await page.wait_for_timeout(3_000)

    prev_text = ""
    prev_selector = ""
    stable_count = 0
    max_polls = RESPONSE_TIMEOUT_MS // POLL_INTERVAL_MS

    for poll_idx in range(int(max_polls)):
        current_text, used_selector = await _try_extract_response(page)

        # ストリーミング中チェック
        streaming = await _is_streaming(page)
        if streaming:
            stable_count = 0
            if current_text:
                log.info("[Response] ストリーミング中... (%d 文字)", len(current_text))
            prev_text = current_text
            prev_selector = used_selector
            await page.wait_for_timeout(POLL_INTERVAL_MS)
            continue

        # テキスト安定化チェック
        if current_text and current_text == prev_text:
            stable_count += 1
            log.info("[Response] 安定チェック: %d/%d (%d 文字, セレクタ: %s)",
                     stable_count, STABLE_THRESHOLD, len(current_text), used_selector)
            if stable_count >= STABLE_THRESHOLD:
                log.info("[Response] 応答完了 (%d 文字, 成功セレクタ: %s)",
                         len(current_text), used_selector)
                return current_text
        else:
            stable_count = 0
            if current_text:
                log.info("[Response] 受信中... (%d 文字)", len(current_text))

        prev_text = current_text
        prev_selector = used_selector
        await page.wait_for_timeout(POLL_INTERVAL_MS)

    # タイムアウト
    if prev_text:
        log.warning("[Response] タイムアウト。取得済みテキストを返します (%d 文字)", len(prev_text))
        return prev_text

    # 完全失敗
    all_tried = RESPONSE_TEXT_SELECTORS + ["main>div>p (フォールバック)"]
    debug = await collect_debug_info(page, "response", all_tried)
    write_error_log(f"応答取得失敗 (タイムアウト)\n{debug}")
    raise TimeoutError(
        "Claude の応答を取得できませんでした。\n"
        "詳細は output/error.log とスクリーンショットを確認してください。"
    )


# =====================================================================
# メイン処理
# =====================================================================

async def run(pdf_path: Path, prompt: str, output_path: Path | None = None) -> None:
    """テスト実行のメイン関数。"""
    log.info("=" * 60)
    log.info("  Claude ブラウザ PDF 読取テスト")
    log.info("=" * 60)
    log.info("PDF        : %s", pdf_path)
    log.info("プロンプト : %s", prompt)
    log.info("ブラウザDB : %s", USER_DATA_DIR)
    log.info("出力先     : %s", OUTPUT_DIR)
    log.info("")

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # --- Playwright import ---
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        msg = (
            "Playwright がインストールされていません。\n"
            "以下を実行してください:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        log.error(msg)
        write_error_log(msg)
        sys.exit(1)

    async with async_playwright() as pw:
        log.info("Chromium を起動中...")
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = context.pages[0] if context.pages else await context.new_page()

        try:
            # Step 1: Claude.ai に遷移
            log.info("[Step 1/6] Claude.ai に遷移中...")
            await page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=30_000)

            # Step 2: ログイン確認
            log.info("[Step 2/6] ログイン確認...")
            await wait_for_login(page)

            # Step 3: 新規チャット画面に遷移
            log.info("[Step 3/6] 新規チャット画面に遷移...")
            await page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2_000)

            # Step 4: PDF アップロード
            log.info("[Step 4/6] PDF アップロード...")
            await upload_pdf(page, pdf_path)

            # Step 5: プロンプト送信
            log.info("[Step 5/6] プロンプト送信...")
            await send_prompt(page, prompt)

            # Step 6: 応答取得
            log.info("[Step 6/6] 応答取得...")
            response_text = await wait_for_response(page)

            # 結果保存
            result_path = save_result(response_text, output_path)

            log.info("")
            log.info("=" * 60)
            log.info("  *** テスト成功! ***")
            log.info("=" * 60)
            log.info("結果ファイル : %s", result_path)
            log.info("テキスト長   : %d 文字", len(response_text))
            log.info("-" * 40)
            preview = response_text[:500]
            log.info(preview)
            if len(response_text) > 500:
                log.info("... (全文は %s を参照)", result_path)
            log.info("=" * 60)

            # 成功時もスクリーンショットを残す
            await save_screenshot(page, "success")

        except Exception as e:
            log.error("テスト失敗: %s", e, exc_info=True)
            write_error_log(str(e), exc=e)
            await save_screenshot(page, "error")
            raise
        finally:
            log.info("ブラウザを閉じています...")
            await context.close()
            log.info("終了")


# =====================================================================
# エントリポイント
# =====================================================================

def _parse_args() -> argparse.Namespace:
    """CLI 引数をパースする。

    【CLI 契約 — browser_reader.py との連携仕様】
    ■ 引数:
      --pdf <path>          処理対象の PDF ファイルパス
      --output <path>       結果テキストの保存先 (デフォルト: output/result.txt)
      --prompt-file <path>  プロンプトをファイルから読み込む (長文対応)
      位置引数              後方互換: 第1引数=PDF, 第2引数=プロンプト文字列
    ■ 終了コード:
      0  成功 (--output で指定した先に結果テキストが書き出される)
      1  失敗 (output/error.log にエラー詳細が追記される)
      130 ユーザー中断 (Ctrl+C)
    ■ 成功判定: exit code == 0 AND --output ファイルが存在 AND 中身が空でない
    """
    parser = argparse.ArgumentParser(
        description="Claude ブラウザ PDF 読取テスト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf_positional", nargs="?", default=None,
                        help="(後方互換) PDF ファイルパス")
    parser.add_argument("prompt_positional", nargs="?", default=None,
                        help="(後方互換) プロンプト文字列")
    parser.add_argument("--pdf", dest="pdf_named", default=None,
                        help="PDF ファイルパス")
    parser.add_argument("--output", default=None,
                        help="結果テキスト保存先パス")
    parser.add_argument("--prompt-file", default=None,
                        help="プロンプトをファイルから読み込む")
    return parser.parse_args()


def main() -> None:
    """コマンドライン引数を処理して実行する。"""
    args = _parse_args()

    # PDF パス: --pdf 優先、なければ位置引数
    pdf_arg = args.pdf_named or args.pdf_positional

    # プロンプト: --prompt-file 優先 → 位置引数 → デフォルト
    if args.prompt_file:
        pf = Path(args.prompt_file)
        if not pf.exists():
            log.error("プロンプトファイルが見つかりません: %s", pf)
            sys.exit(1)
        prompt = pf.read_text(encoding="utf-8").strip()
        log.info("プロンプトをファイルから読込: %s (%d 文字)", pf, len(prompt))
    else:
        prompt = args.prompt_positional or DEFAULT_PROMPT

    # 出力先
    output_path = Path(args.output) if args.output else None

    try:
        pdf_path = resolve_pdf_path(pdf_arg)
    except FileNotFoundError as e:
        log.error(str(e))
        write_error_log(str(e), exc=e)
        sys.exit(1)

    try:
        asyncio.run(run(pdf_path, prompt, output_path))
    except KeyboardInterrupt:
        log.info("\n中断されました (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        log.error("異常終了: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
