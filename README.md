# oura-sync

Oura Ring (API v2) のデータを Google Sheets に取り込むスクリプト。
エンドポイントごとに 1 タブ、`id` をキーに冪等 upsert するので何度実行しても重複しない。

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

## 定期実行 (macOS launchd の例)
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
