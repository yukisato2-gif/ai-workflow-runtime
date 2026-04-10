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
│   ├── monitoring_record/ # モニタリング記録抽出
│   └── meeting_record/    # 担当者会議録抽出
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

## バッチ処理

### 担当者会議録: 1フォルダ処理

指定フォルダ内のPDFを順番に OCR → 抽出 → Google Sheets 書き込みします。
登録済みファイル（B列ファイル名で判定）はスキップされます。

```bash
python scripts/batch_meeting.py <フォルダパス>
```

### 担当者会議録: 複数拠点巡回

親フォルダ配下の拠点フォルダ（GH を含むフォルダ）を巡回し、
各拠点の `032_個別支援計画関連PDF格納フォルダ` 内のPDFを処理します。

```bash
python scripts/scan_sites.py <親フォルダパス>
```

#### スキップ条件

- フォルダ名に `GH` を含まないフォルダはスキャン対象外
- `032_個別支援計画関連PDF格納フォルダ` がない拠点はスキップ
- フォルダ内にPDFがない場合はスキップ
- B列ファイル名が既にシートに存在する場合は重複スキップ

## 設計思想：「何をするか」と「どう動くか」の分離

本プロジェクトは以下の分離原則に基づいています。

| 関心 | 担当リポジトリ | 例 |
|---|---|---|
| **何をするか**（業務設計） | cowork-assets | プロンプト設計、データ構造定義、業務フロー設計 |
| **どう動くか**（実行基盤） | ai-workflow-runtime（本リポジトリ） | API呼び出し、PDF処理、バッチ実行、エラー処理 |

runtime 側は業務ロジックの「実行方法」に集中し、業務ルールの「定義」は cowork-assets に委ねます。

## ワークフロー一覧

現在のワークフローと実装場所の対応は `configs/workflow_registry.yaml` に台帳化しています。

| ID | 名称 | 実装場所 | 実行方法 |
|---|---|---|---|
| WF-001 | モニタリング記録抽出 | `src/workflows/monitoring_record/` | `python -m src.main` |
| WF-002 | 担当者会議録バッチ | `src/workflows/meeting_record/` | `python scripts/batch_meeting.py <フォルダ>` |
| WF-003 | 複数拠点巡回処理 | `scripts/scan_sites.py` | `python scripts/scan_sites.py <親フォルダ>` |

詳細は [configs/workflow_registry.yaml](configs/workflow_registry.yaml) を参照してください。

## 現状の構造上の課題

以下は今後の段階的な改善対象として認識している点です。現時点では実行に影響はありません。

- **WF-002 のワークフロー本体は `src/workflows/meeting_record/` へ切り出し済み**
  - `scripts/batch_meeting.py` は CLI入口・バッチ制御として維持
- **`sample_workflow` は `monitoring_record` へ rename 済み**

## 今後の拡張方針

- `tools/` に新規ツールを追加（OCR、外部 API 連携等）
- `workflows/` に業務フローを追加
- `rules/` にバリデーション・判定ルールを追加
- `clients/` に外部サービスクライアントを追加
- `configs/` に環境別設定ファイルを配置
- `scripts/` にバッチ実行スクリプトを配置
- `.github/workflows/` に CI/CD パイプラインを構築
- cowork-assets のプロンプト・ルール定義との連携強化

## 段階移行方針

本リポジトリは以下の理想構造を目指して段階的に整理を進めます。
ただし、**既存の実行パス・import・スクリプトを壊さないこと**を最優先とし、一括移行は行いません。

### 理想構造（参考）

```
src/
├── common/          # 共通処理
├── clients/         # 外部 API クライアント
├── tools/           # 機能単位のツール
├── workflows/       # 全ワークフローの実装
├── schemas/         # データ構造
├── rules/           # 判定ロジック
└── orchestrator/    # ワークフロー実行制御（将来）
scripts/             # 実行スクリプト（workflows を呼び出すのみ）
configs/
└── workflow_registry.yaml  # ワークフロー台帳
```

### 移行原則

1. **非破壊優先** — 既存の実行コマンド・import パスは変更しない
2. **見える化先行** — まず `workflow_registry.yaml` で全体を可視化する
3. **段階実施** — ワークフロー単位で順次 `src/workflows/` へ整理
4. **部署別構造にしない** — runtime は技術レイヤ構造を維持する。部署別の分類は cowork-assets 側の責務

### なぜ部署別ディレクトリにしないか

runtime は「どう動くか」を担う技術基盤であり、業務部署の区分とは直交します。
部署別にすると、共通ツールや共通クライアントの重複・分散が発生し、保守性が低下します。
業務単位の整理（どの部署の何の業務か）は cowork-assets 側で管理します。
