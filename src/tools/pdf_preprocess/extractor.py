"""PDF テキスト抽出モジュール。

PyMuPDF (fitz) を使用して PDF からテキストを抽出する。
テキストレイヤがない画像ベース PDF の場合は、
画像前処理 + 向き補正 + pytesseract による OCR をフォールバックとして実行する。
OCR 後テキストには軽量な整形処理を施してから返す。

将来的に Cloud OCR (Google Cloud Vision / Document AI 等) に差し替える場合は、
_run_ocr_on_pages() を置き換えるだけで対応できる構成になっている。
"""

import io
import os
import re
import shutil
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF

from src.common import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────
# 設定定数
# ──────────────────────────────────────────────

# OCR 用の画像解像度 (DPI)。高めに設定して文字の輪郭を鮮明にする。
OCR_DPI = 400

# 二値化しきい値（0-255）。帳票の薄い文字を残すため低めに設定。
BINARIZE_THRESHOLD = 120

# メディアンフィルタのカーネルサイズ（奇数）。小さいほど軽い除去。
MEDIAN_FILTER_SIZE = 3

# 向き補正で試す回転角度
ROTATION_CANDIDATES = [0, 90, 180, 270]

# OCR 候補設定（向き決定後に比較する）
# (lang, config の説明, config 文字列)
OCR_CANDIDATES: list[tuple[str, str, str]] = [
    ("jpn+eng",          "psm6", "--oem 3 --psm 6"),
    ("jpn+jpn_vert+eng", "psm6", "--oem 3 --psm 6"),
    ("jpn+eng",          "psm4", "--oem 3 --psm 4"),
]

# 前処理デバッグ画像を保存するか（True で output/ocr_debug_page_N.png を保存）
SAVE_DEBUG_IMAGES = True

# 1文字行として保持する意味のある文字（除去しない）
MEANINGFUL_SINGLE_CHARS = set(
    "年月日時分秒円名前後左右上下中大小高低長短"
    "東西南北春夏秋冬男女父母子人口手目耳足心頭"
    "有無可否済未計合他備注記項番号"
)

# ノイズ行判定用の記号パターン（行の大半がこれらなら除去）
_NOISE_SYMBOLS = re.compile(
    r"^[\s|+\-_=.,:;!?・。、「」『』【】（）()\[\]{}<>~～…※#*@&%^/\\'\"`°"
    r"①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]+$"
)

# 連続空白を1つに圧縮する正規表現
_MULTI_SPACES = re.compile(r"[ 　]{2,}")

# 出力ディレクトリ
_OUTPUT_DIR = Path("output")


# ──────────────────────────────────────────────
# Tesseract 検出
# ──────────────────────────────────────────────

def _check_tesseract_available() -> str:
    """Tesseract OCR が利用可能か確認する。

    Returns:
        Tesseract 実行ファイルのパス。

    Raises:
        RuntimeError: Tesseract が見つからない場合。
    """
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "pytesseract is not installed. Run: pip install pytesseract Pillow"
        )

    tesseract_path = shutil.which("tesseract")
    if tesseract_path:
        return tesseract_path

    win_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if win_default.exists():
        return str(win_default)

    raise RuntimeError(
        "Tesseract OCR is not installed or not found in PATH. "
        "Install from: https://github.com/UB-Mannheim/tesseract/wiki "
        "and ensure 'tesseract' is on PATH, or install to the default location."
    )


# ──────────────────────────────────────────────
# 画像前処理（OCR入口の精度改善）
# ──────────────────────────────────────────────

def _preprocess_image_for_ocr(img: "Image.Image", page_num: int, total_pages: int) -> "Image.Image":
    """OCR 精度向上のための画像前処理を行う。

    以下の順で処理する:
    1. グレースケール化
    2. コントラスト強調
    3. 二値化（固定しきい値。帳票罫線を消しつつ文字を残す）
    4. 白背景膨張（罫線の残り・細いノイズ線を消す）
    5. メディアンフィルタによるノイズ除去

    前処理失敗時は元画像をそのまま返す（OCR 自体は継続）。

    Args:
        img: PIL Image オブジェクト。
        page_num: 現在のページ番号（1始まり、ログ用）。
        total_pages: 総ページ数（ログ用）。

    Returns:
        前処理済みの PIL Image オブジェクト。
    """
    from PIL import ImageFilter, ImageOps

    try:
        logger.info("OCR preprocess Page %d/%d: starting", page_num, total_pages)

        # 1. グレースケール化
        img = img.convert("L")

        # 2. コントラスト強調（ヒストグラムを正規化）
        img = ImageOps.autocontrast(img, cutoff=2)

        # 3. 二値化（しきい値以上 → 白、未満 → 黒）
        img = img.point(lambda p: 255 if p >= BINARIZE_THRESHOLD else 0, mode="1")

        # 4. 白背景膨張（MinFilter で黒領域を縮小 = 白を膨張させ、細い罫線を消す）
        img = img.filter(ImageFilter.MinFilter(size=3))

        # 5. メディアンフィルタによるノイズ除去
        img = img.filter(ImageFilter.MedianFilter(size=MEDIAN_FILTER_SIZE))

        logger.info("OCR preprocess Page %d/%d: completed", page_num, total_pages)

        # デバッグ画像保存
        if SAVE_DEBUG_IMAGES:
            try:
                _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                debug_path = _OUTPUT_DIR / f"ocr_debug_page_{page_num}.png"
                img.save(str(debug_path))
                logger.info("OCR debug image saved: %s", debug_path)
            except Exception as e:
                logger.warning("Failed to save debug image: %s", e)

        return img

    except Exception as e:
        logger.warning(
            "OCR preprocess Page %d/%d: failed (%s), using original image",
            page_num, total_pages, e,
        )
        return img


# ──────────────────────────────────────────────
# 向き補正・OCR設定比較
# ──────────────────────────────────────────────

def _find_best_rotation(
    img: "Image.Image",
    page_num: int,
    total_pages: int,
) -> tuple["Image.Image", int]:
    """4方向を試して最も文字数が多い回転角度を選択する。

    基本設定 (jpn+eng, --oem 3 --psm 6) で各角度の文字数を比較する。

    Args:
        img: 前処理済みの PIL Image オブジェクト。
        page_num: 現在のページ番号（1始まり、ログ用）。
        total_pages: 総ページ数（ログ用）。

    Returns:
        (最適角度に回転済みの画像, 採用された角度)。
    """
    import pytesseract

    base_lang = "jpn+eng"
    base_config = "--oem 3 --psm 6"

    best_rotation = 0
    best_chars = 0
    best_img = img

    logger.info("OCR rotation detection Page %d/%d: testing %s", page_num, total_pages, ROTATION_CANDIDATES)

    for angle in ROTATION_CANDIDATES:
        try:
            rotated = img.rotate(-angle, expand=True) if angle != 0 else img
            text = pytesseract.image_to_string(rotated, lang=base_lang, config=base_config)
            char_count = len(text.strip())
            logger.info(
                "  rotation=%d°: %d chars", angle, char_count,
            )
            if char_count > best_chars:
                best_chars = char_count
                best_rotation = angle
                best_img = rotated
        except Exception as e:
            logger.warning("  rotation=%d°: failed (%s)", angle, e)

    logger.info(
        "OCR rotation detection Page %d/%d: best=%d° (%d chars)",
        page_num, total_pages, best_rotation, best_chars,
    )
    return best_img, best_rotation


def _find_best_ocr_config(
    img: "Image.Image",
    page_num: int,
    total_pages: int,
    rotation: int,
) -> tuple[str, str, str, str]:
    """複数の OCR 設定候補を試して最も文字数が多い組合せを選択する。

    Args:
        img: 向き補正済みの PIL Image オブジェクト。
        page_num: 現在のページ番号（1始まり、ログ用）。
        total_pages: 総ページ数（ログ用）。
        rotation: 採用された回転角度（ログ用）。

    Returns:
        (最良のテキスト, 採用された lang, 採用された config 説明, 採用された config)。
        全候補で失敗した場合は ("", "", "", "") を返す。
    """
    import pytesseract

    best_text = ""
    best_lang = ""
    best_label = ""
    best_config = ""

    logger.info("OCR config comparison Page %d/%d: testing %d candidates", page_num, total_pages, len(OCR_CANDIDATES))

    for lang, label, config in OCR_CANDIDATES:
        try:
            text = pytesseract.image_to_string(img, lang=lang, config=config)
            text = text.strip()
            logger.info(
                "  lang=%s, %s: %d chars", lang, label, len(text),
            )
            if len(text) > len(best_text):
                best_text = text
                best_lang = lang
                best_label = label
                best_config = config
        except Exception as e:
            logger.warning("  lang=%s, %s: failed (%s)", lang, label, e)

    if best_text:
        logger.info(
            "OCR Page %d/%d: BEST → rotation=%d°, lang=%s, %s, %d chars",
            page_num, total_pages, rotation, best_lang, best_label, len(best_text),
        )

    return best_text, best_lang, best_label, best_config


# ──────────────────────────────────────────────
# OCR 実行（将来 Cloud OCR に差し替える場合はここを置換）
# ──────────────────────────────────────────────

def _run_ocr_on_pages(doc: fitz.Document, pdf_path: str) -> str:
    """PDF の各ページに対して Google Cloud Vision API で OCR を実行し、生テキストを返す。

    各ページを PyMuPDF で PNG 画像に変換し、
    Cloud Vision の document_text_detection でテキストを抽出する。
    Cloud Vision は内部で自動的に向き補正・ノイズ除去を行うため、
    画像前処理や回転検出は行わない。

    認証には環境変数 GOOGLE_APPLICATION_CREDENTIALS を使用する。

    Args:
        doc: 開済みの PyMuPDF Document。
        pdf_path: ログ用の PDF ファイルパス。

    Returns:
        全ページの OCR 生テキストを結合した文字列。

    Raises:
        RuntimeError: Cloud Vision API が利用不可の場合、
                      または全ページで OCR テキストが空の場合。
    """
    # Cloud Vision クライアントの初期化
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds_path:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set. "
            "Set it to the path of your GCP service account JSON key file."
        )
    if not Path(creds_path).exists():
        raise RuntimeError(
            f"GOOGLE_APPLICATION_CREDENTIALS file not found: {creds_path}"
        )

    try:
        from google.cloud import vision
    except ImportError:
        raise RuntimeError(
            "google-cloud-vision is not installed. "
            "Run: pip install google-cloud-vision"
        )

    client = vision.ImageAnnotatorClient()
    logger.info("Google Cloud Vision client initialized (credentials=%s)", creds_path)

    total_pages = len(doc)
    scale = OCR_DPI / 72
    mat = fitz.Matrix(scale, scale)

    page_texts: list[str] = []

    for i in range(total_pages):
        try:
            page = doc[i]
            pix = page.get_pixmap(matrix=mat)
            png_bytes = pix.tobytes("png")

            logger.info("OCR Page %d/%d: sending to Cloud Vision (%d bytes)", i + 1, total_pages, len(png_bytes))

            image = vision.Image(content=png_bytes)
            response = client.document_text_detection(
                image=image,
                image_context=vision.ImageContext(
                    language_hints=["ja", "en"],
                ),
            )

            if response.error.message:
                logger.warning(
                    "OCR Page %d/%d: Cloud Vision error: %s",
                    i + 1, total_pages, response.error.message,
                )
                continue

            text = response.full_text_annotation.text.strip() if response.full_text_annotation.text else ""
            logger.info("OCR Page %d/%d: extracted %d chars", i + 1, total_pages, len(text))

            if text:
                page_texts.append(text)
            else:
                logger.warning("OCR Page %d/%d: no text recognized", i + 1, total_pages)
        except Exception as e:
            logger.warning("OCR Page %d/%d: failed: %s", i + 1, total_pages, e)

    combined_text = "\n\n".join(page_texts)

    if len(combined_text) == 0:
        raise RuntimeError(
            f"OCR produced no text from PDF: {pdf_path} ({total_pages} pages). "
            "The document may be blank or unreadable."
        )

    logger.info(
        "OCR extraction completed: total %d chars from %d/%d pages",
        len(combined_text), len(page_texts), total_pages,
    )
    return combined_text


# ──────────────────────────────────────────────
# OCR 後テキスト整形
# ──────────────────────────────────────────────

def _count_japanese_chars(text: str) -> int:
    """文字列中の日本語文字（CJK漢字・ひらがな・カタカナ）の数を返す。

    Args:
        text: 判定対象の文字列。

    Returns:
        日本語文字の数。
    """
    count = 0
    for c in text:
        name = unicodedata.name(c, "")
        if "CJK" in name or "HIRAGANA" in name or "KATAKANA" in name:
            count += 1
    return count


def _is_noise_line(line: str) -> bool:
    """行がノイズかどうかを判定する。

    以下の条件でノイズと判定する:
    - 空行
    - 記号のみの行
    - 1文字で意味のない文字
    - 2-3文字で日本語が含まれない行
    - 4文字以上でも日本語が1文字もなく、かつ短い行（8文字以下）

    Args:
        line: 判定対象の行（strip 済み想定）。

    Returns:
        True ならノイズとして除去する。
    """
    if not line:
        return True

    # 記号だけの行
    if _NOISE_SYMBOLS.match(line):
        return True

    line_len = len(line)

    # 1文字の行: 意味のある文字はノイズとみなさない
    if line_len == 1:
        return line not in MEANINGFUL_SINGLE_CHARS

    # 2-3文字の行: 日本語が1文字も含まれなければノイズ
    if line_len <= 3:
        return _count_japanese_chars(line) == 0

    # 短い行（8文字以下）で日本語が1文字もなければノイズ
    if line_len <= 8:
        return _count_japanese_chars(line) == 0

    return False


def _clean_ocr_text(raw_text: str) -> tuple[str, int, int]:
    """OCR 生テキストを整形する。

    以下の順で処理する:
    1. 行単位で前後空白を trim
    2. 完全空行を除去
    3. ノイズ行（記号のみ・ゴミ文字・日本語なし短行）を除去
    4. 連続空白を1つに圧縮
    5. 分断された短い行を慎重に前行に連結

    意味を壊すような大胆な加工は行わない。

    Args:
        raw_text: OCR から得られた生テキスト。

    Returns:
        (整形後テキスト, 削除行数, 連結行数)。
    """
    lines = raw_text.split("\n")
    original_count = len(lines)

    # STEP 1-3: trim + 空行除去 + ノイズ行除去
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _is_noise_line(stripped):
            continue
        # STEP 4: 連続空白を1つに圧縮
        stripped = _MULTI_SPACES.sub(" ", stripped)
        cleaned.append(stripped)

    removed_count = original_count - len(cleaned)

    # STEP 5: 分断された短い行を慎重に前行へ連結
    # 条件: 前行が句読点・括弧閉じで終わっていない AND 現行が短い（4文字以下）AND 現行に日本語あり
    merged: list[str] = []
    merge_count = 0
    for line in cleaned:
        if (
            merged
            and len(line) <= 4
            and _count_japanese_chars(line) > 0
            and not merged[-1].endswith(("。", "、", "）", ")", "」", "』", "】", ".", ":", "："))
            and not line.startswith(("・", "（", "(", "「", "『", "【"))
        ):
            merged[-1] = merged[-1] + line
            merge_count += 1
        else:
            merged.append(line)

    result = "\n".join(merged)
    return result, removed_count, merge_count


# ──────────────────────────────────────────────
# OCR 結果の保存
# ──────────────────────────────────────────────

def _save_ocr_raw(text: str) -> None:
    """OCR 生テキストを output/ocr_raw.txt に保存する。

    Args:
        text: OCR 生テキスト。
    """
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_file = _OUTPUT_DIR / "ocr_raw.txt"
    raw_file.write_text(text, encoding="utf-8")
    logger.info("OCR raw text saved: %s (%d chars)", raw_file, len(text))


def _save_ocr_cleaned(text: str) -> None:
    """整形済み OCR テキストを output/ocr_cleaned.txt に保存する。

    Args:
        text: 整形済み OCR テキスト。
    """
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cleaned_file = _OUTPUT_DIR / "ocr_cleaned.txt"
    cleaned_file.write_text(text, encoding="utf-8")
    logger.info("OCR cleaned text saved: %s (%d chars)", cleaned_file, len(text))


# ──────────────────────────────────────────────
# OCR フォールバック統合関数
# ──────────────────────────────────────────────

def _ocr_extract_from_pdf(doc: fitz.Document, pdf_path: str) -> str:
    """画像ベース PDF から OCR でテキストを抽出し、整形して返す。

    処理の流れ:
    1. _run_ocr_on_pages() で OCR 生テキストを取得
    2. _save_ocr_raw() で生テキストを保存
    3. _clean_ocr_text() で整形
    4. _save_ocr_cleaned() で整形済みテキストを保存
    5. 整形済みテキストを返す

    将来 Cloud OCR に差し替える場合は、手順1 の _run_ocr_on_pages() を
    別の実装に置き換えるだけで、手順2-5 はそのまま動作する。

    Args:
        doc: 開済みの PyMuPDF Document。
        pdf_path: ログ用の PDF ファイルパス。

    Returns:
        整形済みの OCR テキスト。

    Raises:
        RuntimeError: OCR 実行に失敗した場合。
    """
    # 1. OCR 実行（差し替えポイント）
    raw_text = _run_ocr_on_pages(doc, pdf_path)

    # 2. 生テキスト保存
    _save_ocr_raw(raw_text)

    # 3. テキスト整形
    try:
        cleaned_text, removed_lines, merged_lines = _clean_ocr_text(raw_text)
        logger.info(
            "OCR text cleaning: %d chars → %d chars (removed %d lines, merged %d lines)",
            len(raw_text), len(cleaned_text), removed_lines, merged_lines,
        )
    except Exception as e:
        logger.warning("OCR text cleaning failed (%s), using raw text", e)
        cleaned_text = raw_text

    # 4. 整形済みテキスト保存
    _save_ocr_cleaned(cleaned_text)

    return cleaned_text


# ──────────────────────────────────────────────
# 公開関数（外部I/F — 変更なし）
# ──────────────────────────────────────────────

def extract_text_from_pdf(pdf_path: str) -> str:
    """PDF ファイルから全ページのテキストを抽出する。

    まず PyMuPDF で通常のテキスト抽出を試みる。
    全ページでテキストが取得できなかった場合のみ、
    OCR によるフォールバック抽出を実行する。

    Args:
        pdf_path: PDF ファイルのパス。

    Returns:
        全ページから結合されたテキスト。

    Raises:
        FileNotFoundError: 指定されたファイルが存在しない場合。
        RuntimeError: PDF を開けない場合、または全手段でテキストが空の場合。
    """
    path = Path(pdf_path)
    if not path.exists():
        logger.error("PDF file not found: %s", pdf_path)
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    logger.info("Extracting text from PDF: %s", pdf_path)

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        logger.error("Failed to open PDF: %s", e)
        raise RuntimeError(f"Failed to open PDF: {pdf_path}") from e

    total_pages = len(doc)
    logger.info("PDF opened successfully (pages=%d)", total_pages)

    # ── 通常テキスト抽出 ──
    page_texts: list[str] = []

    for i in range(total_pages):
        try:
            page = doc[i]
            text = page.get_text() or ""
            text = text.strip()
            logger.info("Page %d/%d: extracted %d chars", i + 1, total_pages, len(text))

            if text:
                page_texts.append(text)
            else:
                logger.warning("Page %d/%d: no text extracted (empty or image-only)", i + 1, total_pages)
        except Exception as e:
            logger.warning("Page %d/%d: extraction failed: %s", i + 1, total_pages, e)

    combined_text = "\n\n".join(page_texts)
    total_chars = len(combined_text)

    if total_chars > 0:
        logger.info("Text extraction completed: total %d chars from %d/%d pages", total_chars, len(page_texts), total_pages)
        doc.close()
        return combined_text

    # ── OCR フォールバック ──
    logger.info(
        "No text layer found in %s (%d pages). Falling back to OCR.",
        pdf_path, total_pages,
    )

    try:
        ocr_text = _ocr_extract_from_pdf(doc, pdf_path)
    finally:
        doc.close()

    return ocr_text
