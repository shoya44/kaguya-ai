# かぐやAI

Windows PCを母艦にして、PCまたは同じ家庭内Wi-FiのiPhoneから使う個人用AIキャラクターです。
日常の雑談・相談を中心に、長期記憶、リマインダー、簡易カレンダー、天気、参照ファイル検索、自分自身の仕様確認、軽い生活・感情演出を備えています。

安定性と低トークン消費を優先しています。通常の雑談では不要なFunction Calling定義をGeminiへ送りません。天気のようにローカルで意図を確定できる処理はGeminiを経由しません。

## 現在の主な機能

- Geminiによる文字チャット
- PostgreSQLへの会話保存
- 「会話原文 → 知恵・好み → 接し方」の長期記憶
- 「これ覚えておいて」による即時記憶
- 指定時刻のリマインダー
- リマインダーは吹き出しをクリックして確認するまで保持（複数件は順に表示、次の通知は最大約30秒後）
- ローカルカレンダー
- Open-Meteoによる天気取得
- `%LOCALAPPDATA%\KaguyaAI\references` の参照ファイル検索
- README / docs / ソースコードの読み取り専用自己参照
- 通常チャットのストリーミング表示（書き終わるのを待たずに読み始められる）
- 「接続中」「回答を生成中」「保存中」の進捗表示と、常設の接続状態表示
- 返答のあとに出る定型の追いかけボタン（「もっと詳しく」など。候補生成のためのLLM呼び出しはしない）
- 書きかけの端末内保存（送信が受理されると消える）
- PC版の通常表示 / 簡易表示
- PC版の「最新版を反映」
- 同一LAN内のiPhoneブラウザからの利用
- Living Kaguya（生活状態、軽い感情、低負荷モーション、利用時間帯の学習）

アプリ画面の「使い方」は `frontend/public/manual.html` です。READMEと同じく現行仕様へ更新します。

---

# 安定性優先の動作

## 会話を最優先

記憶整理中にユーザーが話しかけた場合は、整理処理をキャンセルして通常チャットを優先します。未処理の会話原文は削除しません。

Gemini APIは以下の方針です。

- 通常チャットは低Thinking設定
- 記憶整理も低Thinking設定
- Geminiの一時的な500/502/503/504、ネットワークエラーだけ短く1回再試行
- 429（利用制限）は自動で繰り返さず、ユーザーへ返す
- 長いタイムアウト後の自動再試行はしない

## 記憶整理

自動整理をONにしている場合、**未整理の会話があり、かつ会話が5分以上途切れているとき**に、少量ずつ整理します。

- 確認間隔は15分。整理は1回につき1バッチだけ
- 未整理の会話が無いときはGemini APIを呼びません（DBの件数を数えるだけ）
- 1日のAPI上限（設定値）を超えたら、その日は打ち切り
- 会話が始まったら整理を中断し、未処理の原文は保持

会話していない時間に少しずつ進めるので、深夜まで待たずに知恵へ反映されます。逆に、
会話中や連続操作中にバックグラウンドのGemini呼び出しが割り込むことはありません。

「設定 → 今すぐ整理」は手動でいつでも実行できます。整理失敗時は、分かる範囲で利用制限・タイムアウト・結果形式不正・保存失敗を区別して表示します。

## 天気

「今日の天気は？」「傘いる？」「足立区の明日の天気」のような明示的な天気質問は、Geminiを呼ばずOpen-Meteoへ直接問い合わせます。

- APIキー不要
- 30分キャッシュ
- 外部API障害時は直近6時間以内の成功結果をフォールバック可能
- 「今日は寒いね」のような雑談だけでは強制的に天気APIへ送らない

既定地点は東京です。会話で「天気の場所を横浜にして」のように変更できます。

## 更新

通常の `start.bat` は**Gitを変更しません**。
最新版を取り込むのは、PC版の「最新版を反映」を明示的に押したときだけです。

更新時は外部PowerShellヘルパーが以下を行います。

1. 現在のかぐやAI終了を待つ
2. `update_repo.bat` で `origin/main` を `--ff-only` 更新
3. ビルド
4. かぐやAIを再起動

オフライン・main以外・ローカル変更あり等で更新できない場合でも、現在版の再起動を試みます。
ログは `%LOCALAPPDATA%\KaguyaAI\update.log` です。

---

# セットアップ

## 必要な環境

| ソフト | 用途 |
|---|---|
| Python 3.11以降 | FastAPI backend |
| Node.js + npm | frontend build |
| Rust stable + MSVC Build Tools + Windows SDK | Tauri build |
| WebView2 Runtime | PC画面 |
| PostgreSQL | 会話・長期記憶 |
| Git | 「最新版を反映」 |

Python 3.13系でも、requirementsが正常にインストールできれば利用できます。

## PostgreSQL

専用DB/ロールを用意します。例：

```sql
CREATE ROLE kaguya WITH LOGIN PASSWORD '<任意のパスワード>';
CREATE DATABASE kaguya_ai OWNER kaguya;
```

## backend

```powershell
cd C:\kaguya-ai\backend
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

`backend/.env` を編集します。

```ini
GEMINI_API_KEY=...
GEMINI_MODEL=利用するGeminiモデル名
DATABASE_URL=postgresql://kaguya:<password>@127.0.0.1:5432/kaguya_ai
```

秘密情報はGitへコミットしないでください。

## frontend

```powershell
cd C:\kaguya-ai\frontend
npm install
```

## 起動

```bat
C:\kaguya-ai\start.bat
```

起動時にDBマイグレーションとローカルビルドを行います。パッケージの自動インストールはしません。

---

# 基本操作

## 画面

初期画面は「会話」です。「記憶」「設定」は補助画面で、「使い方」は設定画面の中にあります。

## 会話

- PC: Enterで送信、Shift+Enterで改行
- iPhone等のタッチ端末: **改行キーは改行**。送信は送信ボタンだけ
- 入力中はキャラクターが小さな顔表示に縮まり、そのぶん会話履歴が広がります
- 書きかけは端末内に保存され、送信が受理された時点で消えます（送信前にタブが閉じても戻ります）
- 過去の会話を読んでいる間は自動で最下部へ移動しません。「新しい返答 ↓」で戻ります
- 返答は届いた順に表示されます（ストリーミング）
- 返答のあとに「もっと詳しく」「例をあげて」「短くまとめて」が出ます。押すと普通の会話として送るだけです
- 最大2,000文字
- 同時生成は1件
- 失敗した会話は「再送する」
- 回答生成済みで保存だけ失敗した場合は「保存のみ再試行」

## 進捗と接続状態

画面上部に接続状態（接続中… / 接続済み / 未接続）を常設し、生成中は「接続中」「回答を生成中」「保存中」を出します。
実際には検索していないのに「記憶を探しているよ」のような演出は出しません。

## 終了

- タスクトレイ → 終了
- `stop.bat`

ウィンドウの×はアプリ終了ではなく非表示です。

## 簡易表示

PC版では「簡易表示に切り替え」でキャラクター中心の小型表示へ切り替えられます。
通常表示への復帰はダブルクリックまたは右クリックメニューです。

---

# iPhoneから使う

PCとかぐやAIを起動した状態で、iPhoneを同じWi-Fiへ接続します。

```text
http://<PCのLAN IP>:8765/
```

iPhoneはPC側と同じFastAPI・Gemini・PostgreSQLを使います。
別のiPhoneアプリや別DBではありません。

利用可能：

- 通常チャット（ストリーミング表示）
- 記憶
- 天気
- カレンダー
- リマインダー（画面を開いている間）
- 参照ファイル
- 自分自身の仕様確認
- Living Kaguya

制約：

- PCが終了/スリープ中は使えない
- HTTPS未実装
- Web Push未実装
- PC更新ボタン・最前面設定はiPhoneには出さない
- ホーム画面へ追加した場合もSafariのタブとして開きます。マニフェストの `display` は
  `browser` のままです。iOSのスタンドアロン起動はHTTPS必須で、HTTP接続の本アプリでは
  「HTTPS-Onlyが有効なHTTP URL」エラーになり起動できないためです

Living Kaguyaの利用時間学習・感情状態は端末localStorageなので、PCとiPhoneで別々に保持されます。

---

# Living Kaguya

追加のLLM/API呼び出しを使わず、キャラクターがアプリ内で生活しているように見せる軽量レイヤーです。

状態例：

- idle
- reading
- working
- playing
- snacking
- daydreaming
- sleeping

感情例：

- normal
- happy
- sleepy
- sulky

褒められる、深夜になる、特定の会話をする等で軽く変化します。よく会話する時間帯も端末内で少しずつ学習します。

既存PNGにはCanvasの低負荷な上下・呼吸・傾きモーションを付けています。専用の `snack.png` や `sulky.png` 等は将来追加可能ですが、未配置ファイルは参照しません。

---

# Function Calling / ローカル機能

## リマインダー

例：

```text
明日9時に薬って教えて
30分後に声かけて
```

## カレンダー

例：

```text
明日15時に歯医者の予定を入れて
今週の予定を教えて
```

保存先：

```text
%LOCALAPPDATA%\KaguyaAI\calendar.json
```

## 設定変更

例：

```text
静かにして
文字サイズを16にして
天気の場所を横浜にして
```

## 参照ファイル

以下へファイルを置きます。

```text
%LOCALAPPDATA%\KaguyaAI\references
```

対象：`.md`, `.txt`, `.json`, `.csv`

例：

```text
参照資料から勤怠の締め日を探して
```

## 自分自身の仕様確認

読み取り専用でREADME/docs/ソースを確認できます。

```text
自分の簡易表示の仕様をソースから調べて
READMEを確認してiPhone対応を教えて
```

`.git`, `.venv`, `node_modules`, `target`, `dist` 等は走査しません。
ソースコードを書き換えるFunction Callingはありません。

---

# 記憶

## 3層

| 層 | 画面上の呼び方 | 役割 | できる操作 |
|---|---|---|---|
| `raw_memory` | 会話履歴 | 過去のやり取りを読み返す | 訂正 / 削除 |
| `wisdom` | あなたについて覚えていること | 好み・習慣・続いている事情 | 訂正 / もう当てはまらない |
| `persona` | かぐやの接し方 | 呼び方、返答の長さ、相談時の接し方 | 変更 / 元に戻す |

知恵の蓄積（`wisdom`）と会話履歴の長期保存（`raw_memory`）は別に設計しています。
整理済みで7日を過ぎた会話原文は削除されますが、そこから作られた知恵は残ります。

「記憶」タブの先頭には、いま何件が長期記憶への反映待ちかと、最近覚えた・更新した内容を出します。
かぐやが返答で実際に参照した記憶は、その返答の下の「参照した記憶」から確認できます。

## 即時記憶

```text
これ覚えておいて
```

と明示すると、その場で知恵へ保存します。

## 検索

記憶の呼び出しは、全角・半角と大文字・小文字を揃えてから部分一致で探します。
いまの発言に一致した記憶を、直前の話題にだけ一致した記憶より優先します（直前の話題に引きずられないため）。
「今回だけ詳しく」のような一度きりの指示は、長期的な好みとしては保存しません。

## 整理

「設定 → 今すぐ整理」で未処理の会話から長期的な情報を抽出します。

- Geminiへ送るのは入力上限内の未処理会話
- 結果はPydantic schemaで検証
- 根拠IDを検証してからDBへ保存
- 失敗時は未処理原文を保持
- 会話が始まったら整理を中断
- 1日のAPI上限あり

接し方（`persona`）の自動更新は従来どおり慎重で、週次かつ「明示された知恵」が
3日以上の別々の日で確認できた場合だけ候補にします。手動で変更・保護した項目は上書きしません。

自動整理の実行タイミングは「安定性優先の動作 → 記憶整理」を参照してください。

---

# 保存場所

| データ | 保存先 |
|---|---|
| 会話/知恵/接し方/リマインダー | PostgreSQL |
| 設定/整理回数 | `%LOCALAPPDATA%\KaguyaAI\settings.json` |
| ローカル予定 | `%LOCALAPPDATA%\KaguyaAI\calendar.json` |
| 参照資料 | `%LOCALAPPDATA%\KaguyaAI\references` |
| Living状態 | 各ブラウザ/WebViewのlocalStorage |
| 更新ログ | `%LOCALAPPDATA%\KaguyaAI\update.log` |

---

# アーキテクチャ

```text
PC / iPhone Web UI
        |
   HTTP / WebSocket
        |
      FastAPI
   /      |       \
Gemini  PostgreSQL  Local tools
                   |- weather (Open-Meteo)
                   |- calendar.json
                   |- references/
                   |- project inspector

PCのみ: Tauri
 |- tray / mini window
 |- backend child process
 |- explicit repository updater
```

主要技術：

- Python / FastAPI
- google-genai
- PostgreSQL / psycopg
- TypeScript / Vite
- Tauri / Rust
- Open-Meteo

---

# テスト

リポジトリ直下：

```bat
check.bat
```

実行内容：

1. backendの全 `unittest`（`backend/tests/test_iphone_memory_stream.py` を含む）
2. `frontend/tests/main.test.cjs`
3. `frontend/tests/living.test.cjs`
4. `frontend/tests/avatar.test.cjs`
5. TypeScript compile / Vite build
6. Cargo check（オフライン）

ライブGemini、ライブ天気API、本番DBへの呼び出しは行いません。

実DBの確認用コードは `backend/tests/run_db_checks.py` にあります。必要な場合だけ、対象DBを確認して実行してください。

---

# 更新手順

通常はPC版の「最新版を反映」を使用します。

手動で更新する場合：

```bat
cd C:\kaguya-ai
stop.bat
git switch main
git pull --ff-only origin main
check.bat
start.bat
```

`start.bat` 単体では `git pull` しません。

---

# トラブルシュート

## Gemini利用制限

429は自動連打しません。表示された待ち時間を目安に再送してください。

## タイムアウト

長いタイムアウト後の自動再試行はしません。手動で再送してください。

## 記憶整理失敗

「設定」の整理ステータスを確認してください。未処理原文は保持されます。

## 天気失敗

インターネット接続と地点名を確認してください。直近6時間以内の成功キャッシュがあれば自動利用します。

## 更新後に再起動しない

```text
%LOCALAPPDATA%\KaguyaAI\update.log
```

を確認してください。現在版は `start.bat` から手動起動できます。

## ポート8765

別のかぐやAI/uvicorn/開発サーバーが8765を使っていないか確認してください。

---

# 未実装・制約

- iPhone接続のHTTPS化
- Web Push
- 音声入出力
- 配布用インストーラー
- LLMによる自己ソース書き換え
- 本格的な感情モデル / Fine-tuning

Living Kaguyaは「本当に感情がある」ことを主張する機能ではなく、キャラクターとして自然に見えるためのローカル演出です。
