# 受信JSONの診断ログ

`LOG_REQUEST_BODY=true` の間だけ、認証に成功した `POST /api/runs` のJSONを正規化・改行分割前に記録する。既定は無効。Cloud Loggingでは `jsonPayload.event="runs_request_body"` で絞り込める。

本文には心拍などのラン情報が含まれる。既存プロジェクトのログ保持・閲覧設定が適用され、無効化後も過去ログは残る。ヘッダー、query、認証失敗時の本文は記録しない。既知の認証フィールドを再帰的に秘匿し、現在のAPI_TOKEN値も文字列から除く。JSON不正時は本文を記録せず400を返す。認証済みの有効なJSONなら、started_at不足で422になる場合も秘匿済み本文を記録する。

ログはstdoutの1行JSON。HTTP本文のバイト列ではなく、解析後の構造・値を保存するため、空白や重複キーは復元できない。秘匿後のエントリが64 KiB超、構造が4096ノード超、深さ32超の場合、本文全体を省略し `body_complete=false`、`body_omitted=true` と理由を記録する。黙った切断や分割保存は行わない。上限以下でも認証情報は秘匿される。Cloud Loggingへの配送成功は保証しない。

## 反映と無効化

変更済みコードのあるリポジトリルートから、既存デプロイ権限で実行する。古いcheckoutから実行すると旧コードがデプロイされる。下記は既存サービスを対象とし、API_TOKENなど既存環境変数を保持して診断スイッチだけ更新する。

```sh
gcloud run deploy SERVICE --source server \
  --project PROJECT --region REGION \
  --update-env-vars LOG_REQUEST_BODY=true
```

ソースデプロイにはCloud Build、Artifact Registry、ソース格納用Cloud Storageが必要。デプロイ実行者の標準ロールはprojectの `roles/run.sourceDeveloper` と `roles/serviceusage.serviceUsageConsumer`、実行SAへの `roles/iam.serviceAccountUser`。実際のビルドSAには `roles/run.builder` または同等権限が必要で、Compute Engine SAやユーザー管理SAを使う場合はそのSAへの `iam.serviceAccounts.actAs` も確認する。既定のビルドSAはproject履歴で異なるため推測しない。既存権限で不足する場合だけ管理者に確認する。

デプロイ前に旧revisionとtrafficを控える。反映後は新revisionのReady、traffic、`/health`、認証済み送信のログ、BQの心拍保存を別々に確認する。送信成功・ラン更新成功だけでは心拍保存成功を意味しない。正規化・改行分割・BQ MERGE処理は変更していない。

切り分け終了後：

```sh
gcloud run services update SERVICE --project PROJECT --region REGION \
  --update-env-vars LOG_REQUEST_BODY=false
```

## テスト

実API・実データを使わず、BQクライアントをモックして検証する。

```sh
python -m pip install -r server/requirements.txt pytest httpx
python -m pytest -q server/tests/test_body_logging.py
```

[ソースデプロイ公式手順](https://docs.cloud.google.com/run/docs/deploying-source-code)、[ビルドSAの選択と権限](https://docs.cloud.google.com/build/docs/cloud-build-service-account-updates)。
