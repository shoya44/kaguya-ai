# かぐやAI — Codex引き継ぎメモ

更新日：2026-09-11

## 現在地

仕様検討は完了。実装は未着手。正本は `kaguya_ai_final_spec_and_quickstart.md`。

## 採用方針

- Windows PCに住む常駐キャラクター。Tauri 2 + TypeScript + Canvas2D。
- 本体はPython / FastAPI、記憶はPostgreSQL。
- iPhoneは後段で、自宅Wi-Fi内からHTTPS接続。PC停止中は利用不可。
- LLMは個人用Gemini API無料枠。課金先・会社契約・別モデルへ自動切替しない。
- 音声は後段。録音ボタン → Whisper → Gemini → VOICEVOX。
- まず文字会話を完成させ、Tauri、記憶、自発的声かけ、音声、iPhoneの順に追加。

## キャラクター

普段は明るく無邪気で、子供っぽく少しワガママ。親しい軽口は言うが、重い相談は茶化さず親身に聞く。一人称は「あたし」。返答を催促したり、未起動や無視で罪悪感を与えたりしない。

## 次に実施する作業

1. 個人用Google AI Studioで、課金未接続のAPIキーを用意する。
2. 候補モデルで、日本語の雑談・相談・JSON形式の要約が通るか確認する。
3. `kaguya-ai` プロジェクトを作成し、FastAPIの `/health` とGemini接続を実装する。
4. PostgreSQLへ3テーブルを作成し、文字会話1往復の保存・再取得を通す。
5. Viteの最小UIからWebSocketで送受信する。

APIキーはチャットやリポジトリへ貼らず、Windows側の `.env` に設定する。

## 最初の完了条件

ブラウザで文章を送ると、かぐやの口調で返答し、ユーザー発言と回答が重複せずDBへ保存される。再起動後も履歴を表示できる。429・通信失敗・保存失敗を通常会話と区別して表示できる。
