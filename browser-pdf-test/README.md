# browser-pdf-test

Claude API を使わず、ブラウザ操作 (Playwright) で Claude Web UI に PDF を渡して内容を読み取る検証環境。

**既存の ai-workflow-runtime (Claude API 方式) とは完全に独立。このフォルダ配下だけで完結する。**

---

## 前提条件

- Python 3.11 以上
- インターネット接続
- Claude アカウント (claude.ai にログインできること)

---

## セットアップ

### 1. このフォルダに移動

```bash
cd "/Users/administrator/claude/claude code/browser-pdf-test"
```

Windows の場合:
```cmd
cd "C:\Users\<ユーザー名>\claude\claude code\browser-pdf-test"
```

### 2. Python 仮想環境を作成 (推奨)

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows の場合:
```cmd
python -m venv .venv
.venv\Scripts\activate
```

### 3. 依存パッケージをインストール

```bash
pip install -r requirements.txt
```

### 4. ブラウザをインストール

```bash
playwright install chromium
```

### 5. テスト用 PDF を配置

```bash
cp /path/to/your/test.pdf ./input/
```

---

## 実行方法

### input/ 内の PDF を自動選択

```bash
python run_test.py
```

### ファイル名を指定 (input/ 内)

```bash
python run_test.py sample.pdf
```

### 絶対パスを指定

```bash
python run_test.py /path/to/file.pdf
```

### プロンプトも指定

```bash
python run_test.py sample.pdf "このPDFの主要な情報を抽出してください"
```

---

## 実行の流れ

| ステップ | 内容 | 手動操作 |
|---|---|---|
| 1 | Chromium 起動、Claude.ai に遷移 | 不要 |
| 2 | ログイン確認 | **初回のみ手動ログイン** |
| 3 | 新規チャット画面に遷移 | 不要 |
| 4 | PDF アップロード | 不要 |
| 5 | プロンプト送信 | 不要 |
| 6 | 応答待機・テキスト取得 | 不要 |

---

## 出力

| ファイル | タイミング | 内容 |
|---|---|---|
| `output/result.txt` | 成功時 | Claude の応答テキスト |
| `output/error.log` | 失敗時 | エラー詳細・トレースバック |
| `output/success_*.png` | 成功時 | 画面スクリーンショット |
| `output/error_*.png` | 失敗時 | 画面スクリーンショット |
| `output/fail_*.png` | セレクタ全滅時 | デバッグ用スクリーンショット |
| `test.log` | 常時 | 全ステップの詳細ログ |

`output/` フォルダは実行時に自動作成される。事前作成は不要。

---

## 成功判定

以下が全て満たされれば成功:

1. ターミナルに `テスト成功!` が表示される
2. `output/result.txt` が生成される
3. `output/result.txt` に PDF の要約テキストが含まれている
4. `test.log` に `[ERROR]` が記録されていない

---

## フォルダ構成

```
browser-pdf-test/
├── run_test.py            # メインスクリプト
├── requirements.txt       # 依存: playwright のみ
├── README.md              # この手順書
├── TROUBLESHOOTING.md     # 失敗時の対処法
├── test.log               # 実行ログ (自動生成)
├── input/                 # PDF 置き場 (手動配置)
├── output/                # 結果 (実行時自動作成)
└── .browser_data/         # ブラウザセッション (自動生成)
```

---

## 注意事項

- Claude.ai の Web UI に依存するため、UI 変更で動作しなくなる可能性がある
- CAPTCHA やログイン認証の自動突破は行わない
- 検証目的のテスト環境であり、実運用用ではない
- 詳細なトラブルシューティングは TROUBLESHOOTING.md を参照
