from app.planner import plan

def test_multiple_read_steps():
    result = plan('muestra los pods en ns demo y dame los eventos')
    assert result['status'] == 'ready'
    assert result['kind'] == 'compound'
    assert [s['intent'] for s in result['steps']] == ['list_pods','list_events']
    assert [s['namespace'] for s in result['steps']] == ['demo', 'demo']
    assert [s['source'] for s in result['steps']] == ['explicit', 'propagated']
    assert result['steps'][1]['parameters'] == {'namespace': 'demo'}

def test_write_cannot_be_hidden_in_compound_request():
    result = plan('muestra los pods y elimina el pod pagos')
    assert result['status'] == 'blocked'
    assert result['steps'] == []

def test_unknown_step_aborts_all():
    result = plan('muestra los pods y dame los logs del pod pagos')
    assert result['status'] == 'unsupported'
    assert result['steps'] == []

def test_single_read():
    result = plan('muestra los pods')
    assert result['status'] == 'ready'
    assert result['kind'] == 'single'

def test_help_has_executable_plan_shape():
    result = plan('ayuda')
    assert result['status'] == 'ready'
    assert result['kind'] == 'single'
    assert result['steps'][0]['intent'] == 'help'

def test_context_namespace_is_reused():
    result = plan('dame los eventos', {'namespace':'demo'})
    assert result['steps'][0]['namespace'] == 'demo'
    assert result['steps'][0]['source'] == 'propagated'
