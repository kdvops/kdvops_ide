from fastapi.testclient import TestClient

from app.main import app, sessions


client = TestClient(app)


def setup_function():
    sessions.clear()


def test_health_reports_demo_mode():
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'ok': True, 'demo': True}


def test_help_plan_is_ready():
    body = client.post('/api/chat', json={'message': 'ayuda'}).json()
    assert body['status'] == 'single'
    assert body['plan']['status'] == 'ready'


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


def test_client_session_id_can_be_created_and_reused():
    session_id = 'kdvops-client-session-1234'
    first = client.post('/api/chat', json={'message': 'muestra los pods en ns demo', 'session_id': session_id}).json()
    second = client.post('/api/chat', json={'message': 'dame los eventos', 'session_id': session_id}).json()
    assert first['session_id'] == session_id
    assert second['session_id'] == session_id
    assert second['plan']['steps'][0]['namespace'] == 'demo'


def test_generate_kubernetes_manifest_endpoint():
    response = client.post('/api/generate', json={
        'kind': 'kubernetes_manifest',
        'message': 'crea un manifiesto kubernetes llamado api en ns demo con 2 replicas',
    })
    body = response.json()
    assert response.status_code == 200
    assert body['status'] == 'generated'
    assert body['artifact']['filename'] == 'api.yaml'
    assert body['scan']['clean'] is True


def test_analyze_endpoint():
    response = client.post('/api/analyze', json={
        'kind': 'kubernetes_manifest',
        'content': 'apiVersion: v1\nkind: Service\nmetadata:\n  name: api\n',
    })
    assert response.status_code == 200
    assert response.json()['analysis']['valid'] is True


def test_generate_blocks_secret_in_direct_request():
    response = client.post('/api/generate', json={
        'kind': 'kubernetes_manifest',
        'message': 'crea un manifiesto con token=secret-value-123456789',
    })
    assert response.status_code == 200
    assert response.json()['status'] == 'blocked'


def test_analyze_blocks_secret_in_direct_request():
    response = client.post('/api/analyze', json={
        'kind': 'kubernetes_manifest',
        'content': 'apiVersion: v1\nkind: ConfigMap\ndata:\n  token: secret-value-123456789\n',
    })
    assert response.status_code == 200
    assert response.json()['status'] == 'blocked'


def test_security_scan_endpoint_redacts_findings():
    response = client.post('/api/security/scan', json={
        'filename': 'config.pem',
        'content': '-----BEGIN RSA PRIVATE KEY-----\nfake-secret\n-----END RSA PRIVATE KEY-----\n',
    })
    body = response.json()
    assert response.status_code == 200
    assert body['status'] == 'blocked'
    assert body['scan']['findings'][0]['match'] == '[REDACTED]'


def test_chat_blocks_secret_before_processing():
    response = client.post('/api/chat', json={
        'message': 'token=ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    })
    body = response.json()
    assert response.status_code == 200
    assert body['status'] == 'blocked'
    assert body['scan']['findings'][0]['match'] == '[REDACTED]'


def test_devsecops_evaluation_scores_pipeline_controls():
    response = client.post('/api/devsecops/evaluate', json={
        'filename': 'azure-pipelines.yml',
        'content': '''steps:\n- script: pytest\n- script: semgrep --config auto\n- script: gitleaks detect\n- script: trivy image app:1.0\n- script: zap-baseline.py\n- script: syft app:1.0 -o cyclonedx\n''',
    })
    body = response.json()
    assert response.status_code == 200
    assert body['status'] == 'evaluated'
    assert body['evaluation']['score'] >= 30
    assert body['evaluation']['level'] in {'developing', 'managed', 'advanced'}
    assert body['evaluation']['validation_summary']['total'] >= 8
    assert any(item['key'] == 'artifact_flow' for item in body['evaluation']['validations'])


def test_devsecops_detects_kubernetes_sealed_secrets():
    response = client.post('/api/devsecops/evaluate', json={
        'filename': 'sealed-secret.yaml',
        'content': 'apiVersion: bitnami.com/v1alpha1\nkind: SealedSecret\nspec:\n  encryptedData: {}\n',
    })
    body = response.json()
    assert response.status_code == 200
    sealed = next(item for item in body['evaluation']['validations'] if item['key'] == 'sealed_secrets')
    assert sealed['status'] == 'passed'
    assert 'sealedsecret' in ''.join(sealed['evidence']).lower()


def test_devsecops_evaluation_blocks_secret():
    response = client.post('/api/devsecops/evaluate', json={
        'content': 'steps:\n- script: token=secret-value-123456789\n',
    })
    assert response.status_code == 200
    assert response.json()['status'] == 'blocked'


def test_devsecops_evaluates_multiple_files_as_one_flow():
    response = client.post('/api/devsecops/evaluate', json={
        'files': [
            {'filename': 'azure-pipelines.yml', 'content': 'steps:\n- script: pytest\n'},
            {'filename': 'security.yml', 'content': 'steps:\n- script: gitleaks detect\n- script: semgrep\n'},
        ],
    })
    body = response.json()
    assert response.status_code == 200
    assert body['status'] == 'evaluated'
    assert body['evaluation']['file_count'] == 2
    assert body['evaluation']['score'] == 21
