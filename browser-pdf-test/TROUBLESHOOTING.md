# Troubleshooting

失敗しやすいポイントと対処法。

---

## 1. セットアップ失敗

### pip install playwright が失敗

```bash
python3 --version        # 3.11 以上を確認
pip install --upgrade pip
pip install playwright
playwright install chromium
```

### playwright install chromium が失敗

```bash
# Linux の場合は OS 依存パッケージも必要
playwright install-deps chromium
playwright install chromium
```

macOS: Gatekeeper が初回起動をブロックする場合
→ システム環境設定 > セキュリティとプライバシー で許可

---

## 2. ログイン関連

### 「手動ログインが必要です」が出る

初回実行では必ず表示される。ブラウザで手動ログインすること。

2回目以降も表示される場合:
```bash
# セッションリセット
rm -rf .browser_data/
python run_test.py
```

### ログインタイムアウト (5分)

run_test.py 内の定数を変更:
```python
LOGIN_TIMEOUT_MS = 600_000   # 10分に延長
```

### CAPTCHA が表示される

手動で解く。自動突破は行わない。
頻発する場合は時間を置いてから再試行。

---

## 3. PDF アップロード失敗

### error.log の確認

```bash
cat output/error.log
```

「Tried selectors」にどのセレクタを試したかが記録されている。

### セレクタ更新手順

1. ブラウザで https://claude.ai を開く
2. F12 で開発者ツール → Elements
3. ファイルアップロードボタンを右クリック → Inspect
4. 以下の属性を確認:
   - `aria-label`
   - `data-testid`
   - `class`
5. run_test.py の `ATTACH_BUTTON_SELECTORS` に追加

**優先順位:**
1. `data-testid` (テスト用属性、最も安定)
2. `aria-label` (アクセシビリティ用、比較的安定)
3. `class` (最も不安定、頻繁に変わる)

### input[type=file] がない場合

Claude.ai が file input を DOM に持たない場合、
方法2 (file_chooser) か方法3 (ボタン総当たり) に頼る。
全滅時は output/ のスクリーンショットで画面状態を確認。

---

## 4. プロンプト送信失敗

### 入力欄が見つからない

開発者ツールで入力欄の要素を確認:
- `contenteditable="true"` の div があるか
- `ProseMirror` クラスがあるか
- `textarea` が使われているか

run_test.py の `INPUT_FIELD_SELECTORS` に追加。

### 送信が動作しない

Enter キーで送信されない UI の場合:
- 送信ボタンのセレクタを `SEND_BUTTON_SELECTORS` に追加
- 開発者ツールで送信ボタンの `aria-label`, `data-testid` を確認

---

## 5. 応答取得失敗

### セレクタが合わない

開発者ツールで Claude の応答テキスト要素を確認:
1. Claude に何か質問を手動で送信
2. 応答が表示されたら Inspect
3. class 名や data 属性を確認
4. run_test.py の `RESPONSE_TEXT_SELECTORS` に追加

### 応答タイムアウト

大きい PDF では 3分 (180秒) で足りない場合がある:
```python
RESPONSE_TIMEOUT_MS = 300_000   # 5分に延長
```

### テキストが途中で切れる

タイムアウト時に取得済みテキストを返す設計。
テキスト長が不足していれば RESPONSE_TIMEOUT_MS を延長。

---

## 6. Windows 固有の問題

### パスに日本語が含まれてエラー

英数字のみのパスに配置することを推奨。

### 仮想環境の有効化

```powershell
.venv\Scripts\Activate.ps1
```

実行ポリシーエラーの場合:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## 7. ログ・スクリーンショットの確認

| 確認対象 | パス |
|---|---|
| 実行ログ (全ステップ) | `test.log` |
| エラー詳細 | `output/error.log` |
| 失敗時の画面 | `output/error_*.png` |
| セレクタ全滅時の画面 | `output/fail_*.png` |
| 成功時の画面 | `output/success_*.png` |

---

## 8. 既知の制限

| 制限 | 原因 | 回避策 |
|---|---|---|
| UI 変更で動作しなくなる | DOM セレクタ依存 | セレクタを手動更新 |
| CAPTCHA 自動回答不可 | 技術的制限 + 利用規約 | 手動で解く |
| ログイン自動化不可 | セキュリティ制約 | persistent context で初回のみ手動 |
| 大きい PDF で遅い | Claude の処理時間 | タイムアウト値を延長 |
| レート制限 | Claude の利用制限 | 時間を置いて再試行 |
