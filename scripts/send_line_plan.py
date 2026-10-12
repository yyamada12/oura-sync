"""Send a UTF-8 message from stdin using Secret Manager or local, Git-ignored LINE credentials."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
LOCAL_CREDENTIALS = ROOT / 'secrets/line-notify.json'
sys.path.insert(0, str(ROOT))


def secret_name():
    """LINE_TOKEN_SECRET を環境変数から、無ければ .env から読む。"""
    name = os.environ.get('LINE_TOKEN_SECRET', '')
    if name:
        return name
    try:
        from oura_sync import config
    except ImportError:
        # stdlib だけの python (例: /usr/bin/python3) ではローカル鍵のみ対応
        return ''
    return config.LINE_TOKEN_SECRET


def load_credentials():
    """Secret Manager (SA 権限のみで可) を優先し、無ければ Git 対象外のローカル JSON。"""
    name = secret_name()
    if not name:
        return json.loads(LOCAL_CREDENTIALS.read_text())
    from oura_sync import secret_store

    credentials = secret_store.load(name)
    if not credentials:
        raise SystemExit('No secret version in {}. Add one; no credentials logged.'.format(name))
    return credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', required=True, help='Stable delivery key; reuse on retries')
    args = parser.parse_args()
    message = sys.stdin.read().strip()
    if not message or len(message.encode('utf-16-le')) // 2 > 5000:
        raise SystemExit('Message must contain 1–5000 UTF-16 code units.')
    credentials = load_credentials()
    if not re.fullmatch(r'U[0-9a-f]{32}', credentials.get('user_id', '')):
        raise SystemExit('Invalid LINE user ID.')
    if not credentials.get('channel_access_token'):
        raise SystemExit('Missing channel_access_token in LINE credentials.')
    retry_key = str(uuid.uuid5(uuid.NAMESPACE_URL, credentials['user_id'] + ':' + args.key))
    request = urllib.request.Request(
        'https://api.line.me/v2/bot/message/push',
        data=json.dumps({'to': credentials['user_id'], 'messages': [{'type': 'text', 'text': message}]}).encode(),
        headers={'Authorization': 'Bearer ' + credentials['channel_access_token'],
                 'Content-Type': 'application/json', 'X-Line-Retry-Key': retry_key},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print('LINE API accepted message (HTTP {}). Delivery to device is not verified.'.format(response.status))
    except urllib.error.HTTPError as error:
        if error.code == 409 and error.headers.get('x-line-accepted-request-id'):
            print('LINE API already accepted this delivery key; no duplicate sent.')
        else:
            raise SystemExit('LINE API failed: HTTP {}. No credentials logged.'.format(error.code))
    except urllib.error.URLError:
        raise SystemExit('LINE network error. Retry using the same --key.')


if __name__ == '__main__':
    main()
