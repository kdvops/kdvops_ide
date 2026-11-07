import pytest
from app.nlu import parse

@pytest.mark.parametrize('message,intent,status', [
    ('Muéstrame los pods', 'list_pods', 'matched'),
    ('listar pods en ns demo', 'list_pods', 'matched'),
    ('Enseñame los pod que fallan', 'failing_pods', 'matched'),
    ('ver pods con errores', 'failing_pods', 'matched'),
    ('muestra los deployments', 'list_deployments', 'matched'),
    ('lista los namespaces', 'list_namespaces', 'matched'),
    ('dame los eventos', 'list_events', 'matched'),
    ('ayuda', 'help', 'matched'),
    ('dame los logs del pod pagos', 'get_pod_logs', 'unsupported'),
    ('estado del deployment pagos', 'get_deployment_status', 'unsupported'),
    ('que alertas estan activas', 'get_alerts', 'unsupported'),
    ('Reinicia el deployment pagos', 'restart_deployment', 'unsupported'),
    ('elimina el pod pagos', 'delete_resource', 'unsupported'),
])
def test_classification(message, intent, status):
    result = parse(message)
    assert (result['intent'], result['status']) == (intent, status)

def test_namespace_and_entity():
    r = parse('listar pods en ns demo')
    assert r['namespace'] == 'demo'
    assert parse('dame los logs del pod pagos')['entity'] == 'pagos'

def test_context_reference():
    r = parse('dame los logs del primero', {'last_items':[{'name':'pagos-123','namespace':'demo'}]})
    assert r['entity'] == 'pagos-123'
    assert r['namespace'] == 'demo'

def test_unknown():
    assert parse('quiero comprar una pizza')['status'] == 'unknown'
