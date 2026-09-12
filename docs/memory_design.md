# かぐやの記憶設計

かぐやが扱うデータを3系統に整理し、テーブル定義とデータの流れを定める。
実装はこの文書を基準に行い、食い違いが出たらこちらを先に直す。

## 1. 3系統の定義

かぐやが持つデータは「**誰についての情報か**」で3つに分かれる。

| 系統 | 定義 | 変化の速さ | 接頭辞 |
|---|---|---|---|
| **Memory** | ユーザーを覚える | 日〜年 | `memory_` |
| **Living** | かぐやの今の状態 | 分〜時間 | `living_` |
| **Persona** | かぐやの性格 | 週〜月 | `persona_` |

置き場所に迷ったら、次の順で判断する。

1. **誰の情報か** — ユーザーなら Memory、かぐやなら Living か Persona
2. **どれくらいで変わるか** — 数時間で戻るなら Living、週単位で育つなら Persona
3. **毎回使うか** — 話題に関係するときだけ使うなら Memory、毎回必ず使うなら Persona

### 接し方の扱い

`reply_style` / `addressing` / `support_style` は、出自はユーザーの好みだが
**定着先はかぐや**として Persona に置く。相手に合わせた接し方が繰り返されて
身についたなら、それはもうかぐやの性格の一部、という解釈による。

長期記憶に混ぜてはいけない。長期記憶は「話題に関係するときだけ上位N件」を
使うのに対し、接し方は「毎回必ず全部」使う。混ぜると、たまたま言葉が一致
しなかった回だけ接し方が抜け落ちる。

## 2. テーブル定義

7テーブル。すべて PostgreSQL。

### Memory系

#### `memory_short` — 短期記憶（会話の逐語録）

旧 `raw_memory`。発言そのものを、加工せずに残す。

| カラム | 型 | 説明 |
|---|---|---|
| `id` | uuid PK | |
| `turn_id` | uuid | 1往復の識別子。user と assistant で対になる |
| `role` | text | `user` / `assistant` |
| `content` | text | 発言そのまま |
| `status` | text | `pending` / `completed` / `failed` / `cancelled` |
| `input_mode` | text | `text` / `voice` |
| `origin_client_id` | uuid | どの端末から話したか |
| `created_at` | timestamptz | |
| `processed_at` | timestamptz | 長期記憶へ整理した時刻。NULL なら未整理 |
| `processing_reason` | text | 整理の結果（`wisdom` / `no_durable_fact` / `cancelled`） |
| `revision` | integer | 訂正のたびに増える |

**寿命**：整理済みかつ7日経過した往復を削除する。未整理の発言は消さない。

#### `memory_long` — 長期記憶（ユーザーの好み・事実）

旧 `wisdom`。話題ごとに1行へ集約する。

| カラム | 型 | 説明 |
|---|---|---|
| `id` | uuid PK | |
| `topic_key` | text UNIQUE | 日本語の短い名詞句 |
| `summary` | text | 覚えている内容 |
| `kind` | text | `explicit`（本人が言った） / `inferred`（推測） |
| `support_level` | text | `unconfirmed` / `stated` / `repeated`（自動算出） |
| `importance` | integer 1-5 | 1=その場限りに近い、5=生活や価値観の前提 |
| `tone` | smallint -1/0/1 | **新規**。記憶したときの感情（ネガ／普通／ポジ） |
| `evidence` | jsonb | 根拠となった `memory_short` の id・日付・抜粋 |
| `locked` | boolean | 手で直した記憶。自動更新で上書きしない |
| `last_seen_at` | timestamptz | 最後に話題に出た日 |
| `last_used_at` | timestamptz | 最後に想起された時刻 |
| `revision` | integer | |
| `updated_at` | timestamptz | |

**寿命**：無期限。ただし `last_used_at` は想起スコアの減点に使う。

**`tone` について**：3値に留める。細かい感情値を持たせても、抽出の精度が
それに見合わない。想起スコアで「今の気分に近い記憶を優先する」ためだけに使う。

#### `memory_concern` — 気がかり（ユーザーの予定・体調）

旧 `mind_open_loops`。短期／長期と並ぶ3本目の記憶。人間でいう展望記憶
（これからやること・気にかけること）にあたり、エピソード記憶・意味記憶とは
別系統として扱う。

| カラム | 型 | 説明 |
|---|---|---|
| `topic` | text PK | 話題（「面接」「熱」など） |
| `kind` | text | `plan`（予定） / `concern`（体調・心配事） |
| `quote` | text | 抽出のきっかけになった発言 |
| `opened_at` | timestamptz | 記録した時刻 |
| `due_at` | timestamptz | この時刻を過ぎてから話題にする |
| `last_asked_at` | timestamptz | 最後に触れた時刻 |
| `asked` | integer | 触れた回数。上限に達したら諦める |
| `resolved_at` | timestamptz | 片付いた時刻。NULL なら未解決 |

**寿命**：解決済みは14日、動きのないものは30日で削除。

**`memory_short` のカラムにしない理由**：寿命が違う（7日 vs 30日）、粒度が
違う（発言ごと vs 話題ごと）、可変の状態を持つ（不変の記録に混ぜない）。

### Living系

#### `living_emotion` — 感情

旧 `mind_emotions`。6つの感情を0〜100で持つ。

| カラム | 型 | 説明 |
|---|---|---|
| `name` | text PK | `happiness` / `curiosity` / `boredom` / `affection` / `jealousy` / `concern` |
| `value` | double precision | 0〜100 |
| `updated_at` | timestamptz | 減衰計算の起点 |
| `samples` | integer | これまでに数えた回数 |
| `high_count` | integer | そのうち「強い」と言えた回数 |

**履歴の表は持たない。** 週次で傾向を見るのに必要なのは「その状態が何回あったか」
だけで、毎ターン1行増える記録は掃除の手間に見合わない。

**寿命**：保存はするが、読み出し時に半減期で基準値へ減衰させる。
値そのものを消すことはない。

#### `living_activity` — 活動・元気さ・会った記録

**新規**。いまフロントエンドの localStorage と `app_settings.ledger` に
散らばっているものを、1行にまとめる。常に1行だけ持つ。

| カラム | 型 | 説明 |
|---|---|---|
| `id` | boolean PK DEFAULT true | 常に1行（`CHECK (id)`） |
| `activity` | text | `idle` / `reading` / `working` / `playing` / `snacking` / `daydreaming` / `sleeping` |
| `energy` | smallint | 0〜100 |
| `last_seen_at` | timestamptz | 最後に会った時刻 |
| `hour_counts` | jsonb | 24要素の配列。よく会話する時間帯の学習 |
| `chats` | integer | 会話が成立した回数 |
| `days` | jsonb | 会話した日付の配列 |
| `updated_at` | timestamptz | |

**サーバに置く理由**：かぐやはPC上に1人しかいない。端末ごとの localStorage に
持つと、PCとiPhoneで違う行動をする。元気さの計算もサーバとフロントで
二重になっており、数値も食い違っている。

### Persona系

#### `persona_character` — 人格・接し方

旧 `persona`。毎回かならずプロンプトへ全件渡す。

| カラム | 型 | 説明 |
|---|---|---|
| `key` | text PK | `base_personality` / `reply_style` / `addressing` / `support_style` / `style_feedback` / `disposition` |
| `value` | jsonb | 内容 |
| `locked` | boolean | true なら自動更新しない。`base_personality` は常に true |
| `source_wisdom_ids` | jsonb | 根拠にした `memory_long` の id |
| `previous_value` | jsonb | 直前の値。「元に戻す」に使う |
| `previous_source_wisdom_ids` | jsonb | |
| `revision` | integer | |
| `updated_at` | timestamptz | |

`base_personality` だけが不変。他は週次で1項目ずつ育つ。

#### `persona_favorite` — かぐやの好み

旧 `mind_traits`。

| カラム | 型 | 説明 |
|---|---|---|
| `name` | text PK | 対象（「猫」「朝」など） |
| `valence` | double precision | 0〜1。1に近いほど好き |
| `confidence` | double precision | 0〜1。繰り返すほど上がる |
| `evidence` | integer | 根拠の数 |
| `updated_at` | timestamptz | |

**寿命**：定着しなかったもの（`confidence` が閾値未満）は30日で忘れる。

### 廃止するテーブル

| テーブル | 理由 | 移し先 |
|---|---|---|
| `mind_phrases` | ユーザーの口癖。プロンプトへ渡しておらず、表示のみで機能していない | なし（削除） |
| `mind_graph_edges` | `かぐや→likes→対象` しか作らず `mind_traits` の写し | なし（削除） |
| `mind_meta` | 会話回数のカウンタ | `living_activity.chats` |
| `app_settings.ledger` の関係性データ | 慣れ・利用日数・話し方フィードバック | `living_activity` と `persona_character` |

`app_settings` は設定（`options`）とスケジューラの記録（`ledger`）に戻す。
`calendar_events` と `reminders` は記憶ではないので、この設計の対象外。

## 3. データフロー

会話に影響されて、状態と性格が段々と育っていく。

```
【1. 記憶する】毎ターン・同期
   会話 ──→ memory_short（無条件で保存）
        └─→ living_activity（会った時刻を更新）

【2-a. 感じる】毎ターン・同期／LLMを呼ばない
   memory_long ──┐
                 ├──→ living_emotion
   発言の表層 ───┘
   ① いま話している事柄が、その人にとって大事なら感情が動く
   人間でいう情動反応。速い・無意識・言葉より先

【2-b. 覚える】日次バッチ・LLM
   memory_short ──→ memory_long    （記憶の定着）
                └─→ memory_concern （予定・気がかりの抽出）
   人間でいう睡眠中の記憶固定化

【2-c. 育つ】週次バッチ
   living_emotion の回数 ──→ persona_character（disposition）
   ② 同じ状態が繰り返されると性格になる。LLMは呼ばない
   memory_long ──(LLM)──→ persona_character（接し方）
   人間でいう経験による性格形成。遅い・たまにしか変わらない

【3. 話す】毎ターン
   living_emotion が「何を思い出すか」の重みを決める ③
          ↓
   memory_long 上位N件 + persona_* 全件 + living_* の現在値
    + memory_short 直近 ──→ プロンプト ──→ LLM ──→ 返答
```

### 段階ごとの実行場所

| 段階 | いつ | LLM | 場所 |
|---|---|---|---|
| 1. 記憶する | 毎ターン | 不要 | `Controller._run` |
| 2-a. 感じる | 毎ターン | 不要 | `Controller._run`（想起の直後） |
| 2-b. 覚える | 日次 | 必要 | `Jobs.run` |
| 2-c. 育つ | 週次 | 好みは不要／接し方は必要 | `Jobs.run` |
| 3. 話す | 毎ターン | 必要 | `Controller._run` |

## 4. 想起スコア

「いま何を思い出すか」を次の式で決める。現在は一致度と重要度しか見ていない。

順位づけの要素を、強い順に並べる（積ではなく優先順で見る）。

```
一致の有無 → 一致の強さ → 手で直した記憶 → 文脈の一致
  → 本人が言ったこと → 気分の一致 → 重要度 → 更新の新しさ
```

| 要素 | 元データ | 効き方 |
|---|---|---|
| 一致度 | 発言との部分一致 | 一致した語が長く多いほど高い |
| 重要度 | `memory_long.importance` | 1〜5 |
| 新しさ | `memory_long.last_used_at` | 最近使ったものほど出やすい |
| 気分の一致 | `living_emotion` と `memory_long.tone` | **新規** |

**気分の一致**は、人間の気分一致効果（mood-congruent recall）にあたる。
落ち込んでいるときはつらい記憶を、機嫌がいいときは楽しい記憶を思い出しやすい。
これを入れると、同じ質問でもその日の状態で返事が変わる。

いまの表情から符号を1つ求め、`tone` が一致する記憶を優先する。

| 今の表情 | 優先する `tone` |
|---|---|
| `worried` / `sulky` | -1 |
| `happy` | +1 |
| その他 | 差をつけない |

`tone` が 0 の記憶はどの気分でも中立に扱う。**合わない記憶を捨てはしない**
（同じ強さで並んだ候補の中で選ばれやすくなるだけ）。

渡すのは「この会話が始まる前の表情」になる。実装2で順序を
「想起 → 感情」にしたため、思い出すときの気分は直前までのもの。
人間の思い出し方と同じ順序なので、これでよい。

## 5. 応答時間

**この設計で、1ターンの同期処理は増えない。**

現在の1ターンは DB 7〜8クエリ（合計数ミリ秒）と LLM 1〜3秒で、体感の遅さは
ほぼ LLM だけが決めている。そのうえで次を守る。

- **① 記憶→感情は、想起の結果を再利用する。** 追加のクエリを発行しない。
  そのために処理順を「感情更新 → 想起」から「**想起 → 感情更新**」へ入れ替える
  （記憶を引いてから感情が動く、という順序は人間としても自然）
- **③ 気分の一致は `memory_long` のカラムを見るだけ。** 追加コストなし
- **感情の保存は返答後に回す。** ターン中の書き込みを1回減らす
- **② 状態→性格は週次バッチ。** ターンに影響しない
- `living_activity` の更新は毎ターン1回だが、いま `ledger` へ毎ターン書いて
  いる分の置き換えなので、実質増えない

## 6. 移行

既存データは失わずに移す。テーブル名の変更と新規カラムを1つのマイグレーション
（`005_memory_redesign.sql`）で行う。

| 作業 | 方法 |
|---|---|
| `raw_memory` → `memory_short` | `ALTER TABLE ... RENAME` |
| `wisdom` → `memory_long` | 同上。`tone` を `0` 既定で追加 |
| `mind_open_loops` → `memory_concern` | 同上 |
| `mind_emotions` → `living_emotion` | 同上 |
| `mind_traits` → `persona_favorite` | 同上 |
| `persona` → `persona_character` | 同上 |
| `living_activity` | 新規作成。`mind_meta.interactions` と `ledger` の関係性データを移す |
| `mind_phrases` / `mind_graph_edges` / `mind_meta` | `DROP TABLE` |

`kaguya.bat` の更新時にマイグレーションが1度走る。ロールバックは、この
マイグレーションを当てる前のバックアップから戻す。

改名は2度流せないため、当てたスクリプトを `schema_migrations` に記録し、
未適用のものだけを流す方式へ変えた（従来は毎回すべて流していた）。

## 7. 実装の順序

手戻りを避けるため、この順で分けて進める。

| # | 内容 | 依存 | 状態 |
|---|---|---|---|
| 0 | この設計文書 | — | 済 |
| 1 | マイグレーションとリネーム（**振る舞いは変えない**）、`living_activity` の新設とフロントからの移行 | 0 | 済 |
| 2 | ① 記憶 → 感情（想起結果の再利用、処理順の入れ替え） | 1 | 済 |
| 3 | ③ 想起スコア（`tone` の付与と気分の一致） | 1, 2 | 済 |
| 4 | ② 状態 → 性格（週次で傾向が育つ） | 1, 2 | 済 |

`tone` は3から、日次整理で新しく作る記憶にだけ付く。既存の記憶は0（中立）の
ままなので、気分一致が効いてくるまで数日かかる。遡って付け直すことはしない
（LLMの呼び出しが増えるわりに、古い記憶ほど当てにならない）。

1は既存の振る舞いを変えないため、問題が起きてもリネームだけを疑えばよい。
2以降は1つずつ体感を確かめながら進める。
