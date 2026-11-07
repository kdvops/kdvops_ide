from app.nlu import parse

def test_pods():
    result = parse('Muéstrame los pods')
    assert result['intent'] == 'list_pods'

def test_failing():
    result = parse('que pods estan fallando')
    assert result['intent'] == 'failing_pods'

def test_namespace():
    assert parse('listar pods en ns demo')['namespace'] == 'demo'

def test_unknown():
    assert parse('quiero comprar una pizza')['status'] == 'unknown'
