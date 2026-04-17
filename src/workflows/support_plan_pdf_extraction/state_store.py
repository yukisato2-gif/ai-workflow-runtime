"""処理済み管理モジュール (最小版)。

JSON ファイルに処理済みファイル識別子 (絶対パス) を保存し、
再実行時の重複処理を防ぐ。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from src.common import get_logger

logger = get_logger(__name__)


DEFAULT_STATE_FILE = Path("output") / "support_plan_state.json"


class StateStore:
    """処理済みファイル識別子の集合を JSON に保存する最小ストア。"""

    def __init__(self, state_file: Path | None = None) -> None:
        self._state_file = state_file or DEFAULT_STATE_FILE
        self._processed: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._state_file.exists():
            logger.info("State file not found (new run): %s", self._state_file)
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._processed = set(data.get("processed", []))
            logger.info("State loaded: %d processed entries from %s",
                        len(self._processed), self._state_file)
        except Exception as e:
            logger.warning("State load failed (%s). Starting empty.", e)
            self._processed = set()

    def is_processed(self, key: str) -> bool:
        return key in self._processed

    def mark_processed(self, key: str) -> None:
        self._processed.add(key)
        self._flush()

    def mark_many(self, keys: Iterable[str]) -> None:
        for k in keys:
            self._processed.add(k)
        self._flush()

    def _flush(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps({"processed": sorted(self._processed)},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("State flush failed: %s", e)
