# oura-sync

Oura Ring (API v2) のデータを Google Sheets / BigQuery に取り込むスクリプト。
- daily 系・sleep・workout など低頻度データ → Google Sheets (エンドポイントごとに 1 タブ、キーで冪等 upsert)
- heartrate / ring_battery_level など高頻度データ → BigQuery `oura-data-yy.oura`
  (日付パーティション。ステージング + MERGE で冪等)

## BigQuery 側のメモ

- プロジェクト `oura-data-yy` (yacropolisy@gmail.com 所有)。yyamada@rimo.app にも
  dataEditor / jobUser を付与済み
- 無料枠 (ストレージ 10GB / クエリ月 1TB) 内で運用する。**ストリーミング挿入は有料なので使わない**
  (ロードジョブ + クエリのみ)
- 請求先アカウント 0118A4-E5AED1-EF566E にリンク済み (2026-08-31)。
  テーブル失効・パーティション失効は解除済み。
  ※ 課金未リンクのサンドボックスに戻すとテーブルが 60 日で失効するので注意。
  失効解除は `bq update --expiration 0` と `--time_partitioning_expiration 0` の両方が必要
- 初回バックフィルは `uv run python scripts/bq_backfill.py --since 2026-01-01`
- ChatGPT から BigQuery を叩く MCP コネクタ用の OAuth クライアント
  (client ID: 50508619040-ni960h1g8g8pdcm3bf195nb3qui8b8mm) の JSON は
  `secrets/chatgpt-bq-oauth-client.json` (git 管理外)。
  リダイレクト URI は `https://chatgpt.com/connector_platform_oauth_redirect`、
  テストユーザーに yacropolisy@gmail.com を登録済み

## セットアップ

### 1. Oura アプリ登録
1. https://cloud.ouraring.com/oauth/applications で "New Application"
2. Redirect URI に `http://localhost:8765/callback` を登録
3. Client ID / Client Secret を控える

### 2. Google Sheets 側の認証 (どちらか)

**A. サービスアカウント (推奨・確実)**
1. 任意の GCP プロジェクトで Google Sheets API を有効化
   `gcloud services enable sheets.googleapis.com --project <project>`
2. サービスアカウントを作り JSON 鍵を `service-account.json` として保存
3. 書き込み先スプレッドシートをそのサービスアカウントのメールに**編集者**として共有
4. `.env` に `GOOGLE_APPLICATION_CREDENTIALS=./service-account.json`

**B. gcloud の ADC (自分のアカウントで書く)**
```
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/drive
gcloud services enable sheets.googleapis.com drive.googleapis.com --project <quota project>
```
※ 現在の ADC は Sheets スコープなしで発行されており、quota project (`rimo-dev-0`) でも Sheets API が未有効。

### 3. 設定
```
cp .env.example .env   # 値を埋める
uv sync
uv run oura-sync auth  # ブラウザで Oura を認可 → tokens.json 生成
```

## 使い方
```
uv run oura-sync sync                       # 直近 7 日 (日次運用向け)
uv run oura-sync sync --since 2023-01-01    # 初回バックフィル
uv run oura-sync sync --only sleep workout  # 対象を絞る
```

## 定期実行 A: Google Apps Script (推奨・PC 不要)

`deploy/oura_sync.gs` を使うとスプレッドシート側だけで完結する (Mac の起動不要)。

※ Personal Access Token は deprecated で新規発行不可のため OAuth を使う。
Oura の refresh token は single-use (使うたびにローテーション) なので、
ローカルの `tokens.json` とは**別に GAS 専用のトークンを発行**する必要がある。

1. GAS 用トークンを発行: `uv run python scripts/auth_gas.py` 相当の手順で
   `tokens_gas.json` を作る (ローカルの tokens.json とは別チェーン)
2. 対象スプレッドシートの「拡張機能 > Apps Script」に `deploy/oura_sync.gs` を貼り付け
3. プロジェクトの設定 > **スクリプト プロパティ** に以下を登録
   - `OURA_CLIENT_ID` / `OURA_CLIENT_SECRET` (.env と同じ値)
   - `OURA_REFRESH_TOKEN` (`tokens_gas.json` の `refresh_token`)
   - トークンはシートの閲覧者からは見えない。コードにも Git にも残さないこと
4. エディタ左の「サービス +」から **BigQuery API** を追加
   (heartrate / ring_battery_level の書き込み先が BigQuery のため)
5. エディタで `syncAll` を一度手動実行して権限を承認・動作確認
6. `setupDailyTrigger` を一度実行 → 毎朝 8 時台に自動同期 (起床後のアプリ反映を待つため)
7. 初回 `syncAll` 成功後、`tokens_gas.json` は削除してよい
   (以降 refresh token は GAS のスクリプト プロパティ内でローテーションされる)

GAS を使う場合、ローカルの launchd 登録は不要 (二重実行しても upsert なので壊れはしないが無駄)。

## 定期実行 B: macOS launchd の例
`~/Library/LaunchAgents/app.oura-sync.plist` に毎朝 6 時実行を登録:
```
launchctl load ~/Library/LaunchAgents/app.oura-sync.plist
```
(plist は `deploy/app.oura-sync.plist` を参照)

## 設計メモ
- refresh token は single-use。`get_access_token()` は refresh 直後に `tokens.json` を上書きする
- Oura は過去データが後から更新されることがあるため、日次でも 7 日分を再取得して upsert する
- `heartrate` / `ring_battery_level` は時系列で件数が多いので 30 日ずつ取得
- ネストした JSON は `a.b` 列に平坦化、配列 (5 分ごとの HRV など) は JSON 文字列で 1 セルに格納
- 旧 `tag` エンドポイントは `enhanced_tag` に置き換わっているため対象外
- Strava は API アプリ作成に Strava 有料サブスクリプションが必須 (2026-06 改定) のため保留

## Privacy Policy / Terms of Service

このアプリケーションは作者本人が個人利用するためのものです。Oura API から取得したデータは
作者自身が所有する Google スプレッドシートにのみ保存され、第三者に提供・共有することはありません。
他のユーザーが利用することは想定していません。

## NRC (Apple Health) ラン取り込み API

NRC → Apple Health → iOS ショートカット (無料アプリ「Actions」の Find Workout) → Cloud Run → BigQuery の 0 円構成。

```text
Nike Run Club → Apple Health → Actions + ショートカット
  → POST https://nrc-ingest-964507190351.asia-northeast1.run.app/api/runs
  → BigQuery spherical-depth-263101.running.runs
```

- コード: `server/` (FastAPI)。GCP プロジェクトは `spherical-depth-263101` (yacropolisy@gmail.com)
- Cloud Run サービス `nrc-ingest` (asia-northeast1)、SA `nrc-ingest@spherical-depth-263101.iam.gserviceaccount.com`
- BigQuery: テーブル `running.runs` (started_at 日付パーティション)、分析用ビュー `running.runs_v` (pace 計算済み)
- 書き込みは MERGE (キー: source + started_at) で冪等。再送で heart_rates が空でも既存時系列は保持
- **ストリーミング挿入は不使用** (クエリジョブのみ = 無料枠内)
- 認証: Bearer トークン (`server/.api_token`、git 管理外)。Cloud Run の env `API_TOKEN`

### エンドポイント
- `GET /health` … 死活確認 (認証不要)。※ `/healthz` は run.app ドメインでは Google Frontend に予約されていて使えない
- `POST /api/runs` … 取り込み。`started_at` 必須 (ISO8601)。`distance` が 30 未満なら km とみなし m に変換
- `GET /api/runs?limit=N` … 直近ランの一覧 (runs_v)

### デプロイ
```
TOKEN=$(cat server/.api_token)
gcloud run deploy nrc-ingest --source server \
  --project spherical-depth-263101 --region asia-northeast1 \
  --allow-unauthenticated \
  --service-account nrc-ingest@spherical-depth-263101.iam.gserviceaccount.com \
  --memory 512Mi --max-instances 2 \
  --set-env-vars "API_TOKEN=${TOKEN},BQ_PROJECT=spherical-depth-263101,BQ_DATASET=running"
```

### iOS ショートカット側 (手動セットアップ)
1. App Store で「Actions」(無料) をインストール
2. ショートカット作成:
   - Actions: **Find Workouts** … Type=Running / Sort=Start Date 降順 / Limit=1
   - (任意) ヘルスケアサンプルを検索 … 心拍数、Workout の開始〜終了で絞り込み
   - 「テキスト」で JSON 組み立て → 「URLの内容を取得」で POST
     - URL: `https://nrc-ingest-964507190351.asia-northeast1.run.app/api/runs`
     - ヘッダー: `Authorization: Bearer <server/.api_token の値>` / `Content-Type: application/json`
     - ボディ例: `{"started_at":"<開始日時 ISO8601>","ended_at":"<終了日時>","distance":<km>,"active_calories":<kcal>,"heart_rates":[{"time":"...","bpm":132},...]}`
3. オートメーション: 「Nike Run Club が閉じられたとき」+ 保険で「毎日 23:00」に実行 (冪等なので重複 POST しても安全)
