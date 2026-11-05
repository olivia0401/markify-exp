"""Exercise the script/client contract without credentials or paid requests."""
import importlib

import pytest
import requests

from src import markify_client as api


def client_with_pages(monkeypatch, pages):
    client = api.MarkifyClient('synthetic-key', rate_limit_seconds=0)
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs['params'])
        payload = next(pages)
        return type('Response', (), {'status_code': 200, 'json': lambda self: payload})()

    monkeypatch.setattr(client._session, 'get', get)
    return client, calls


@pytest.mark.parametrize('name', ['scripts.run_experiment_1', 'scripts.run_experiment_2',
                                 'scripts.run_experiment_2_variance', 'scripts.test_call'])
def test_entrypoints_import_without_live_calls(name):
    importlib.import_module(name)


def test_distance_runner_aggregates_pages(monkeypatch):
    from scripts.run_experiment_1 import call_one_db

    client, calls = client_with_pages(monkeypatch, iter([
        {'meta': {'totalFound': 101}, 'result': [{'mark': 'one'}] * 100},
        {'meta': {'totalFound': 101}, 'result': [{'mark': 'last'}]},
    ]))
    rows, summary = call_one_db(client, 'Apple', '9', 'US')
    assert len(rows) == 101 and rows[-1]['related_trademark'] == 'last'
    assert summary['pages'] == 2 and summary['status'] == 'ok'
    assert [call['startIndex'] for call in calls] == [0, 100]
    assert calls[0]['database'] == 'USPTO'
    assert client.call_count == 2


def test_page_cap_reports_truncation(monkeypatch):
    client, _ = client_with_pages(monkeypatch, iter([
        {'meta': {'totalFound': 2}, 'result': [{'mark': 'first'}]},
    ]))
    result = client.search_all('A', '9', databases='EU', page_limit=1, max_pages=1)
    assert result.status == 'ok_truncated' and result.pages_fetched == 1


def test_short_server_pages_do_not_skip_results(monkeypatch):
    client, calls = client_with_pages(monkeypatch, iter([
        {'meta': {'totalFound': 2}, 'result': [{'mark': 'first'}]},
        {'meta': {'totalFound': 2}, 'result': [{'mark': 'last'}]},
    ]))
    result = client.search_all('A', '9', databases='US', page_limit=100)
    assert result.status == 'ok' and len(result.data['result']) == 2
    assert [call['startIndex'] for call in calls] == [0, 1]


def test_timing_wrapper_maps_arguments(monkeypatch):
    client, calls = client_with_pages(monkeypatch, iter([{'result': []}]))
    monkeypatch.setattr(api, '_default_client', client)
    result = api.search('Apple', '9', databases='US,EU', min_score=0.25, page_limit=100)
    assert result.status == 'ok' and result.http_code == 200
    assert calls[0]['database'] == 'USPTO,EUIPO'
    assert calls[0]['minScore'] == 0.25 and calls[0]['pageLimit'] == 100


def test_timeout_attempts_consume_call_cap(monkeypatch):
    client = api.MarkifyClient('synthetic-key', call_cap=1, rate_limit_seconds=0)
    def timeout(*args, **kwargs):
        raise requests.Timeout('URL contains synthetic-key')
    monkeypatch.setattr(client._session, 'get', timeout)
    monkeypatch.setattr(api.time, 'sleep', lambda _: None)
    with pytest.raises(api.CallCapReachedError):
        client._result(mark='A', classes='9', database='US')
    assert client.call_count == 1


def test_failed_response_does_not_expose_body_or_key(monkeypatch):
    client = api.MarkifyClient('synthetic-key', rate_limit_seconds=0)
    response = type('Response', (), {'status_code': 500, 'text': 'synthetic-key'})()
    monkeypatch.setattr(client._session, 'get', lambda *a, **kw: response)
    result = client._result(mark='A', classes='9', database='US')
    assert result.status == 'http_error'
    assert 'synthetic-key' not in result.error
