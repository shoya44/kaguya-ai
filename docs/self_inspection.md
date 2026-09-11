# かぐやAI 自己参照機能

かぐやとの通常チャットから、かぐやAI自身のREADME・docs・ソースコードを読み取り専用で確認できるようにする機能です。

## できること

- 自身の仕様や画面動作の確認
- 関数名・画面文言・設定名から関連コードを検索
- 検索結果の前後コードを確認して、不具合原因の候補を説明
- Gitブランチと未確定変更の有無を確認

## 安全境界

この機能からプロジェクトファイルを書き換えることはありません。`.env`、`.venv`、`node_modules`、`target`、`dist`など、秘密情報・依存物・生成物は検索対象外です。

バグ修正を頼まれた場合、かぐやは確認できたコードを根拠に「原因候補・対象ファイル・修正案・確認項目」を回答します。実際のコード変更は、内容をレビューした上でGitHub PRや開発ツール側から適用します。

## 実装

`backend/app/project_inspector.py` が読み取り専用の `project_status` / `project_search` / `project_read` を提供し、`backend/app/tools.py` が明示的に登録します。

旧仕様・引継書の `kaguya_ai_codex_handoff.md`、`kaguya_ai_handoff_v2.md`、`kaguya_ai_final_spec_and_quickstart.md` は通常検索から除外します。履歴資料として残し、パスを指定した読み取りは可能です。現行仕様はREADMEと実装を優先します。`.test-output` も検索・読み取り対象外です。
