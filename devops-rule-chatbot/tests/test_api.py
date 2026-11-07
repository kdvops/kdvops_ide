from fastapi.testclient import TestClient

from app.main import app, sessions


client = TestClient(app)


def setup_function():
    sessions.clear()


def test_health_reports_demo_mode():
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'ok': True, 'demo': True}


def test_compound_response_propagates_namespace():
    response = client.post('/api/chat', json={'message': 'muestra los pods en ns demo y dame los eventos'})
    body = response.json()
    assert response.status_code == 200
    assert body['status'] == 'compound'
    assert [item['namespace'] for item in body['results']] == ['demo', 'demo']
    assert body['plan']['steps'][1]['source'] == 'propagated'


def test_write_request_is_blocked():
    response = client.post('/api/chat', json={'message': 'muestra los pods y elimina el pod pagos'})
    body = response.json()
    assert response.status_code == 200
    assert body['status'] == 'blocked'
    assert body['plan']['steps'] == []


def test_session_namespace_is_reused():
    first = client.post('/api/chat', json={'message': 'muestra los pods en ns demo'}).json()
    second = client.post('/api/chat', json={'message': 'dame los eventos', 'session_id': first['session_id']}).json()
    assert second['status'] == 'single'
    assert second['plan']['steps'][0]['namespace'] == 'demo'
