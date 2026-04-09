# ai-workflow-runtime

Claude + Python による AI ワークフロー実行基盤。

## このリポジトリの目的

本リポジトリは **AI ワークフローの実行基盤** です。

以下の処理を担当します:

- Claude API 呼び出し
- Python による データ処理
- 外部 API 連携
- バッチ処理
- ログ出力・エラー処理

## cowork-assets との関係

| リポジトリ | 役割 |
|---|---|
| **cowork-assets** | 設計資産の管理（core / rule / workflow / automation の設計、プロンプト、データ構造） |
| **ai-workflow-runtime**（本リポジトリ） | 設計資産を実行するランタイム基盤 |

cowork-assets が「何をするか」を定義し、本リポジトリが「どう動かすか」を実装します。

## ディレクトリ構成

```
src/
├── common/          # 共通処理（ロガー、例外定義）
├── clients/         # 外部 API クライアント
│   └── claude/      # Claude API クライアント
├── tools/           # 機能単位のツール
│   └── pdf_preprocess/  # PDF 前処理
├── workflows/       # 業務フロー
│   └── sample_workflow/ # サンプルワークフロー
├── rules/           # 判定ロジック（バリデーション等）
├── schemas/         # データ構造（Pydantic モデル）
└── main.py          # エントリポイント
```

### レイヤ間の依存ルール

```
workflows → tools / rules / clients
tools     → clients / common
rules     → common
clients   → 外部 API のみ
```

逆方向の依存は禁止です。

## 起動方法

### 1. 環境準備

```bash
# Python 3.11 以上が必要
python --version

# 依存パッケージのインストール
pip install -e .
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して ANTHROPIC_API_KEY を設定
```

### 3. 実行

```bash
python -m src.main
```

## 今後の拡張方針

- `tools/` に新規ツールを追加（OCR、外部 API 連携等）
- `workflows/` に業務フローを追加
- `rules/` にバリデーション・判定ルールを追加
- `clients/` に外部サービスクライアントを追加
- `configs/` に環境別設定ファイルを配置
- `scripts/` にバッチ実行スクリプトを配置
- `.github/workflows/` に CI/CD パイプラインを構築
- cowork-assets のプロンプト・ルール定義との連携強化
