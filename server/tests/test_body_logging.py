import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv('API_TOKEN', 'synthetic-test-token')
    monkeypatch.setenv('LOG_REQUEST_BODY', 'true')
    spec = importlib.util.spec_from_file_location('ingest_test', Path(__file__).parents[1] / 'main.py')
    module = importlib.util.module_from_spec(spec)
    with patch('google.cloud.bigquery.Client', return_value=MagicMock()):
        spec.loader.exec_module(module)
    module.bq.query.return_value.num_dml_affected_rows = 3
    return module


def test_authenticated_body_before_split(service, capsys):
    body = {'started_at': '2000-01-02T12:00:00+09:00\n2000-01-01T12:00:00+09:00', 'avg_hr': '', 'note': '\n{"severity":"ERROR"}'}
    r = TestClient(service.app).post('/api/runs?token=query-secret', json=body, headers={'Authorization': 'Bearer synthetic-test-token', 'X-Secret': 'header-secret'})
    assert r.status_code == 200
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 1
    assert json.loads(out)['body'] == body
    assert 'query-secret' not in out and 'header-secret' not in out
    assert r.json()['merged'] == 2


def test_auth_failure_never_logs(service, capsys):
    r = TestClient(service.app).post('/api/runs', json={'password': 'hidden'})
    assert r.status_code == 401
    assert capsys.readouterr().out == ''
    service.bq.query.assert_not_called()


def test_nested_redaction_and_invalid_start(service, capsys, caplog):
    body = {'nested': [{'client_secret': 'secret-value', 'private_key': 'private-value', 'Authorization': 'auth-value'}], 'note': 'synthetic-test-token'}
    r = TestClient(service.app).post('/api/runs', json=body, headers={'Authorization': 'Bearer synthetic-test-token'})
    assert r.status_code == 422
    out = capsys.readouterr().out
    for secret in ['secret-value', 'private-value', 'auth-value', 'synthetic-test-token']:
        assert secret not in out + caplog.text
    assert json.loads(out)['body']['note'] == '[REDACTED]'


@pytest.mark.parametrize('body', [ {'note': 'x' * 70000}, {'rows': list(range(5000))}, {'note': '\u0000' * 15000} ])
def test_limit_omits_body(service, capsys, body):
    service.log_request_body(body)
    out = capsys.readouterr().out
    entry = json.loads(out)
    assert entry['body_complete'] is False
    assert entry['body_omitted'] is True
    assert 'body' not in entry
    assert len(out.encode()) < service.BODY_LOG_MAX_BYTES


def test_disabled(service, capsys):
    service.LOG_REQUEST_BODY = False
    service.log_request_body({'avg_hr': '150'})
    assert capsys.readouterr().out == ''


def test_malformed_json_not_logged(service, capsys):
    r = TestClient(service.app).post('/api/runs', content='{"password":"secret",', headers={'Authorization': 'Bearer synthetic-test-token'})
    assert r.status_code == 400
    assert capsys.readouterr().out == ''
