from app.generators import analyze_yaml, generate_azure_pipeline, generate_kubernetes_manifest


def test_kubernetes_manifest_generation_is_deterministic():
    artifact = generate_kubernetes_manifest(
        'crea un manifiesto kubernetes llamado payments en namespace production con 3 replicas, imagen ghcr.io/acme/payments:1.2.0 y puerto 8080'
    )
    assert artifact.kind == 'kubernetes_manifest'
    assert artifact.filename == 'payments.yaml'
    assert 'replicas: 3' in artifact.content
    assert 'namespace: production' in artifact.content
    assert 'image: ghcr.io/acme/payments:1.2.0' in artifact.content


def test_azure_pipeline_generation_contains_validation_build_and_deploy():
    artifact = generate_azure_pipeline('crea un azure pipeline para una app python llamada payments')
    assert artifact.filename == 'azure-pipelines.yml'
    assert 'pytest -q' in artifact.content
    assert 'Docker@2' in artifact.content
    assert 'KubernetesManifest@1' in artifact.content


def test_kubernetes_analysis_detects_missing_fields():
    result = analyze_yaml('kind: Deployment\nmetadata:\n  name: payments\n', 'kubernetes_manifest')
    assert result['valid'] is False
    assert any('apiVersion' in error for error in result['errors'])


def test_kubernetes_analysis_accepts_generated_manifest():
    artifact = generate_kubernetes_manifest('crea un manifiesto kubernetes llamado api en ns demo con imagen api:1.0')
    result = analyze_yaml(artifact.content, 'kubernetes_manifest')
    assert result['valid'] is True
    assert result['documents'] == 2
