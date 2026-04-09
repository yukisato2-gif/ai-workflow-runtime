"""PDF テキスト抽出モジュール。

PyMuPDF (fitz) を使用して PDF からテキストを抽出する。
テキストレイヤがない画像ベース PDF の場合は、
画像前処理 + 向き補正 + pytesseract による OCR をフォールバックとして実行する。
"""

import io
import shutil
from pathlib import Path

import fitz  # PyMuPDF

from src.common import get_logger

logger = get_logger(__name__)

# OCR 用の画像解像度 (DPI)。高めに設定して文字の輪郭を鮮明にする。
OCR_DPI = 400

# 二値化しきい値（0-255）。この値以上を白、未満を黒にする。
BINARIZE_THRESHOLD = 140

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


def _preprocess_image_for_ocr(img: "Image.Image", page_num: int, total_pages: int) -> "Image.Image":
    """OCR 精度向上のための画像前処理を行う。

    以下の順で処理する:
    1. グレースケール化
    2. コントラスト強調
    3. 二値化（固定しきい値）
    4. メディアンフィルタによるノイズ除去

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
        img = ImageOps.autocontrast(img, cutoff=1)

        # 3. 二値化（しきい値以上 → 白、未満 → 黒）
        img = img.point(lambda p: 255 if p >= BINARIZE_THRESHOLD else 0, mode="1")

        # 4. メディアンフィルタによるノイズ除去
        img = img.filter(ImageFilter.MedianFilter(size=MEDIAN_FILTER_SIZE))

        logger.info("OCR preprocess Page %d/%d: completed", page_num, total_pages)
        return img

    except Exception as e:
        logger.warning(
            "OCR preprocess Page %d/%d: failed (%s), using original image",
            page_num, total_pages, e,
        )
        return img


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


def _ocr_extract_from_pdf(doc: fitz.Document, pdf_path: str) -> str:
    """画像ベース PDF の各ページを OCR でテキスト抽出する。

    各ページに対して以下の処理を行う:
    1. 画像化 (400dpi)
    2. 前処理（グレースケール・コントラスト強調・二値化・ノイズ除去）
    3. 向き補正（4方向で最大文字数の角度を選択）
    4. OCR 設定比較（言語/psm の候補で最大文字数を選択）

    一部ページの失敗は警告ログに記録して継続する。

    Args:
        doc: 開済みの PyMuPDF Document。
        pdf_path: ログ用の PDF ファイルパス。

    Returns:
        全ページの OCR テキストを結合した文字列。

    Raises:
        RuntimeError: Tesseract が利用不可の場合、または全ページで OCR テキストが空の場合。
    """
    import pytesseract
    from PIL import Image

    tesseract_path = _check_tesseract_available()
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    logger.info("Tesseract found: %s", tesseract_path)

    total_pages = len(doc)
    scale = OCR_DPI / 72
    mat = fitz.Matrix(scale, scale)

    page_texts: list[str] = []

    for i in range(total_pages):
        try:
            page = doc[i]
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            # 画像前処理
            img = _preprocess_image_for_ocr(img, i + 1, total_pages)

            # 向き補正（4方向で最適角度を選択）
            img, rotation = _find_best_rotation(img, i + 1, total_pages)

            # OCR 設定比較（言語/psm の候補で最適設定を選択）
            text, lang, label, config = _find_best_ocr_config(
                img, i + 1, total_pages, rotation,
            )

            logger.info("OCR Page %d/%d: extracted %d chars", i + 1, total_pages, len(text))

            if text:
                page_texts.append(text)
            else:
                logger.warning("OCR Page %d/%d: no text recognized", i + 1, total_pages)
        except Exception as e:
            logger.warning("OCR Page %d/%d: failed: %s", i + 1, total_pages, e)

    combined_text = "\n\n".join(page_texts)
    total_chars = len(combined_text)

    if total_chars == 0:
        raise RuntimeError(
            f"OCR produced no text from PDF: {pdf_path} ({total_pages} pages). "
            "The document may be blank or unreadable."
        )

    logger.info(
        "OCR extraction completed: total %d chars from %d/%d pages",
        total_chars, len(page_texts), total_pages,
    )

    # OCR 生テキストをファイル保存（デバッグ・精度確認用）
    ocr_output_dir = Path("output")
    ocr_output_dir.mkdir(parents=True, exist_ok=True)
    ocr_output_file = ocr_output_dir / "ocr_raw.txt"
    ocr_output_file.write_text(combined_text, encoding="utf-8")
    logger.info("OCR raw text saved: %s (%d chars)", ocr_output_file, total_chars)

    return combined_text


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
