# かぐやAI — 実装引き継ぎ仕様書

版：2.0（実装反映版） / 更新日：2026-09-11 / 対応する正本：`kaguya_ai_final_spec_and_quickstart.md`（v1.0）、`kaguya_ai_codex_handoff.md`

本書は、正本仕様書に基づいて実装した内容を、コードの実態に合わせて記録したものです。初見の技術者・AIがこのプロジェクトを引き継いで作業を再開できることを目的とします。正本仕様書の「決定事項」「性格」「音声仕様」など未実装の章はそのまま正としており、本書では上書きしません。実装済みの範囲（工程0〜3）についてのみ、実際の設計・コード・既知の問題を記載します。

---

## 1. 現在地（一言で）

**工程0〜3が完了**しています。Windows PC上でTauriデスクトップアプリとして常駐し、キャラクター画像を表示、テキストチャットでGemini APIと会話でき、会話はPostgreSQLに保存され、アプリを再起動しても履歴が復元されます。工程4（記憶の知恵化・自発的な声かけ）以降は未着手です。

正本仕様書の最短実装手順（0〜7）との対応:

| 工程 | 内容 | 状態 |
|---|---|---|
| 0 | 無料APIの動作確認 | 完了 |
| 1 | 開発環境準備 | 完了 |
| 2 | 文字会話（1往復の完成） | 完了 |
| 3 | Tauriで常駐化 | 完了 |
| 4 | 記憶の知恵化・自発的な声かけ | 未着手 |
| 5 | ボタン式音声（Whisper/VOICEVOX） | 未着手 |
| 6 | iPhone接続（HTTPS/mkcert/Caddy） | 未着手 |
| 7 | 普段使い向け仕上げ | 未着手 |

---

## 2. アーキテクチャ

### 2.1 全体構成図

```mermaid
flowchart TD
  subgraph Windows PC
    W["Tauriデスクトップアプリ<br/>(常駐ウィンドウ)"]
    F["フロントエンド<br/>TypeScript + Vite + Canvas2D"]
    B["Controller<br/>Python + FastAPI (Uvicorn, 1 worker)"]
    D[("PostgreSQL<br/>raw_memory")]
    W --> F
    F <-->|HTTP / WebSocket<br/>127.0.0.1:8765| B
    B <-->|内部HTTP<br/>x-internal-token| B
    B <-->|psycopg| D
  end
  B <-->|HTTPS| L["Gemini API<br/>(google-genai, 無料枠)"]
```

- iPhone接続・音声（Whisper/VOICEVOX）・自発的な声かけ・記憶の知恵化(wisdom/persona)は**未実装**。図には現在動いている経路のみ記載しています。
- Windows外枠(Tauri)とWebフロントエンドのコードは同一で、iPhone用にもそのまま流用する設計（正本仕様書 3章）を踏襲していますが、iPhoneからの接続自体は未実装です。

### 2.2 プロセス構成

1. **Tauri本体プロセス**（`frontend/src-tauri`、Rustバイナリ）
   - アプリ起動時に`backend/.venv`のPythonでuvicornを子プロセスとして起動する。
   - ウィンドウを閉じても常駐（`hide`のみ、プロセスは終了しない）。
   - タスクトレイに常駐し、「表示」「終了」の2項目のみ持つ。「終了」を選ぶとbackend子プロセスをkillしてからアプリ自体も終了する。
   - 単一起動制御（`tauri-plugin-single-instance`）を持ち、二重起動時は既存ウィンドウを前面表示する。

2. **Webビュー（Tauriウィンドウ内）**
   - Vite+TypeScriptのSPA。`http://127.0.0.1:8765`のbackendに直接fetch/WebSocket接続する。
   - 起動直後はbackendの起動待ちのため、`/health`エンドポイントへ最大20秒間・500ms間隔でリトライする（詳細は5章）。

3. **backend（FastAPI, Uvicorn, 127.0.0.1:8765固定）**
   - 会話の受付・Gemini呼び出し・DB保存を1プロセス内で完結。
   - 記憶API（`/internal/memory/*`）は同一プロセス内の別ルーターだが、内部トークン(`x-internal-token`)で保護されており、外部（ブラウザ側のセッショントークン）からは呼べない。正本仕様書10章の「記憶の内部APIは通さない」設計をこの内部トークンで実現している。

4. **PostgreSQL**（Windowsサービスとして常時起動、127.0.0.1:5433）
   - `kaguya_ai`データベース、`kaguya`ロールを使用。

---

## 3. 技術スタック

### フロントエンド（`frontend/`）

| 項目 | 採用技術 | バージョン指定 |
|---|---|---|
| 言語 | TypeScript | ^5.6.0 |
| ビルドツール | Vite | ^6.0.0 |
| 描画 | Canvas2D（UIフレームワーク不使用、正本仕様書5章の方針通り） | - |
| デスクトップ外枠 | Tauri | 2系（`@tauri-apps/api` ^2.11.1, `@tauri-apps/cli` ^2.11.4） |

Rust側（`frontend/src-tauri/Cargo.toml`）:

| クレート | 用途 |
|---|---|
| `tauri` (2.11.3, feature: tray-icon) | アプリ本体、トレイアイコン |
| `tauri-plugin-log` | デバッグビルド時のログ出力 |
| `tauri-plugin-single-instance` | 二重起動防止 |
| `serde` / `serde_json` | Tauri内部で使用 |
| `log` | ログマクロ |

> 補足：以前は`reqwest`/`tokio`もRust側の依存に含めていましたが、backendの起動待ち処理をフロントエンド（TypeScript）側の`/health`リトライ方式に変更したことに伴い、Rust側では未使用となったため削除済みです。

### バックエンド（`backend/`）

| 項目 | 採用技術 | バージョン指定（`requirements.txt`） |
|---|---|---|
| 言語 | Python | 3.13（開発機の実測。要件は3.11系〜） |
| Webフレームワーク | FastAPI | >=0.115,<1 |
| ASGIサーバー | Uvicorn（standard extras） | >=0.34,<1 |
| LLM SDK | google-genai | >=1.30,<2 |
| DBドライバ | psycopg（binary extras） | >=3.2,<4 |
| 内部HTTPクライアント | httpx | >=0.28,<1 |
| 設定管理 | pydantic-settings | >=2.8,<3 |

### データベース

- PostgreSQL（Windowsサービス、ポート5433で稼働 — デフォルトの5432ではなくローカル環境の都合で5433を使用）
- マイグレーションはSQLファイルの番号管理（`backend/migrations/001_init.sql`）。ORM・マイグレーションツールは未導入（正本仕様書8章の方針通り）。

### 外部サービス

- Google Gemini API（`google-genai`経由）。使用モデルは`GEMINI_MODEL`環境変数で指定し、コードにハードコードしない（正本仕様書7章の方針）。
- 現在確定しているモデル: `gemini-3.5-flash-lite`（実機での日本語会話・構造化要約テストに成功したため採用）。

---

## 4. フロントエンド／バックエンド／DB設計

### 4.1 フロントエンド構成

```
frontend/
├── index.html            # レイアウト（キャラ表示エリア + チャットパネル、常時両方表示）
├── src/
│   ├── main.ts           # セッション管理・履歴取得・WebSocket送受信・エラー表示
│   ├── avatar.ts          # キャラクター画像の状態管理・描画（Canvas2D）
│   ├── tauri.ts           # isTauri()判定のみ（Tauri環境かプレーンブラウザかの分岐用）
│   ├── style.css
│   └── vite-env.d.ts
├── public/
│   └── sprites/           # キャラクター画像9種類（img.pngを3x3分割）
├── src-tauri/              # Tauri本体（Rust）
│   ├── src/lib.rs          # backend起動・トレイ・ウィンドウ制御
│   ├── src/main.rs         # エントリポイント（標準生成のまま）
│   ├── tauri.conf.json     # ウィンドウ設定
│   ├── Cargo.toml
│   └── capabilities/default.json  # Tauriパーミッション定義
├── package.json
└── vite.config.ts
```

**画面構成**: 正本仕様書3章にある「透明・枠なしの小窓 → クリックで会話パネルを開く」という2段階UIは、Tauri実装時に安定動作しなかったため、**通常の枠付きウィンドウ（420×640、リサイズ可）にキャラクター表示エリアと会話パネルを常時両方表示する**シンプルな構成に変更しています（6章の経緯を参照）。

**キャラクター画像**: `img.png`（1254×1254、3×3グリッド、9パターンの立ち絵）を分割し、`public/sprites/*.png`として配置。`avatar.ts`が状態に応じて表示する画像を切り替えます。

| 状態 | 表示 |
|---|---|
| `idle`（待機中） | `wave`/`talk`/`laugh`/`laptop`/`cards`/`book`の6枚から20秒ごとにランダム選択 |
| `thinking`（送信〜返答待ち） | `write`（集中して書いている絵）固定 |

`sleep`・`think`の2枚は現時点で未使用（工程4の自発的な声かけ・長時間アイドル時の演出用に転用を想定）。

### 4.2 バックエンド構成

```
backend/
├── app/
│   ├── main.py         # FastAPIエントリポイント。/health, /session, /history, /ws
│   ├── controller.py   # 会話ターンの状態管理（1件のみ同時実行）
│   ├── llm.py          # Gemini呼び出しのラッパー（会話・要約）
│   ├── memory_api.py   # 内部APIルーター(/internal/memory/*) + MemoryClient
│   ├── models.py       # Pydanticモデル（Turn, Completion, Failure）
│   ├── persona.py      # システムプロンプト・会話文脈の組み立て
│   ├── errors.py       # ChatError例外クラス
│   └── config.py       # 環境変数ベースの設定（Settings）
├── migrations/
│   └── 001_init.sql    # 3テーブルの初期スキーマ
├── requirements.txt
├── .env                # 実際のAPIキー・DB接続情報（Git管理外）
└── .env.example        # プレースホルダーのみ
```

**内部認証の仕組み**: `main.py`の`lifespan`で起動時にランダムトークン（`secrets.token_urlsafe(32)`）を生成し、同一プロセス内のhttpxクライアントにのみ付与します。`memory_api.py`側は`x-internal-token`ヘッダを検証する`internal_auth`依存性を全エンドポイントに強制しており、ブラウザ側のセッショントークンでは記憶APIを直接呼べません（正本仕様書5章・10章の要件を満たす設計）。

### 4.3 データベース設計（ER図）

```mermaid
erDiagram
    raw_memory {
        uuid id PK
        uuid turn_id
        text role "user | assistant"
        text content
        timestamptz created_at
        timestamptz processed_at "NULL可"
        text status "pending | completed | failed | cancelled"
        uuid origin_client_id
        text input_mode "text | voice"
    }
    wisdom {
        uuid id PK
        text topic_key UK
        text summary
        text kind "explicit | inferred"
        text support_level "unconfirmed | stated | repeated"
        integer importance "1-5"
        jsonb evidence
        timestamptz last_seen_at
        timestamptz last_used_at
        integer revision
        timestamptz updated_at
    }
    persona {
        text key PK
        jsonb value
        boolean locked
        jsonb source_wisdom_ids
        integer revision
        timestamptz updated_at
        jsonb previous_value
    }
```

**実装状況**:
- `raw_memory`テーブルのみ実際に読み書きされています（`001_init.sql`でテーブル自体は3つとも作成済み）。
- `wisdom`・`persona`は正本仕様書8章の設計通りスキーマは存在しますが、読み書きするコード（工程4の知恵化ジョブ、人格更新処理）は未実装です。
- `raw_memory`は`UNIQUE(turn_id, role)`制約により1往復（ユーザー発言1件＋AI回答1件）を保証。`CHECK (role <> 'assistant' OR status = 'completed')`でAI側レコードは常にcompleted状態でのみ存在できるよう制約しています。

### 4.4 APIエンドポイント一覧（実装済み）

| メソッド・経路 | 認証 | 用途 |
|---|---|---|
| `GET /health` | なし | 起動確認（フロントエンドの起動待ちポーリングにも使用） |
| `POST /session` | なし | セッショントークン発行（`client_id`の割り当て） |
| `GET /history` | Bearerトークン | 会話履歴取得（カーソルページング、`limit`最大50） |
| `WS /ws?token=...` | クエリのtoken + Origin検証 | リアルタイム会話（下記イベント参照） |
| `POST /internal/memory/*` 系 | `x-internal-token`（内部専用） | raw_memoryの読み書き。外部からは直接呼べない |

正本仕様書6章に記載の`/audio/transcribe`、`/audio/{turn_id}`、`/settings`、`/memories/{layer}/{id}`は**未実装**（音声・記憶編集・声かけ設定機能自体が未着手のため）。

**WebSocketイベント（実装済み）**:

受信（クライアント→サーバー）:
- `chat.send`（`turn_id`, `text`, `retry`）
- `chat.cancel`（`turn_id`）
- `chat.retry_save`（`turn_id`）
- `playback.ended`（受け取るが何もしない。音声未実装のためプレースホルダー）

送信（サーバー→クライアント、全接続へbroadcast）:
- `state.changed`（`state`: idle/thinking）
- `chat.accepted`（ユーザー発言の保存完了通知）
- `chat.completed`（`turn_id`, `text`, `answer`）
- `chat.error`（`code`, `message`, `retry_after`など）

正本仕様書6章にある`speech.ready`・`proactive.message`は音声・自発声かけ未実装のため送出されません。

---

## 5. 現在の仕様（実際の挙動）

### 5.1 会話の流れ

1. フロントエンドがUUID（`turn_id`）を発行し、`chat.send`をWebSocketで送信する。
2. `Controller.send()`が同時実行を1件に制限（既に処理中なら`busy`エラーを返す）。
3. `MemoryClient.begin()`が`raw_memory`にユーザー発言を`pending`状態で保存（同じ`turn_id`が既にあれば内容一致を検証し、完了済みならその回答をそのまま返す＝再送の冪等性を確保）。
4. 直近の会話履歴（完了済み最新10往復）を`context()`で取得し、`persona.py`の`SYSTEM_PROMPT`と組み合わせてGeminiへ送信。
5. Gemini応答を`raw_memory`に`assistant`ロールで保存し、ユーザー側レコードを`completed`に更新。
6. `chat.completed`イベントを全接続にbroadcastし、フロントエンドが画面を更新。

**保存失敗時の扱い**（正本仕様書6章準拠）: Gemini応答は得られたがDB保存に失敗した場合、回答をメモリ上に保持（`self.unsaved`）し、「保存のみ再試行」ボタンをフロントエンドに表示。生成のやり直しは行わない。

### 5.2 Input/Output

- 入力: テキストのみ（最大2,000文字、`Turn`モデルでバリデーション）。音声入力は未実装。
- 出力: Gemini応答をそのまま表示（最大1,024トークン、`max_output_tokens`設定）。音声合成（TTS）は未実装。
- エラー表示: レート制限（429）、タイムアウト、通信失敗、内部エラーをそれぞれ日本語メッセージで表示し、自動再試行はしない（手動の再送ボタンのみ）。

### 5.3 DB記憶

- 会話は`raw_memory`に永続化。アプリ再起動後も`GET /history`で履歴を復元できることを確認済み。
- `wisdom`（知恵の抽出・要約）・`persona`（人格の動的更新）は未実装のため、現時点でかぐやは「直近10往復の会話文脈」以外の長期記憶を持ちません。
- 起動時に`recover()`を呼び、異常終了で残った`pending`状態のユーザー発言を`failed`に戻す処理を実装済み（正本仕様書6章「プロセス停止により生成中のまま残った発言は起動時に失敗状態へ戻す」に対応）。

### 5.4 LLM呼び出し

- `llm.py`の`Gemini`クラスが`google-genai`の非同期クライアントをラップ。
- システムプロンプト（`persona.py`）は固定文字列。人格の動的変更（正本仕様書2章の「persona」連動）は未実装。
- レート制限（429）発生時はレスポンスの`retryDelay`を抽出し、待機秒数をユーザーに提示（自動再試行はしない）。
- タイムアウトは30秒（`llm_timeout_seconds`設定）。

---

## 6. 試したこと・分かったこと（トラブルシュート履歴）

実装中に発生した問題と対処を、後続の作業者が同じ問題に当たった際に参照できるよう記録します。

### 6.1 モデル選定（工程0）

- **試したこと**: `gemini-2.5-flash-lite`など複数のモデル名をハードコードして呼び出したが、すべて404エラー。
- **分かったこと**: Gemini APIのモデル名は頻繁に変わる。エラーメッセージ自体に後継モデル名（`gemini-3.5-flash-lite`、`gemini-3.6-flash`）が明記されていたため、それを指定して解決。
- **結論**: `gemini-3.5-flash-lite`で日本語会話・構造化要約とも成功を確認し、採用確定。`-latest`のようなエイリアス名は中身が自動で差し替わり人格の一貫性を損なうリスクがあるため、具体的なバージョン名を使う方針とした（正本仕様書7章と整合）。
- 調査用に`backend/check_gemini.py`（利用可能モデルを列挙して自動試行）・`list_gemini_models.py`を作成したが、本体アプリからは参照されない一時スクリプトのため削除予定（7章参照）。

### 6.2 DB接続まわり（工程1〜2）

- **CORSエラー**: `http://localhost:5173`からアクセスすると`http://127.0.0.1:8765`宛てのfetchがCORSでブロックされた。原因はホスト名の不一致（`localhost`と`127.0.0.1`はブラウザ上は別オリジン扱い）。ブラウザで`127.0.0.1`側にアクセスし直すことで解決。
- **DATABASE_URLの書式ミス**: `.env`に`postgresql://kaguya:6334:5433/kaguya_ai`と記載しており、`@127.0.0.1`が欠落していた。`psycopg.OperationalError: failed to resolve host 'kaguya'`のエラーから特定し、`postgresql://kaguya:PASSWORD@127.0.0.1:5433/kaguya_ai`の形式に修正して解決。
- **PostgreSQLのスキーマ権限エラー**（PostgreSQL 15以降の仕様変更）: 「スキーマpublicへのアクセスが拒否されました」というエラーが発生。`GRANT ALL ON SCHEMA public TO kaguya;`を実行して解決。PostgreSQL 15から`public`スキーマへのデフォルト権限が変更されたことが原因。

### 6.3 ブラウザ拡張機能による干渉

- ブラウザの拡張機能が有効な状態で「履歴を取得できませんでした」というエラーが継続。拡張機能をオフにしたところ解決。原因の拡張機能は特定していないが、ローカルAPIへの通信を監視・ブロックする類の拡張機能が疑われる。

### 6.4 Rustのコンパイルエラー（工程3）

- **E0599（no method named `emit`）**: `AppHandle`に`emit`メソッドが見つからないエラー。`tauri::Emitter`トレイトがuseされていなかったことが原因。`use tauri::{AppHandle, Emitter, Manager, WindowEvent};`のようにトレイトを明示的にインポートして解決。
- **E0597（`state` does not live long enough）**: `MutexGuard`の一時変数がスコープを抜ける前に借用が終わらないという借用チェッカーのエラー。`if let Some(mut child) = state.0.lock().unwrap().take()`のようにワンライナーで書くと、`MutexGuard`の一時オブジェクトの寿命が`if let`ブロック全体に及んでしまうため発生。`let taken = state.0.lock().unwrap().take();`のように一度変数へ代入してから`if let`することで解決。

### 6.5 Tauriの透明ウィンドウ・小窓⇔会話パネル切替（工程3、最終的に方針転換）

これが最も時間を要した問題です。

- **試したこと**: 正本仕様書3章の「透明・枠なしの小さなキャラウィンドウ→クリックで会話パネルを開く」を素直に実装。`tauri.conf.json`で`transparent: true`, `decorations: false`, `alwaysOnTop: true`, `skipTaskbar: true`の256×256ウィンドウを作り、Rust側で`show_chat`/`hide_to_character`関数によりウィンドウサイズを動的に切り替える方式（256×256の小窓⇔420×640の会話パネル）を実装した。
- **できたこと**: 透明ウィンドウの表示自体、キャラクリックでウィンドウサイズが切り替わる動作は実現できた。CSS側で`background: rgba(0,0,0,0)`（`transparent`キーワードではなく明示的な透明値。WebView2で`transparent`キーワードが不透明合成される既知の問題を避けるため）、Canvas側で全面塗りつぶしを削除することで、デスクトップの壁紙が透けて見える表示にも成功した。
- **できなかったこと**: ウィンドウは開くが「サーバーに接続できません」というエラーが解消しなかった。開発者ツールのNetworkタブを確認しても`/session`や`/history`へのリクエストが一切記録されず、`toggle_chat_panel`/`close_chat_panel`（Tauriコマンド呼び出し）だけが200 OKで成功していた。
  - 仮説1: CORS Originの問題。Windows版TauriのWebViewは`Origin: http://tauri.localhost`を送信する（macOS/Linuxの`tauri://localhost`とは異なる）ため、`config.py`の`allowed_origins`に`http://tauri.localhost`等を追加したが解消せず。
  - 仮説2: レースコンディション。Rust側の`backend.ready`イベント（1回限りのemit）を、フロントエンド側のリスナー登録前に発火してしまい、フロントエンドが永久に「準備完了」を検知できていないのではと推測。対策として`is_backend_ready`という問い合わせ用Tauriコマンドを追加し、リスナー登録後に明示的に状態を確認するコードを実装したが、これも効果がなかった。
  - 最終的に原因を完全には特定できなかった。
- **分かったこと・判断**: 複雑な実装（透明ウィンドウ、小窓⇔会話パネルの動的サイズ切替、イベントベースのbackend準備完了通知）を維持したままデバッグを続けるコストが、得られる価値（見た目の凝った演出）に見合わないと判断。ユーザーからも「不安定・不確定な部分は実装不要」と明確な方針転換の指示があり、以下のシンプルな構成に置き換えて解決した。
  - 通常の枠付きウィンドウ（420×640）を常に表示し、小窓⇔会話パネルの切替自体を廃止。
  - トレイメニューは「表示」「終了」のみに削減。
  - backendの起動待ちは、Tauriイベント（emit/listen）ではなく、フロントエンド側で`/health`エンドポイントへの単純なリトライ付きfetch（500ms間隔、最大20秒）に変更。イベントの取りこぼしという非決定的な問題を、ポーリングという決定的な方式に置き換えることで解消した。
  - この変更後、`npx tauri dev`再実行で問題なく動作することを確認。
- **教訓**: Tauriのイベント（`emit`/`listen`）は、フロントエンドの初期化タイミングによって取りこぼしが起きうる。特にRust側の処理が速く完了する場合、フロントエンドのリスナー登録前にイベントが発火してしまうケースがある。起動時の同期を取りたい場合、イベント購読よりも「フロントエンド側から能動的に状態を問い合わせるポーリング」の方が単純で確実な場合がある。

### 6.6 一時的な502エラー（工程3完了後）

- Tauri再起動直後、`/history`へのリクエストのみ502 Bad Gatewayが1回発生（`/health`は200 OKで通っていた）。原因は特定していないが、アプリを再起動したところ再現しなくなった。backend起動直後の一時的なタイミング問題（DB接続プールの初期化など）だった可能性がある。継続して発生する場合は要調査。

### 6.7 リモート環境特有の制約（開発支援ツール側の学び）

- この引き継ぎ作業はクラウド上のAIエージェント（Claude）が、ユーザーのWindows PCにリモートでファイルを書き込む形で行われた。Claude内蔵ブラウザ環境からは、リモートWindows機のlocalhost（127.0.0.1）に到達できないため、動作確認は必ずユーザー自身の実機ブラウザ（開発者ツール含む）で行う必要があった。
- ファイルの削除・移動はこの環境から直接実行できず、ユーザーに手動削除を依頼する形になった（7章参照）。今後リモート作業を依頼する場合、削除が必要なファイルが出ることを見込んでおくとよい。

---

## 7. 整理事項・重要な補足

### 7.1 削除待ちファイル（ユーザー手動対応依頼中）

以下は本引き継ぎ作成時点でユーザーに削除を依頼済みだが、完了確認は取れていません。次の作業者は存在有無を確認してください。

- `C:\kaguya-ai\Claude outputs\env` — `backend\.env`と同内容の重複ファイル（Gemini APIキー・DBパスワードを平文で含む誤配置ファイル）
- `C:\kaguya-ai\Claude outputs\`フォルダ自体（空になっていれば）
- `C:\kaguya-ai\backend\check_gemini.py` — モデル選定用の一時デバッグスクリプト
- `C:\kaguya-ai\backend\list_gemini_models.py` — 同上

### 7.2 機密情報の取り扱い

- `backend/.env`にGemini APIキーとDB接続文字列（パスワード含む）が平文で保存されています。`.gitignore`で除外済みですが、バックアップやスクリーンショット共有の際は写り込みに注意してください。
- 本ドキュメント含め、これまでの作業ログ上にAPIキー・DBパスワードの平文が一部残っている可能性があります。リポジトリを外部に公開する前にはキーのローテーションを推奨します。

### 7.3 ポート・接続情報

| 項目 | 値 |
|---|---|
| backend | `http://127.0.0.1:8765` |
| PostgreSQL | `127.0.0.1:5433`（標準の5432ではない点に注意） |
| Viteデフォルト開発サーバー | `http://127.0.0.1:5173`（Tauri経由では使わず、プレーンブラウザでの単体確認用） |

### 7.4 今後着手する場合の推奨順序

正本仕様書の最短実装手順に従い、工程4（記憶の知恵化・自発的な声かけ）から着手するのが妥当です。特に以下の点に注意してください。

- `wisdom`・`persona`テーブルは既にスキーマが存在するため、マイグレーション追加は不要。読み書きするジョブ処理（日次03:00の知恵化、週次のpersona更新）をゼロから実装する必要があります。
- 自発的な声かけは、正本仕様書4章にある通りControllerが1本のタイマーを持つ設計とし、DBの会話履歴には保存しない定型文であることに注意してください。
- 6.5節の教訓（Tauriイベントの取りこぼし）を踏まえ、新機能でもタイミング依存の実装は避け、可能な限りポーリングや冪等な設計を優先することを推奨します。

---

## 8. 参考：既存ドキュメントとの対応関係

| 本書の章 | 対応する正本仕様書の章 | 差分の有無 |
|---|---|---|
| 2章 アーキテクチャ | 5章「構成と通信」 | 実装範囲が工程0〜3のみのため一部要素（音声API、iPhone）が未実装 |
| 3章 技術スタック | 5章「構成と通信」の表 | ほぼ一致。バージョンを実装時点の実測値で明記 |
| 4章 FE/BE/DB設計 | 6章「会話とAPIの契約」、8章「DBと記憶のルール」 | エンドポイントは実装済み分のみ抜粋。wisdom/persona未実装 |
| 5章 現在の仕様 | 6章「会話とAPIの契約」 | 実装通り、齟齬なし |
| 6章 試したこと | （正本仕様書に対応章なし、新規） | - |
| 7章 整理事項 | 10章「保存・起動・認証」 | 削除待ちファイルの状況を追記 |

`kaguya_ai_codex_handoff.md`（v1）の「次に実施する作業」1〜5は全て完了済みです。同メモの「最初の完了条件」（ブラウザで文章を送ると返答があり、DBへ重複なく保存され、再起動後も履歴表示でき、429・通信失敗・保存失敗を区別できる）も達成済みであることを確認しています。
