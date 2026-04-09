"""共通例外定義モジュール。

レイヤごとの例外クラスを定義し、エラーの発生箇所を明確にする。
"""


class WorkflowError(Exception):
    """ワークフロー実行時のエラー。"""

    pass


class ClientError(Exception):
    """外部クライアント接続時のエラー。"""

    pass


class ValidationError(Exception):
    """バリデーション失敗時のエラー。"""

    pass
