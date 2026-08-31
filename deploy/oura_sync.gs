/**
 * Oura Ring → Google Sheets 同期 (Google Apps Script 版)
 *
 * Python 版 (oura_sync/) と同じシート構造で upsert する:
 * - エンドポイントごとに 1 タブ、1 行目がヘッダ
 * - A 列 `_key` が upsert キー、B 列 `_synced_at` が取り込み時刻
 * - ネストは `a.b` に平坦化、配列は JSON 文字列
 *
 * ── セットアップ ──
 * 1. 対象スプレッドシートの「拡張機能 > Apps Script」にこのファイルを貼り付け
 * 2. プロジェクトの設定 > スクリプト プロパティ に認証情報を登録
 *    OURA_CLIENT_ID / OURA_CLIENT_SECRET / OURA_REFRESH_TOKEN の 3 つ
 *    ※ Personal Access Token は deprecated で新規発行不可
 *    ※ Oura の refresh token は single-use。ローカルの tokens.json と
 *      共有すると片方が壊れるので、GAS 専用に認可した refresh token を使うこと
 *      (OURA_ACCESS_TOKEN に有効な長寿命トークンがあればそれも使える)
 * 3. エディタ左の「サービス +」から BigQuery API を追加する
 *    (heartrate / ring_battery_level は Sheets ではなく BigQuery
 *     `oura-data-yy.oura` に書き込む。実行アカウントに IAM 付与済み)
 * 4. syncAll を一度手動実行して動作確認 (初回は権限承認ダイアログが出る)
 * 5. setupDailyTrigger を一度実行 → 毎朝 8 時台に自動実行される
 *
 * スクリプト プロパティはシートの閲覧者には見えない (スクリプトの編集権限を
 * 持つ人だけが見られる)。シートを共有する場合もトークンは漏れない。
 */

const API_BASE = 'https://api.ouraring.com/v2/usercollection';
const TOKEN_URL = 'https://api.ouraring.com/oauth/token';
const SYNC_DAYS = 7; // 直近何日分を毎回取り直すか
const META_COLS = ['_key', '_synced_at'];

// oura_client.py の ENDPOINTS と同一
const ENDPOINTS = [
  { name: 'personal_info', mode: 'single', key: ['id'] },
  { name: 'daily_activity', mode: 'date', key: ['id'] },
  { name: 'daily_readiness', mode: 'date', key: ['id'] },
  { name: 'daily_sleep', mode: 'date', key: ['id'] },
  { name: 'daily_spo2', mode: 'date', key: ['id'] },
  { name: 'daily_stress', mode: 'date', key: ['id'] },
  { name: 'daily_resilience', mode: 'date', key: ['id'] },
  { name: 'daily_cardiovascular_age', mode: 'date', key: ['id'] },
  { name: 'sleep', mode: 'date', key: ['id'] },
  { name: 'sleep_time', mode: 'date', key: ['id'] },
  { name: 'workout', mode: 'date', key: ['id'] },
  { name: 'session', mode: 'date', key: ['id'] },
  { name: 'enhanced_tag', mode: 'date', key: ['id'] },
  { name: 'rest_mode_period', mode: 'date', key: ['id'] },
  { name: 'vO2_max', mode: 'date', key: ['id'] },
  { name: 'ring_configuration', mode: 'none', key: ['id'] },
  // 高頻度データは Sheets ではなく BigQuery へ (bq.fields が格納カラム)
  {
    name: 'heartrate',
    mode: 'datetime',
    key: ['timestamp', 'source'],
    bq: { fields: ['timestamp', 'source', 'bpm', 'producer_timestamp'] },
  },
  {
    name: 'ring_battery_level',
    mode: 'datetime',
    key: ['timestamp'],
    bq: { fields: ['timestamp', 'level', 'charging', 'in_charger', 'producer_timestamp'] },
  },
];

const BQ_PROJECT = 'oura-data-yy';
const BQ_DATASET = 'oura';

/** メインエントリ。トリガーにはこれを登録する */
function syncAll() {
  const end = new Date();
  const start = new Date(end.getTime() - SYNC_DAYS * 24 * 3600 * 1000);
  const book = getBook_();
  for (const ep of ENDPOINTS) {
    const docs = fetchEndpoint_(ep, start, end);
    if (ep.bq) {
      const loaded = bqLoadPartitions_(ep, docs);
      console.log(`[${ep.name}] BQ loaded=${loaded}`);
    } else {
      const [updated, added] = upsert_(book, ep.name, docs, ep.key);
      console.log(`[${ep.name}] updated=${updated} added=${added}`);
    }
  }
}

/** 毎朝 8 時台の実行トリガーを登録 (多重登録は自動で掃除)
 *  起床が 7 時台でアプリへの反映を待ってから取り込むため 8 時 */
function setupDailyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter((t) => t.getHandlerFunction() === 'syncAll')
    .forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('syncAll').timeBased().everyDays(1).atHour(8).create();
  console.log('毎日 8 時台に syncAll を実行するトリガーを登録しました');
}

// ───────────────────────── 認証 ─────────────────────────

function getAccessToken_() {
  const props = PropertiesService.getScriptProperties();
  const pat = props.getProperty('OURA_ACCESS_TOKEN');
  if (pat) return pat;

  // OAuth モード: access token をキャッシュし、期限切れなら refresh
  const cached = props.getProperty('OURA_OAUTH_CACHE');
  if (cached) {
    const c = JSON.parse(cached);
    if (Date.now() / 1000 < c.expires_at) return c.access_token;
  }
  const refreshToken = props.getProperty('OURA_REFRESH_TOKEN');
  if (!refreshToken) {
    throw new Error(
      'スクリプト プロパティに OURA_ACCESS_TOKEN (PAT) か OURA_REFRESH_TOKEN 一式を設定してください'
    );
  }
  const res = UrlFetchApp.fetch(TOKEN_URL, {
    method: 'post',
    payload: {
      grant_type: 'refresh_token',
      refresh_token: refreshToken,
      client_id: props.getProperty('OURA_CLIENT_ID'),
      client_secret: props.getProperty('OURA_CLIENT_SECRET'),
    },
    muteHttpExceptions: true,
  });
  if (res.getResponseCode() >= 400) {
    throw new Error(`token refresh 失敗 ${res.getResponseCode()}: ${res.getContentText()}`);
  }
  const tok = JSON.parse(res.getContentText());
  // refresh token は single-use。新しいものを必ず即保存する
  props.setProperty('OURA_REFRESH_TOKEN', tok.refresh_token);
  props.setProperty(
    'OURA_OAUTH_CACHE',
    JSON.stringify({
      access_token: tok.access_token,
      expires_at: Math.floor(Date.now() / 1000) + (tok.expires_in || 86400) - 60,
    })
  );
  return tok.access_token;
}

// ─────────────────────── Oura API 取得 ───────────────────────

function ouraGet_(path, params) {
  const qs = Object.entries(params)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  const url = `${API_BASE}/${path}` + (qs ? `?${qs}` : '');
  for (let attempt = 0; attempt < 5; attempt++) {
    const res = UrlFetchApp.fetch(url, {
      headers: { Authorization: `Bearer ${getAccessToken_()}` },
      muteHttpExceptions: true,
    });
    const code = res.getResponseCode();
    if (code === 429) {
      const wait = Number(res.getHeaders()['Retry-After'] || 30);
      Utilities.sleep(Math.min(wait, 60) * 1000);
      continue;
    }
    if (code >= 500) {
      Utilities.sleep(2 ** attempt * 1000);
      continue;
    }
    if (code >= 400) throw new Error(`${path} ${code}: ${res.getContentText()}`);
    return JSON.parse(res.getContentText());
  }
  throw new Error(`${path}: リトライ上限`);
}

function fetchEndpoint_(ep, start, end) {
  if (ep.mode === 'single') return [ouraGet_(ep.name, {})];

  const params = {};
  if (ep.mode === 'date') {
    params.start_date = isoDate_(start);
    params.end_date = isoDate_(end);
  } else if (ep.mode === 'datetime') {
    params.start_datetime = isoDate_(start) + 'T00:00:00+00:00';
    const next = new Date(end.getTime() + 24 * 3600 * 1000);
    params.end_datetime = isoDate_(next) + 'T00:00:00+00:00';
  }

  const out = [];
  let nextToken = null;
  do {
    const p = Object.assign({}, params);
    if (nextToken) p.next_token = nextToken;
    const body = ouraGet_(ep.name, p);
    out.push(...(body.data || []));
    nextToken = body.next_token;
  } while (nextToken);
  return out;
}

function isoDate_(d) {
  return Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
}

// ─────────────────────── BigQuery load ───────────────────────
// 高頻度データ (heartrate 等) は Sheets ではなく BigQuery に入れる。
// 同期窓は常に丸1日単位なので、UTC 日付パーティションごとに
// WRITE_TRUNCATE でロード = その日を丸ごと洗い替え。何度実行しても重複しない。
// DML (MERGE) はサンドボックス (課金未リンク) では使えず、ロードジョブは
// 課金後も無料なのでこの方式にしている。
// ※ Apps Script エディタの「サービス +」から BigQuery API を追加しておくこと

function bqLoadPartitions_(ep, docs) {
  if (!docs.length) return 0;
  const syncedAt = new Date().toISOString();

  // UTC 日付ごとに NDJSON をまとめる
  const byDate = new Map();
  for (const d of docs) {
    const row = { synced_at: syncedAt };
    for (const f of ep.bq.fields) {
      if (d[f] !== undefined && d[f] !== null) row[f] = d[f];
    }
    const part = String(d.timestamp).slice(0, 10).replace(/-/g, ''); // yyyyMMdd
    if (!byDate.has(part)) byDate.set(part, []);
    byDate.get(part).push(JSON.stringify(row));
  }

  const schema = BigQuery.Tables.get(BQ_PROJECT, BQ_DATASET, ep.name).schema;
  for (const [part, lines] of byDate) {
    const job = BigQuery.Jobs.insert(
      {
        configuration: {
          load: {
            destinationTable: {
              projectId: BQ_PROJECT,
              datasetId: BQ_DATASET,
              tableId: `${ep.name}$${part}`,
            },
            sourceFormat: 'NEWLINE_DELIMITED_JSON',
            schema: schema,
            writeDisposition: 'WRITE_TRUNCATE',
          },
        },
      },
      BQ_PROJECT,
      Utilities.newBlob(lines.join('\n'), 'application/octet-stream')
    );
    waitForBqJob_(job.jobReference);
  }
  return docs.length;
}

function waitForBqJob_(jobRef) {
  // データセットが asia-northeast1 にあるため、Jobs.get には location 必須
  // (省略すると US を探して Not found になる)
  for (let i = 0; i < 60; i++) {
    const st = BigQuery.Jobs.get(BQ_PROJECT, jobRef.jobId, { location: jobRef.location });
    if (st.status.state === 'DONE') {
      if (st.status.errorResult) throw new Error(JSON.stringify(st.status.errorResult));
      return;
    }
    Utilities.sleep(2000);
  }
  throw new Error(`BigQuery job ${jobRef.jobId} が完了しません`);
}

// ─────────────────────── Sheets upsert ───────────────────────

function getBook_() {
  const id = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  if (id) return SpreadsheetApp.openById(id);
  const active = SpreadsheetApp.getActiveSpreadsheet();
  if (!active) {
    throw new Error(
      'コンテナバインドでない場合はスクリプト プロパティに SPREADSHEET_ID を設定してください'
    );
  }
  return active;
}

function flatten_(obj, prefix) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    const name = (prefix || '') + k;
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      Object.assign(out, flatten_(v, name + '.'));
    } else if (Array.isArray(v)) {
      out[name] = JSON.stringify(v);
    } else {
      out[name] = v;
    }
  }
  return out;
}

function upsert_(book, tab, docs, keyFields) {
  if (!docs.length) return [0, 0];
  let ws = book.getSheetByName(tab);
  if (!ws) {
    ws = book.insertSheet(tab);
    ws.getRange(1, 1, 1, META_COLS.length).setValues([META_COLS]);
  }
  const now = new Date().toISOString().replace(/\.\d+Z$/, '+00:00');

  const rows = new Map();
  for (const d of docs) {
    const flat = flatten_(d);
    flat._key = keyFields.map((f) => String(d[f] != null ? d[f] : '')).join('|');
    flat._synced_at = now;
    rows.set(flat._key, flat);
  }

  // ヘッダ読み込みと新規列の拡張
  let header =
    ws.getLastColumn() > 0
      ? ws.getRange(1, 1, 1, ws.getLastColumn()).getValues()[0].filter(String)
      : [];
  if (!header.length) header = META_COLS.slice();
  const known = new Set(header);
  const newCols = [...new Set([].concat(...[...rows.values()].map(Object.keys)))]
    .filter((c) => !known.has(c))
    .sort();
  if (newCols.length) {
    header = header.concat(newCols);
    if (ws.getMaxColumns() < header.length) {
      ws.insertColumnsAfter(ws.getMaxColumns(), header.length - ws.getMaxColumns());
    }
    ws.getRange(1, 1, 1, header.length).setValues([header]);
  }

  // A 列 → 行番号の索引
  const lastRow = ws.getLastRow();
  const index = new Map();
  if (lastRow > 1) {
    const keys = ws.getRange(2, 1, lastRow - 1, 1).getValues();
    keys.forEach((r, i) => index.set(String(r[0]), i + 2));
  }

  const toRow = (flat) =>
    header.map((c) => {
      const v = flat[c];
      if (v === undefined || v === null) return '';
      if (typeof v === 'boolean') return String(v);
      return v;
    });

  const updates = []; // {row, values}
  const appends = [];
  for (const [k, flat] of rows) {
    if (index.has(k)) updates.push({ row: index.get(k), values: toRow(flat) });
    else appends.push(toRow(flat));
  }

  // 更新行は連続ブロックにまとめて書き込む (heartrate の数千行更新対策)
  updates.sort((a, b) => a.row - b.row);
  let i = 0;
  while (i < updates.length) {
    let j = i;
    while (j + 1 < updates.length && updates[j + 1].row === updates[j].row + 1) j++;
    const block = updates.slice(i, j + 1).map((u) => u.values);
    ws.getRange(updates[i].row, 1, block.length, header.length).setValues(block);
    i = j + 1;
  }
  if (appends.length) {
    ws.getRange(ws.getLastRow() + 1, 1, appends.length, header.length).setValues(appends);
  }
  return [updates.length, appends.length];
}
