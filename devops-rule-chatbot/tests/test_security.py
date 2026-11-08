from app.security import scan_content


def test_clean_content_passes_secret_scan():
    result = scan_content('apiVersion: v1\nkind: Service\nmetadata:\n  name: api\n')
    assert result['clean'] is True
    assert result['scanner'] == 'gitleaks'


def test_private_key_is_detected_and_redacted():
    result = scan_content('-----BEGIN RSA PRIVATE KEY-----\nfake-secret\n-----END RSA PRIVATE KEY-----\n', 'key.pem')
    assert result['clean'] is False
    assert result['findings']
    assert all(item['match'] == '[REDACTED]' for item in result['findings'])
