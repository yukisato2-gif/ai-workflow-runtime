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
# Claude.ai の UI 変更時はここを更新する
ATTACH_BUTTON_SELECTORS = [
    # --- 現 UI 最優先 (2026-04 確認) ---
    'button[aria-label*="ファイルやコネクタなどを追加"]',
    # --- ID ベース (最も安定) ---
    '#chat-input-file-upload-onpage',
    # --- aria-label ベース (日本語 UI) ---
    'button[aria-label*="ファイルをアップロード"]',
    'button[aria-label*="ファイルやコネクタ"]',
    'button[aria-label*="追加"]',
    # --- aria-label ベース (英語 UI) ---
    'button[aria-label*="Attach"]',
    'button[aria-label*="Upload"]',
    'button[aria-label*="Add file"]',
    'button[aria-label*="Add content"]',
    # --- input[type=file] ベース ---
    'input[type="file"]',
    # --- data-testid ベース ---
    'button[data-testid*="attach"]',
    'button[data-testid*="upload"]',
    'button[data-testid*="file"]',
    # --- fieldset 内ボタン (構造ベース) ---
    'fieldset button',
]

# チャット入力欄
# 現 UI (2026-04) では role="textbox" を持つ contenteditable が入力欄。
# 既存候補も残し、先頭に現 UI 向けセレクタを追加。
INPUT_FIELD_SELECTORS = [
    # --- 現 UI 最優先 ---
    '[contenteditable="true"][role="textbox"]',
    '[role="textbox"]',
    # --- 既存候補 (フォールバック) ---
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
# ユーザー発話を拾わないよう、アシスタント応答専用のマーカーを優先
RESPONSE_TEXT_SELECTORS = [
    # Claude 応答本文のコンテナ (最優先・厳密)
    "div.font-claude-response",
    "[data-is-streaming='false'] div.font-claude-response",
    "[data-is-streaming='true'] div.font-claude-response",
    # data-is-streaming 属性を持つ要素 (応答中/完了時に付与)
    "div[data-is-streaming]",
    # font-claude で始まるクラス
    "[class*='font-claude-response']",
    "[class*='font-claude-message']",
    # 旧来の候補 (フォールバック)
    "[class*='font-claude']",
    "[data-testid*='bot-message']",
    "[data-testid*='assistant']",
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
    """Claude の手動ログイン完了を URL ベースで待機する。"""
    log.info("[Login] 手動ログイン確認を開始します")

    current_url = page.url
    log.info("[Login] 現在URL: %s", current_url)

    if "login" not in current_url:
        log.info("[Login] 既にログイン済みです")
        return

    log.info("=" * 55)
    log.info("  *** 手動ログインが必要です ***")
    log.info("  開いたブラウザで Claude にログインしてください")
    log.info("  ログイン完了後、自動で次へ進みます")
    log.info("  制限時間: %d 秒", LOGIN_TIMEOUT_MS // 1000)
    log.info("=" * 55)

    waited = 0
    interval_ms = 1000

    while waited < LOGIN_TIMEOUT_MS:
        await page.wait_for_timeout(interval_ms)
        waited += interval_ms

        current_url = page.url

        if waited % 10000 == 0:
            log.info("[Login] 待機中... %d 秒経過 / URL=%s", waited // 1000, current_url)

        if "login" not in current_url:
            log.info("[Login] ログイン完了を検知: %s", current_url)

            await page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3000)

            log.info("[Login] Claude 画面へ復帰しました: %s", page.url)
            return

    debug = await collect_debug_info(page, "login", ["URL-based login wait"])
    write_error_log(f"ログインタイムアウト\n{debug}")
    raise TimeoutError("ログインがタイムアウトしました。ブラウザで手動ログイン後、再実行してください。")


# =====================================================================
# ブラウザ操作: PDF アップロード
# =====================================================================

async def upload_pdf(page, pdf_path: Path) -> None:
    """PDF を Claude のチャットにアップロードする。"""
    log.info("[Upload] PDF アップロード開始: %s (%d bytes)",
             pdf_path.name, pdf_path.stat().st_size)

    # --- 事前診断: アップロード UI を持つ画面にいるか確認 ---
    # ログイン状態チェック: 未ログインなら即エラー
    if "login" in page.url:
        log.error("[Login] 未ログイン検出: URL=%s", page.url)
        raise RuntimeError(
            "Claude にログインされていません。ブラウザでログインしてください"
        )

    try:
        cur_url = page.url
        cur_title = await page.title()
        log.info("[Upload] 事前診断: URL=%s", cur_url)
        log.info("[Upload] 事前診断: title=%s", cur_title)

        # 画面種別の簡易判定
        if "login" in cur_url:
            log.warning("[Upload] ログイン画面にいます。添付できません。")
        elif "/chat/" in cur_url or "/chats" in cur_url:
            log.info("[Upload] 既存チャット画面にいます (新規ではない)")
        elif cur_url.rstrip("/").endswith("claude.ai"):
            log.info("[Upload] Claude トップ画面 (履歴) にいる可能性")
        elif "/new" in cur_url:
            log.info("[Upload] /new 画面にいます (想定通り)")
        else:
            log.warning("[Upload] 想定外の画面にいる可能性: %s", cur_url)

        # 主要要素の検出数 (アップロード UI の有無)
        id_count = await page.locator("#chat-input-file-upload-onpage").count()
        file_input_count = await page.locator('input[type="file"]').count()
        add_content_count = await page.locator(
            'button[aria-label*="ファイルやコネクタなどを追加"]'
        ).count()
        plus_count = await page.locator(
            'button[aria-label*="追加"], button[aria-label*="ファイル"]'
        ).count()
        log.info(
            "[Upload] 主要要素: #chat-input-file-upload-onpage=%d, "
            "input[type=file]=%d, ファイルやコネクタなどを追加=%d, +/ファイルボタン=%d",
            id_count, file_input_count, add_content_count, plus_count,
        )
    except Exception as e:
        log.warning("[Upload] 事前診断に失敗: %s", e)

    uploaded = False
    tried: list[str] = []

    # --- 方法 1: add-content button + file_chooser (現 UI 最優先) ---
    # 現 UI (2026-04) は「ファイルやコネクタなどを追加」ボタンをクリックして
    # file chooser を開く方式が確実。hidden input への直接 set_input_files
    # は UI 変更で動作しない場合があるため、ボタン経由を先に試す。
    method_name = "add-content button + file chooser"
    tried.append(method_name)
    log.info("[Upload] 方法1: add-content button + file chooser")
    try:
        button = page.locator(
            'button[aria-label*="ファイルやコネクタなどを追加"]'
        ).first
        if await button.count() > 0:
            async with page.expect_file_chooser(timeout=5_000) as fc_info:
                await button.click(timeout=3_000)
            fc = await fc_info.value
            await fc.set_files(str(pdf_path))
            uploaded = True
            log.info("[Upload] 成功 (方法1: add-content button + file chooser)")
        else:
            log.info("[Upload] 方法1 スキップ: add-content button が存在しない")
    except Exception as e:
        log.info("[Upload] 方法1 失敗: %s", e)

    # --- 方法 2: ID 指定で input[type=file] に直接 set_files (フォールバック) ---
    # #chat-input-file-upload-onpage が hidden で存在する場合、
    # set_input_files は visible でなくても動作することがある。
    # 方法1 が失敗した場合のフォールバックとして維持。
    if not uploaded:
        file_input_id = "chat-input-file-upload-onpage"
        method_name = f"#{file_input_id} (set_input_files)"
        tried.append(method_name)
        log.info("[Upload] 方法2 試行: %s", method_name)
        try:
            input_el = page.locator(f"#{file_input_id}")
            if await input_el.count() > 0:
                # hidden でも set_input_files は動作する
                await input_el.set_input_files(str(pdf_path))
                uploaded = True
                log.info("[Upload] 成功 (方法2: #%s)", file_input_id)
        except Exception as e:
            log.info("[Upload] 方法2 失敗: %s", e)

    # --- 方法 2: 汎用 input[type=file] を全探索して set_files ---
    if not uploaded:
        method_name = "input[type='file'] 全探索"
        tried.append(method_name)
        log.info("[Upload] 方法2 試行: %s", method_name)
        try:
            file_inputs = page.locator('input[type="file"]')
            count = await file_inputs.count()
            log.info("[Upload] input[type=file] 要素数: %d", count)
            for i in range(count):
                try:
                    await file_inputs.nth(i).set_input_files(str(pdf_path))
                    uploaded = True
                    log.info("[Upload] 成功 (方法2: input[type=file] #%d)", i)
                    break
                except Exception as e2:
                    log.debug("[Upload] input#%d 失敗: %s", i, e2)
        except Exception as e:
            log.info("[Upload] 方法2 失敗: %s", e)

    # --- 方法 2.5: +ボタンをクリックしてメニュー展開 → input 再探索 ---
    if not uploaded:
        method_name = "+ ボタンクリック → input 再探索"
        tried.append(method_name)
        log.info("[Upload] 方法2.5 試行: %s", method_name)
        try:
            plus_btn = page.locator(
                'button[aria-label*="ファイルやコネクタ"], '
                'button[aria-label*="追加"], '
                'button[aria-label*="Attach"], '
                'button[aria-label*="Add"]'
            ).first
            await plus_btn.click(timeout=3_000)
            log.info("[Upload] + ボタンクリック成功。メニュー展開待機中...")
            await page.wait_for_timeout(1_500)
            # メニュー展開後に input[type=file] が現れるケース
            file_inputs = page.locator('input[type="file"]')
            count = await file_inputs.count()
            log.info("[Upload] +クリック後 input[type=file] 要素数: %d", count)
            for i in range(count):
                try:
                    await file_inputs.nth(i).set_input_files(str(pdf_path))
                    uploaded = True
                    log.info("[Upload] 成功 (方法2.5: +クリック後 input#%d)", i)
                    break
                except Exception as e2:
                    log.debug("[Upload] 方法2.5 input#%d 失敗: %s", i, e2)
            # メニュー項目から file_chooser が開くケース
            if not uploaded:
                menu_item_selectors = [
                    '[role="menuitem"]:has-text("アップロード")',
                    '[role="menuitem"]:has-text("ファイル")',
                    '[role="menuitem"]:has-text("Upload")',
                    '[role="menuitem"]:has-text("File")',
                ]
                for sel in menu_item_selectors:
                    try:
                        async with page.expect_file_chooser(timeout=3_000) as fc_info:
                            await page.locator(sel).first.click(timeout=2_000)
                        fc = await fc_info.value
                        await fc.set_files(str(pdf_path))
                        uploaded = True
                        log.info("[Upload] 成功 (方法2.5: メニュー項目 %s)", sel)
                        break
                    except Exception as e3:
                        log.debug("[Upload] 方法2.5 メニュー項目 %s 失敗: %s", sel, e3)
        except Exception as e:
            log.info("[Upload] 方法2.5 失敗: %s", e)

    # --- 方法 3: 添付ボタンクリック + file_chooser イベント ---
    if not uploaded:
        log.info("[Upload] 方法3 試行: 添付ボタン + file_chooser")
        for selector in ATTACH_BUTTON_SELECTORS:
            tried.append(f"file_chooser + {selector}")
            try:
                async with page.expect_file_chooser(timeout=5_000) as fc_info:
                    await page.click(selector, timeout=3_000)
                file_chooser = await fc_info.value
                await file_chooser.set_files(str(pdf_path))
                uploaded = True
                log.info("[Upload] 成功 (方法3: %s)", selector)
                break
            except Exception as e:
                log.debug("[Upload] 方法3 失敗 (%s): %s", selector, e)
                continue

    # --- 方法 4: ページ上のボタンを順に探索 (最終手段) ---
    if not uploaded:
        log.info("[Upload] 方法4 試行: ボタン総当たり探索")
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
                    log.info("[Upload] 成功 (方法4: button#%d)", i)
                    break
                except Exception:
                    continue
        except Exception as e:
            log.debug("[Upload] 方法4 失敗: %s", e)

    # --- 全方法失敗 ---
    if not uploaded:
        # 失敗時にDOMの主要要素の有無をログに残す
        diag_lines = []
        for sel, label in [
            ('#chat-input-file-upload-onpage', 'file input (ID)'),
            ('input[type="file"]', 'input[type=file]'),
            ('button[aria-label*="ファイル"]', 'ファイルボタン'),
            ('button[aria-label*="Attach"]', 'Attach button'),
            ('fieldset button', 'fieldset内button'),
        ]:
            try:
                cnt = await page.locator(sel).count()
                diag_lines.append(f"  {label} ({sel}): {cnt}個")
            except Exception:
                diag_lines.append(f"  {label} ({sel}): 検出不可")
        diag_text = "主要要素の検出結果:\n" + "\n".join(diag_lines)
        log.error("[Upload] %s", diag_text)

        debug = await collect_debug_info(page, "upload", tried)
        write_error_log(f"PDF アップロード失敗\n{diag_text}\n{debug}")
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
    used_selector = ""
    tried_input: list[str] = []

    # 事前に各候補の検出数をログ出力
    for selector in INPUT_FIELD_SELECTORS:
        try:
            cnt = await page.locator(selector).count()
        except Exception:
            cnt = -1
        log.info("[Send] 入力欄候補数: %s = %d", selector, cnt)

    # 入力欄取得を最大 OUTER_RETRIES 回試す。
    # 1 回目で成功すれば従来同等の速度。失敗時のみ sleep + 再ポーリング+再セレクタループに入る。
    # 主に「リトライ経路で PDF 再添付直後に DOM が遷移中で全セレクタ count=0 になる」
    # ケースを救済する。
    OUTER_RETRIES = 3
    OUTER_SLEEP_MS = 6_000
    ready_selectors = [
        '[contenteditable="true"][role="textbox"]',
        "div.ProseMirror[contenteditable='true']",
        "div[contenteditable='true']",
    ]

    for outer in range(1, OUTER_RETRIES + 1):
        if outer > 1:
            log.info("[Send] 入力欄取得 outer-retry %d/%d (sleep %dms)",
                     outer, OUTER_RETRIES, OUTER_SLEEP_MS)
            await page.wait_for_timeout(OUTER_SLEEP_MS)

        # --- 入力欄が edit-ready になるまで短ポーリング ---
        # リトライ時など、PDF 再添付直後は入力欄が一時的に触れない状態になる。
        # editable な要素が出現するまで最大 8 秒、500ms 間隔でポーリング。
        for _ in range(16):  # 16 * 500ms = 8 秒
            ready = False
            for rs in ready_selectors:
                try:
                    loc = page.locator(rs).first
                    if await loc.count() > 0 and await loc.is_editable(timeout=500):
                        ready = True
                        break
                except Exception:
                    continue
            if ready:
                log.info("[Send] 入力欄が edit-ready 検知 (outer=%d)", outer)
                break
            await page.wait_for_timeout(500)
        else:
            log.warning("[Send] 入力欄 edit-ready タイムアウト (outer=%d, 続行)", outer)

        for selector in INPUT_FIELD_SELECTORS:
            if selector not in tried_input:
                tried_input.append(selector)
            try:
                input_el = page.locator(selector).first
                if await input_el.count() == 0:
                    continue

                # 同一セレクタで「フォーカス+入力+検証」を最大 3 回試す
                # (リトライ時の一時的な入力不可状態を吸収)
                for attempt in range(1, 4):
                    # フォーカス試行 (click → focus の順でフォールバック)
                    focused = False
                    try:
                        await input_el.click(timeout=5_000)
                        focused = True
                        log.info("[Send] フォーカス成功 (click, outer=%d, attempt=%d): %s",
                                 outer, attempt, selector)
                    except Exception as e_click:
                        log.debug("[Send] click フォーカス失敗 (%s): %s", selector, e_click)
                        try:
                            await input_el.focus(timeout=3_000)
                            focused = True
                            log.info("[Send] フォーカス成功 (focus, outer=%d, attempt=%d): %s",
                                     outer, attempt, selector)
                        except Exception as e_focus:
                            log.debug("[Send] focus も失敗 (%s): %s", selector, e_focus)

                    if not focused:
                        log.info("[Send] フォーカス失敗 (outer=%d, attempt=%d): %s",
                                 outer, attempt, selector)
                        await page.wait_for_timeout(800)
                        continue

                    # 入力試行: keyboard.type → keyboard.insert_text
                    typed = False
                    try:
                        await page.keyboard.type(prompt, delay=0)
                        typed = True
                    except Exception as e_type:
                        log.debug("[Send] keyboard.type 失敗: %s", e_type)
                        try:
                            await page.keyboard.insert_text(prompt)
                            typed = True
                            log.info("[Send] keyboard.insert_text で入力")
                        except Exception as e_ins:
                            log.debug("[Send] insert_text 失敗: %s", e_ins)

                    if not typed:
                        await page.wait_for_timeout(800)
                        continue

                    await page.wait_for_timeout(400)

                    # 入力結果検証
                    try:
                        inner = await input_el.inner_text(timeout=2_000)
                    except Exception:
                        inner = ""
                    log.info("[Send] 入力文字数: %d (期待: %d, outer=%d, attempt=%d)",
                             len(inner), len(prompt), outer, attempt)
                    if inner and len(inner) >= 1:
                        input_ok = True
                        used_selector = selector
                        log.info("[Send] 入力成功 (セレクタ: %s, outer=%d, attempt=%d)",
                                 selector, outer, attempt)
                        break

                    # 0 文字: keyboard 入力が要素に届いていない (focus が別の要素に
                    # 引っ張られている等)。JS フォールバックで textContent を直接
                    # 書き込み + input/change イベントを dispatch して同期させる。
                    try:
                        await input_el.evaluate(
                            "(el, text) => {"
                            "  el.focus();"
                            "  if (el.isContentEditable) { el.textContent = text; }"
                            "  else { el.value = text; }"
                            "  el.dispatchEvent(new InputEvent('input', {bubbles: true, data: text}));"
                            "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                            "}",
                            prompt,
                        )
                        await page.wait_for_timeout(400)
                        try:
                            inner = await input_el.inner_text(timeout=2_000)
                        except Exception:
                            inner = ""
                        log.info("[Send] JS フォールバック後 入力文字数: %d (outer=%d, attempt=%d)",
                                 len(inner), outer, attempt)
                        if inner and len(inner) >= 1:
                            input_ok = True
                            used_selector = selector
                            log.info("[Send] JS フォールバックで入力成功 (セレクタ: %s, outer=%d, attempt=%d)",
                                     selector, outer, attempt)
                            break
                    except Exception as e_js:
                        log.debug("[Send] JS フォールバック失敗 (%s): %s", selector, e_js)

                    # 0 文字だったら、同一セレクタで再フォーカス+再入力
                    log.info("[Send] 入力 0 文字、同セレクタで再試行: %s (outer=%d, attempt=%d)",
                             selector, outer, attempt)
                    await page.wait_for_timeout(800)

                if input_ok:
                    break
                log.info("[Send] セレクタ変更: %s", selector)
            except Exception as e:
                log.debug("[Send] 入力失敗 (%s): %s", selector, e)
                continue

        if input_ok:
            break

    if not input_ok:
        debug = await collect_debug_info(page, "send_input", tried_input)
        write_error_log(f"プロンプト入力失敗\n{debug}")
        raise RuntimeError("プロンプトの入力に失敗しました。詳細は output/error.log を確認。")

    log.info("[Send] 確定セレクタ: %s", used_selector)
    await page.wait_for_timeout(500)

    # --- 送信 ---
    # 優先順位:
    #   1) Enter キー送信を最優先で実行
    #   2) UI 変化を待ち、入力欄が空になっていれば送信成功とみなす
    #   3) 送信されていなければ送信ボタン fallback
    #   4) それでも失敗なら既存の SEND_BUTTON_SELECTORS を総当たり
    sent = False
    send_method = ""
    tried_send: list[str] = []

    # 送信前の状態を記録 (送信成功検知用)
    url_before_send = page.url
    try:
        resp_count_before = await page.locator("div.font-claude-response").count()
    except Exception:
        resp_count_before = 0
    try:
        send_btn_count_before = await page.locator(
            'button[aria-label*="送信"], button[aria-label*="Send"]'
        ).count()
    except Exception:
        send_btn_count_before = 0

    async def _wait_for_send_success(max_poll_sec: float = 8.0,
                                     interval_sec: float = 0.5) -> tuple[bool, str]:
        """複数シグナルの OR 判定で送信成功を短ポーリング検知する。

        成功シグナル (優先順):
          1) 応答要素 (div.font-claude-response) の個数が送信前より増えた
          2) URL に /chat/ が含まれる状態で維持されている
             (/new から即 redirect 済みケースも含む = URL に /chat/ が含まれれば OK)
          3) 入力欄が消滅した or 中身が空になった
          4) 送信ボタンが送信前より減った / disabled 化した

        Returns:
            (成功したか, 判定根拠の説明)
        """
        import asyncio
        deadline = asyncio.get_event_loop().time() + max_poll_sec
        iteration = 0
        while True:
            iteration += 1
            # 現在の URL
            try:
                current_url = page.url
            except Exception:
                current_url = ""
            # 応答要素数
            try:
                resp_count_now = await page.locator("div.font-claude-response").count()
            except Exception:
                resp_count_now = resp_count_before
            # 入力欄の状態
            input_empty = False
            input_disappeared = False
            try:
                cnt = await page.locator(used_selector).count()
                if cnt == 0:
                    input_disappeared = True
                else:
                    try:
                        inner = await page.locator(used_selector).first.inner_text(timeout=1_000)
                        input_empty = not inner or len(inner.strip()) == 0
                    except Exception:
                        pass
            except Exception:
                pass
            # 送信ボタン数
            try:
                send_btn_count_now = await page.locator(
                    'button[aria-label*="送信"], button[aria-label*="Send"]'
                ).count()
            except Exception:
                send_btn_count_now = send_btn_count_before

            log.info(
                "[Send] success-check iter=%d url=%s response_count=%d "
                "input_empty=%s input_disappeared=%s send_btn_count=%d",
                iteration, current_url, resp_count_now,
                input_empty, input_disappeared, send_btn_count_now,
            )

            # (1) 応答要素が増えた
            if resp_count_now > resp_count_before:
                log.info("[Send] success detected by response element")
                return True, "response_element"
            # (2) URL が /chat/ を含む (維持されている)
            if "/chat/" in current_url:
                log.info("[Send] success detected by chat url")
                return True, "chat_url"
            # (3) 入力欄が消滅 / 空
            if input_disappeared:
                log.info("[Send] success detected by input disappeared")
                return True, "input_disappeared"
            if input_empty:
                log.info("[Send] success detected by empty input")
                return True, "empty_input"
            # (4) 送信ボタンが減った (補助)
            if send_btn_count_now < send_btn_count_before:
                log.info("[Send] success detected by send button count decrease")
                return True, "send_btn_decrease"

            # deadline チェック
            if asyncio.get_event_loop().time() >= deadline:
                log.info("[Send] success-check timed out; fallback to button")
                return False, "timeout"
            await asyncio.sleep(interval_sec)

    # --- 方法1: Enter キー送信 (最優先) ---
    tried_send.append("Enter キー (最優先)")
    try:
        log.info("[Send] Enter送信実行")
        await page.keyboard.press("Enter")
        ok, reason = await _wait_for_send_success(max_poll_sec=8.0, interval_sec=0.5)
        if ok:
            sent = True
            send_method = f"Enter ({reason})"
            log.info("[Send] 送信成功 (Enter キー, 根拠=%s)", reason)
    except Exception as e:
        log.debug("[Send] Enter 送信失敗: %s", e)

    # --- 方法2: ボタン送信フォールバック (DOM 変化なし時のみ) ---
    # 依頼指定の配列候補
    EXTRA_SEND_BUTTON_SELECTORS = [
        'button:has-text("Send")',
        'button:has-text("送信")',
        'button[aria-label="Send message"]',
        'button[data-testid="send-button"]',
    ]
    if not sent:
        log.info("[Send] ボタン送信フォールバック実行")
        for selector in EXTRA_SEND_BUTTON_SELECTORS:
            tried_send.append(f"button: {selector}")
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0 and await btn.is_visible(timeout=2_000):
                    await btn.click(timeout=3_000)
                    ok, reason = await _wait_for_send_success(
                        max_poll_sec=6.0, interval_sec=0.5
                    )
                    if ok:
                        sent = True
                        send_method = f"button: {selector} ({reason})"
                        log.info("[Send] 使用ボタンセレクタ: %s", selector)
                        log.info("[Send] 送信成功 (ボタン: %s, 根拠=%s)", selector, reason)
                        break
            except Exception as e:
                log.debug("[Send] 送信ボタン失敗 (%s): %s", selector, e)
                continue

    # --- 方法3: 既存の SEND_BUTTON_SELECTORS 総当たり (最終フォールバック) ---
    if not sent:
        for selector in SEND_BUTTON_SELECTORS:
            tried_send.append(f"button: {selector}")
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0 and await btn.is_visible(timeout=2_000):
                    await btn.click(timeout=3_000)
                    ok, reason = await _wait_for_send_success(
                        max_poll_sec=6.0, interval_sec=0.5
                    )
                    if ok:
                        sent = True
                        send_method = f"button: {selector} ({reason})"
                        log.info("[Send] 使用ボタンセレクタ: %s", selector)
                        log.info("[Send] 送信成功 (ボタン: %s, 根拠=%s)", selector, reason)
                        break
            except Exception as e:
                log.debug("[Send] 送信ボタン失敗 (%s): %s", selector, e)
                continue

    if not sent:
        debug = await collect_debug_info(page, "send_submit", tried_send)
        write_error_log(f"プロンプト送信失敗\n{debug}")
        raise RuntimeError("プロンプトの送信に失敗しました。詳細は output/error.log を確認。")

    log.info("[Send] 送信方法: %s", send_method)


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

    # 方法2: font-claude-response 配下の p/ul/ol/pre を拾う (厳密フォールバック)
    # ユーザー発話 (div.ProseMirror) は含めない
    try:
        resp_containers = page.locator("div.font-claude-response")
        c_count = await resp_containers.count()
        if c_count > 0:
            last_resp = resp_containers.last
            paragraphs = last_resp.locator("p, ul, ol, pre")
            p_count = await paragraphs.count()
            if p_count > 0:
                texts = []
                for i in range(p_count):
                    try:
                        t = await paragraphs.nth(i).inner_text(timeout=1_000)
                        if t and t.strip():
                            texts.append(t.strip())
                    except Exception:
                        continue
                if texts:
                    return "\n".join(texts), "font-claude-response > p/ul/ol/pre"
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
    log.info("出力先     : %s", OUTPUT_DIR)
    log.info("")

    # --- Playwright import ---
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        msg = (
            "Playwright がインストールされていません。\n"
            "以下を実行してください:\n"
            "  pip install playwright"
        )
        log.error(msg)
        write_error_log(msg)
        sys.exit(1)

    CDP_ENDPOINT = "http://127.0.0.1:9222"

    async with async_playwright() as pw:
        log.info("既存の Chrome (CDP: 9222) に接続中...")
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_ENDPOINT)
        except Exception as e:
            msg = (
                f"CDP 接続に失敗しました ({CDP_ENDPOINT})。\n"
                f"以下を確認してください:\n"
                f"  1. Chrome が --remote-debugging-port=9222 付きで起動しているか\n"
                f"     起動例: open -na 'Google Chrome' --args --remote-debugging-port=9222\n"
                f"  2. Chrome 上で Claude にログイン済みか\n"
                f"詳細: {e}"
            )
            log.error(msg)
            write_error_log(msg, exc=e)
            sys.exit(1)

        if not browser.contexts:
            msg = (
                "CDP 接続先の Chrome に context がありません。\n"
                "Chrome が正しく起動しているか確認してください。"
            )
            log.error(msg)
            write_error_log(msg)
            sys.exit(1)

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        log.info("既存 Chrome への接続に成功 (ページ数: %d)", len(context.pages))

        try:
            # Step 1: Claude.ai に遷移 (既に claude.ai/new なら goto しない)
            log.info("[Step 1/6] Claude.ai に遷移... 現在URL=%s", page.url)
            if "claude.ai/new" not in page.url:
                log.info("[Step 1/6] goto 実行: %s", CLAUDE_URL)
                await page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=30_000)
            else:
                log.info("[Step 1/6] 既に /new のため goto をスキップ")

            # Step 2: ログイン確認
            log.info("[Step 2/6] ログイン確認...")
            await wait_for_login(page)

            # Step 3: 新規チャット画面に遷移 (既に /new ならスキップ)
            log.info("[Step 3/6] 新規チャット画面に遷移... 現在URL=%s", page.url)
            if "claude.ai/new" not in page.url:
                log.info("[Step 3/6] goto 実行: %s", CLAUDE_URL)
                await page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=30_000)
            else:
                log.info("[Step 3/6] 既に /new のため goto をスキップ")
            await page.wait_for_timeout(2_000)

            # Step 4: PDF アップロード
            # アップロード前に入力 UI (ProseMirror) の描画完了を待つ
            log.info("[Step 4/6] チャット入力 UI 描画待機中...")
            try:
                await page.wait_for_selector(
                    "div.ProseMirror[contenteditable='true']",
                    timeout=10_000,
                )
                log.info("[Step 4/6] 入力 UI 描画完了")
            except Exception as e:
                log.warning("[Step 4/6] 入力 UI 待機タイムアウト: %s (続行)", e)
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
            # CDP 接続を切断する (既存 Chrome 自体は閉じない)
            log.info("CDP 接続を切断しています (Chrome は閉じません)...")
            try:
                await browser.close()
            except Exception:
                pass
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
