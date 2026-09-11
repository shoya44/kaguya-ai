# Quick Win 機能

通常の雑談ではFunction Callingの宣言自体をGeminiへ送らず、関連語がある発言だけ必要な道具を有効化します。

## 天気

- 例: `今日の天気どう？` `明日傘いる？`
- Open-Meteoを利用。APIキー不要。
- 地点解決は24時間、天気結果は30分キャッシュ。
- 既定地点は `東京`。会話で `天気の場所を横浜にして` のように変更できます。

## 会話から設定変更

対象: `quiet`, `proactive_minutes`, `always_on_top`, `font_size`, `weather_location`

例: `静かにして` `文字サイズを16にして` `最前面をオフにして`

## ローカルカレンダー

- 保存先: `%LOCALAPPDATA%\KaguyaAI\calendar.json`
- 例: `明日15時に歯医者の予定を入れて` `今週の予定を教えて`
- PostgreSQLの記憶DBとは分離し、DBマイグレーション不要。

## 参照ファイル

- 保存先: `%LOCALAPPDATA%\KaguyaAI\references`
- 対応: `.md`, `.txt`, `.json`, `.csv`
- 1ファイル200KBまで。検索結果は最大8箇所。
- 例: `参照資料から勤怠の締め日を探して`

フォルダは初回参照時に自動作成されます。ファイルはユーザーが通常のExplorer等から追加します。

## 時刻別キャラクター

既存の `thinking / talking / greeting / sleeping / organizing` はそのまま維持し、`idle` のみローカル時刻で既存PNGを切り替えます。LLM/API呼び出しは発生しません。
