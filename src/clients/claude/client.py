"""Claude API クライアントモジュール。

Anthropic API を通じて Claude モデルを呼び出す機能を提供する。
"""

import json
import re

import anthropic

from src.common import get_logger, ClientError

logger = get_logger(__name__)


class ClaudeClient:
    """Claude API クライアント。

    Anthropic SDK を利用して Claude モデルへリクエストを送信する。
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-opus-20240229",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> None:
        """ClaudeClient を初期化する。

        Args:
            api_key: Anthropic API キー。
            model: 使用するモデル名。
            temperature: 生成時の温度パラメータ。
            max_tokens: 最大トークン数。
        """
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        logger.info("ClaudeClient initialized (model=%s)", self._model)

    def send_message(self, prompt: str, system: str = "") -> str:
        """Claude にメッセージを送信し、テキストレスポンスを取得する。

        Args:
            prompt: ユーザープロンプト。
            system: システムプロンプト（省略可）。

        Returns:
            Claude のレスポンステキスト。

        Raises:
            ClientError: API 呼び出しに失敗した場合。
        """
        try:
            logger.info("Sending message to Claude (length=%d)", len(prompt))
            kwargs: dict = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system

            response = self._client.messages.create(**kwargs)
            text = response.content[0].text
            logger.info("Received response (length=%d)", len(text))
            return text

        except anthropic.APIError as e:
            logger.error("Claude API error: %s", e)
            raise ClientError(f"Claude API call failed: {e}") from e

    @staticmethod
    def _extract_json_text(raw: str) -> str:
        """生レスポンスから JSON 文字列を抽出する。

        以下の順で正規化を行う:
        1. 前後の空白を strip
        2. ```json ... ``` コードブロックがあれば中身を抽出

        Args:
            raw: Claude からの生レスポンス文字列。

        Returns:
            正規化済みの JSON 文字列。
        """
        text = raw.strip()

        # ```json ... ``` または ``` ... ``` のコードブロックを除去
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

        return text

    def send_message_json(self, prompt: str, system: str = "") -> dict:
        """Claude にメッセージを送信し、JSON レスポンスを dict で取得する。

        プロンプトには JSON 形式で回答するよう指示を含めること。

        Args:
            prompt: ユーザープロンプト。
            system: システムプロンプト（省略可）。

        Returns:
            パース済みの dict。

        Raises:
            ClientError: API 呼び出しまたは JSON パースに失敗した場合。
        """
        raw_text = self.send_message(prompt, system=system)

        # デバッグ用: 生レスポンス全文をログ出力
        logger.debug("Raw response from Claude:\n%s", raw_text)
        logger.info(
            "Raw response preview: first_50=%r last_50=%r",
            raw_text[:50],
            raw_text[-50:],
        )

        text = self._extract_json_text(raw_text)
        logger.info("Cleaned text for JSON parse (length=%d): first_50=%r", len(text), text[:50])

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON response: %s", e)
            logger.error("Full cleaned text was:\n%s", text)
            raise ClientError(f"JSON parse failed: {e}") from e
