# このリポジトリでの作業ルール

## 会話で確定した内容をファイルに残す

- ユーザーとの会話で確定した練習計画、日程、目標、制約、通知や定期実行の運用変更は、作業を終える前に必ず適切なファイルへ書き戻す。会話履歴だけを唯一の情報源にしない。
- 練習計画・日付ごとの例外・休養日の変更は `training-plan-2027-02-14.md` に反映する。通知・定期実行の運用は `notification-workflow.md` に反映する。その他の変更は関連する既存ファイルへ記録する。
- 未確定の提案や質問を、決定済みとして保存しない。最新のユーザー指示を優先し、古い記述と矛盾しないよう更新する。期間限定の変更には対象日付と時間帯（Asia/Tokyo）を記載する。
- 新しい会話や定期実行では、関係するファイルを最初に読み、確定済みの内容を引き継ぐ。
- ダッシュボードを更新するときは `dashboard-workflow.md` を読む。本人限定のSitesを維持し、既存の計画履歴を保存する。Site専用リポジトリと通常のGitHubリポジトリを混同しない。
- 当日の調整案は、ユーザーの合意なしに長期計画の恒久変更として扱わない。分析による一時的な提案を保存する場合は、提案であることと対象日付を明記する。
- 毎週日曜の週間見直しはユーザーが自動更新を依頼済み。既存の目標・負荷の段階・走れる時間を守る範囲で、翌月曜〜日曜の日別計画を更新してよい。対象週、更新日、変更理由、天気予報の取得日と不確実性を残し、本人が承認したと偽らない。長期目標や21週間の基本方針を変更する場合は別途相談する。
- APIトークン、秘密鍵などの認証情報はこれらの文書やGitに書かない。Git対象外の `secrets/` 等で管理し、文書には参照先だけを書く。
- ファイル更新後は内容を確認し、更新先をユーザーへ簡潔に伝える。ファイルへの記録と、外部サービスの設定変更・コミット・pushの完了は区別する。

## 編集後のコミット・push

- ユーザーの継続的な指示により、会話・定期実行で編集を行ったら、確認後に都度 `main` へコミットし、対応するリモートへpushする。変更がないときは空コミットしない。
- 親リポジトリは `origin/main`。今回の作業に関係するファイルだけを明示的にステージし、無関係な既存変更を巻き込まない。認証情報・Git対象外のデータを追加しない。
- `dashboard-site/` は独立したSite用リポジトリ。こちらの `main` はSites専用の非公開リモートへpushし、Sitesの保存・非公開デプロイまで完了させる。親GitHubへSite内の個人データをpushしない。
- pushが失敗したら完了と報告しない。認証・競合など原因を確認し、force pushや他者の変更の破棄は行わない。

## 回復データの参照元（2026年9月23日変更）

- 日次通知・週次見直し・その他の回復分析は `oura-api-workflow.md` に従いOura APIを直接参照する。ラン実績は引き続きBQを使う。Ouraの運動一覧でNike Run Clubの記録を置き換えない。

## remote 環境で Oura API を叩く

Oura の refresh token は single-use (使うたびにローテーション) のため、remote 環境では
トークンを `tokens.json` ではなく **GCP Secret Manager** (`oura-data-yy` プロジェクト) に保存する。
環境変数 `OURA_TOKEN_SECRET` があればコードは自動的に Secret Manager を使う
(`oura_sync/secret_store.py`)。refresh のたびに新バージョンを追加し、旧バージョンは destroy する。

### 必要な環境変数

| 変数 | 値 |
|---|---|
| `OURA_CLIENT_ID` | Oura OAuth アプリの Client ID |
| `OURA_CLIENT_SECRET` | Oura OAuth アプリの Client Secret |
| `OURA_TOKEN_SECRET` | `projects/oura-data-yy/secrets/oura-remote-tokens` |
| `GOOGLE_SERVICE_ACCOUNT_KEY` | ローカルの `service-account.json` の中身 (生 JSON か base64)。SA は `oura-sync@spherical-depth-263101.iam.gserviceaccount.com` |
| `SPREADSHEET_ID` | (Sheets に書く場合のみ) 書き込み先スプレッドシート ID |

### 使い方

環境変数が揃っていれば、ローカルと同じコマンドがそのまま動く。

```sh
uv sync
# 回復データを読むだけ (Sheets/BQ には書かない。oura-api-workflow.md 参照)
uv run python scripts/read_oura_recovery.py --days 7
# Sheets に同期 (SPREADSHEET_ID が必要。シートは SA に共有済み)
uv run oura-sync sync --days 7
```

### 注意

- **同時に複数の remote セッションで実行しない。** `tokens.lock` はマシン内の排他にしかならない。access token の期限切れ時に 2 プロセスが同時に refresh すると、
  片方の refresh token が失効済みになりチェーンが切れる (再認可が必要になる)
- `tokens.json` や GAS のスクリプト プロパティの refresh token を remote で使わないこと。
  使った瞬間にそちらのチェーンが失効し、毎朝の GAS 同期が止まる
- トークン・鍵の値をログやコミットに出力しないこと
- `oura-sync auth` はブラウザでの認可が必要なので remote では実行できない。チェーンが切れたら
  人間に下記「再認可」を依頼する

### 初回セットアップ (2026-10-01 実施済み)

Sheets 書き込み用の既存 SA (`service-account.json`) をそのまま使い、
`oura-data-yy` のシークレットにだけ権限を付けている (プロジェクト横断)。

```sh
P=oura-data-yy
SA=oura-sync@spherical-depth-263101.iam.gserviceaccount.com
gcloud services enable secretmanager.googleapis.com --project $P
gcloud secrets create oura-remote-tokens --replication-policy=automatic --project $P
for role in roles/secretmanager.secretAccessor roles/secretmanager.secretVersionManager; do
  gcloud secrets add-iam-policy-binding oura-remote-tokens --project $P \
    --member serviceAccount:$SA --role $role --condition=None
done
```

続けて「再認可」を実行してトークンを Secret Manager に入れ、
`service-account.json` の中身を remote 環境の `GOOGLE_SERVICE_ACCOUNT_KEY` に設定する。

### 再認可 (チェーンが切れたとき / 初回)

ローカルで remote 専用の新しいチェーンを発行し、Secret Manager に直接保存する。

```sh
OURA_TOKEN_SECRET=projects/oura-data-yy/secrets/oura-remote-tokens \
GOOGLE_SERVICE_ACCOUNT_KEY="$(cat service-account.json)" \
uv run oura-sync auth
```
