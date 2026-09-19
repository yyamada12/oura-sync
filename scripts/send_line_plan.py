"""Send a UTF-8 message from stdin using local, Git-ignored LINE credentials."""

import argparse
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', required=True, help='Stable delivery key; reuse on retries')
    args = parser.parse_args()
    message = sys.stdin.read().strip()
    if not message or len(message.encode('utf-16-le')) // 2 > 5000:
        raise SystemExit('Message must contain 1–5000 UTF-16 code units.')
    credentials = json.loads((Path(__file__).resolve().parents[1] / 'secrets/line-notify.json').read_text())
    if not re.fullmatch(r'U[0-9a-f]{32}', credentials['user_id']):
        raise SystemExit('Invalid LINE user ID.')
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
