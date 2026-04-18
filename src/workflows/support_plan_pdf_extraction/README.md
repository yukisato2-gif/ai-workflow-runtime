# 個別支援計画関連書類 PDF 抽出 Workflow (PoC)

運営監査課向け。

## 処理方式 (重要)

本 workflow は **Claude API を使わず、ローカル同期済みの PDF を Claude Web UI に
Playwright 経由で添付アップロードして読ませる方式** である。

```
[Google Drive (ローカル同期フォルダ)]
   ↓ list_pdfs (ファイルシステム列挙)
[ローカル PDF ファイル]
   ↓ Playwright で CDP 接続した Chrome の Claude チャット画面へ添付
[Claude Web UI の応答テキスト]
   ↓ JSON パース + 正規化
[Google Sheets]
```

- Drive API から直接 PDF を読むわけではない
- Claude API も使わない
- 添付アップロードの成否は Claude.ai の UI 変更・画面状態・セッション状態に依存する
- 失敗時のエラー「PDF アップロードに失敗しました」は **Claude ブラウザへの添付失敗** を意味する

書類種別判定 → Claude ブラウザ添付・抽出 → 正規化 → Google Sheets 追記、までを行う。

| 項目 | 値 |
|------|---|
| WF ID | WF-004 |
| Owner | 運営監査課 |
| Skill ref | `cowork-assets/20_部署スキル/運営監査課_operations-audit/個別支援計画抽出_pdf-extraction-support-plan` |
| Status | PoC (最小実装) |

## 対象書類

1. アセスメント (`assessment`)
2. 個別支援計画書案 (`plan_draft`)
3. 担当者会議録 (`meeting_record`)
4. 個別支援計画書本案 (`plan_final`)
5. モニタリング (`monitoring`)

判定不能は `unknown` として `review_required=true` でシートに記録する。

## 前提

### ブラウザ自動化の前提
- 既存の `browser-pdf-test/run_test.py` (CDP 接続方式) を利用
- Chrome を `--remote-debugging-port=9222` 付きで起動済み
- その Chrome で claude.ai にログイン済み
- `BROWSER_PDF_TEST_DIR` など既存 workflow の環境変数設定を流用

### 環境変数

| 環境変数 | 必須 | 用途 |
|---|---|---|
| `SUPPORT_PLAN_INPUT_DIR` | ○ | 対象 PDF 格納フォルダ (再帰走査) |
| `SUPPORT_PLAN_SHEET_ID` | ○ | 追記先スプレッドシート ID |
| `SUPPORT_PLAN_SHEET_NAME` | — | シート名 (既定: `OCR_個別支援計画関連`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | ○ | サービスアカウント JSON のパス |
| `COWORK_ASSETS_DIR` | — | cowork-assets リポのルート (既定: 兄弟ディレクトリ) |
| `SUPPORT_PLAN_PROMPTS_DIR` | — | プロンプトディレクトリを直接指定する場合 |

## 実行方法

```bash
cd ai-workflow-runtime

# 必要な環境変数を設定 (例: 平塚の対象フォルダ)
export SUPPORT_PLAN_INPUT_DIR="/path/to/hiratsuka/032_個別支援計画関連PDF格納フォルダ"
export SUPPORT_PLAN_SHEET_ID="<スプレッドシートID>"
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/sa.json"

# Chrome を CDP モードで起動しておくこと (別ターミナル)
# open -na "Google Chrome" --args --remote-debugging-port=9222 ...

# 実行
python scripts/run_support_plan.py
```

## 処理フロー

```
[対象フォルダ]
   ↓ list_pdfs
[PDF 一覧]
   ↓ StateStore で処理済み除外
[未処理 PDF] ─(1件ずつ)─┐
                        ↓ classify
                   [書類種別]
                        ↓ load_prompt
                   [プロンプト]
                        ↓ run_claude_on_pdf
                   [応答テキスト]
                        ↓ parse_claude_response
                   [raw JSON]
                        ↓ normalize
                   [正規化済み dict]
                        ↓ append_row
                   [Sheets 1行追記]
                        ↓
                   [StateStore に記録]
```

## ファイル構成

| ファイル | 役割 |
|---|---|
| `workflow.py` | オーケストレーション |
| `drive_scanner.py` | 対象フォルダから PDF 列挙 |
| `classifier.py` | ファイル名で書類種別を判定 |
| `claude_runner.py` | `browser_reader.read_pdf_via_browser` を呼ぶ薄いラッパ |
| `extractor.py` | プロンプト選択 + JSON パース |
| `normalizer.py` | 日付・計画期間・参加者・○×正規化 |
| `sheets_writer.py` | Google Sheets 追記 (本 workflow 専用) |
| `state_store.py` | 処理済み管理 (JSON) |

## 注意

- 本ワークフローは PoC。帳票フォーマットの揺れで抽出が失敗することがある
- 失敗時は `review_required=true` でシートに記録され、次回再実行時に再処理される (処理済みにはしない)
- 成功した PDF は `output/support_plan_state.json` に記録され、再実行時にスキップされる
- 既存の WF-001 / WF-002 / WF-003 には影響しない
