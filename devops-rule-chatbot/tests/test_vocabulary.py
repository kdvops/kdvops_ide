from app.nlu import parse, CATALOG

def test_catalog_domains():
    assert len(CATALOG['domains']) >= 10
    assert sum(map(len, CATALOG['domains'].values())) >= 200

def test_typos():
    assert parse('enseñame los pods')['intent'] == 'list_pods'

def test_unsupported_is_not_fake_success():
    r = parse('muestra los logs del pod')
    assert r['status'] in ('unsupported','ambiguous')

def test_no_mutation():
    assert parse('reinicia el deployment')['status'] in ('unsupported','unknown')
