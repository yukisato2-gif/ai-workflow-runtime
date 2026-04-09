"""共通ロガー設定モジュール。

全モジュールで統一されたログフォーマットを提供する。
"""

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """指定された名前でロガーを取得する。

    Args:
        name: ロガー名。通常は __name__ を渡す。

    Returns:
        設定済みの Logger インスタンス。
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger
