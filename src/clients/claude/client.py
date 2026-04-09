"""Claude API クライアントモジュール。

Anthropic API を通じて Claude モデルを呼び出す機能を提供する。
"""

import json

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
        text = self.send_message(prompt, system=system)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON response: %s", e)
            raise ClientError(f"JSON parse failed: {e}") from e
