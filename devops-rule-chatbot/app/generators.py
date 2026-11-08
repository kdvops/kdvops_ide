"""Deterministic DevOps artifact generation and analysis.

The module intentionally uses bounded templates instead of generated shell or
YAML from a model. Unsupported requests return an explicit explanation.
"""

import re
from dataclasses import dataclass

import yaml


NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class Artifact:
    kind: str
    filename: str
    content: str
    warnings: list[str]


def _name(value: str | None, default: str) -> str:
    candidate = (value or default).lower().strip()
    candidate = re.sub(r"[^a-z0-9.-]+", "-", candidate).strip("-.")
    if not candidate or not NAME_RE.fullmatch(candidate):
        raise ValueError("name must contain only lowercase Kubernetes-compatible characters")
    return candidate


def _extract_name(message: str, default: str) -> str:
    match = re.search(r"\b(?:llamad[oa]|llamado|nombre|name)\s+([a-zA-Z0-9._-]+)", message, re.I)
    return _name(match.group(1) if match else None, default)


def _extract_namespace(message: str) -> str:
    match = re.search(r"\b(?:namespace|ns)\s+([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\b", message, re.I)
    return _name(match.group(1) if match else None, "default")


def _extract_int(message: str, patterns: tuple[str, ...], default: int, minimum: int, maximum: int) -> int:
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            value = int(match.group(1))
            if not minimum <= value <= maximum:
                raise ValueError(f"value must be between {minimum} and {maximum}")
            return value
    return default


def generate_kubernetes_manifest(message: str) -> Artifact:
    name = _extract_name(message, "app")
    namespace = _extract_namespace(message)
    replicas = _extract_int(message, (
        r"\b(?:replicas?|réplicas?)\s*[:=]?\s*(\d+)",
        r"\b(\d+)\s+(?:replicas?|réplicas?)\b",
    ), 2, 1, 100)
    port = _extract_int(message, (r"\b(?:puerto|port)\s*[:=]?\s*(\d+)",), 8080, 1, 65535)
    image_match = re.search(r"\b(?:imagen|image)\s*[:=]?\s*([\w./:-]+)", message, re.I)
    image = image_match.group(1) if image_match else f"{name}:latest"
    if image.endswith(":latest"):
        warnings = ["The image uses the mutable :latest tag; pin an immutable version for production."]
    else:
        warnings = []
    content = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  namespace: {namespace}
  labels:
    app.kubernetes.io/name: {name}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app.kubernetes.io/name: {name}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: {name}
    spec:
      containers:
        - name: {name}
          image: {image}
          ports:
            - name: http
              containerPort: {port}
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: {name}
  namespace: {namespace}
spec:
  selector:
    app.kubernetes.io/name: {name}
  ports:
    - name: http
      port: {port}
      targetPort: http
  type: ClusterIP
"""
    return Artifact("kubernetes_manifest", f"{name}.yaml", content, warnings)


def generate_azure_pipeline(message: str) -> Artifact:
    name = _extract_name(message, "app")
    language = "node" if re.search(r"\b(?:node|nodejs|npm)\b", message, re.I) else "python"
    manifest_path_match = re.search(r"\b(?:manifiesto|manifest|path|ruta)\s+([\w./-]+\.ya?ml)", message, re.I)
    manifest_path = manifest_path_match.group(1) if manifest_path_match else "k8s/deployment.yaml"
    if language == "node":
        setup = """      - script: npm ci
        displayName: Install dependencies
      - script: npm test --if-present
        displayName: Run tests
"""
    else:
        setup = """      - task: UsePythonVersion@0
        inputs:
          versionSpec: '3.12'
        displayName: Use Python
      - script: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pytest -q
        displayName: Install dependencies and run tests
"""
    content = f"""# Generated deterministic Azure DevOps pipeline for {name}
trigger:
  branches:
    include:
      - main

pr:
  branches:
    include:
      - main

pool:
  vmImage: ubuntu-latest

variables:
  imageRepository: {name}
  dockerfilePath: Dockerfile
  manifestPath: {manifest_path}

stages:
  - stage: Validate
    displayName: Validate
    jobs:
      - job: Tests
        steps:
          - checkout: self
{setup}
  - stage: Build
    displayName: Build container
    dependsOn: Validate
    jobs:
      - job: DockerBuild
        steps:
          - checkout: self
          - task: Docker@2
            displayName: Build image
            inputs:
              command: build
              repository: $(imageRepository)
              Dockerfile: $(dockerfilePath)
              tags: |
                $(Build.BuildId)
                latest

  - stage: Deploy
    displayName: Deploy to Kubernetes
    dependsOn: Build
    condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))
    jobs:
      - deployment: Kubernetes
        environment: {name}
        strategy:
          runOnce:
            deploy:
              steps:
                - task: KubernetesManifest@1
                  displayName: Apply Kubernetes manifest
                  inputs:
                    action: deploy
                    manifests: $(manifestPath)
"""
    return Artifact("azure_pipeline", "azure-pipelines.yml", content, [
        "Configure a Docker registry service connection before pushing images.",
        "Configure a Kubernetes service connection and environment before deployment.",
        "Review image tags and deployment policy before using in production.",
    ])


def analyze_yaml(content: str, kind: str | None = None) -> dict:
    if not content.strip():
        raise ValueError("content must not be empty")
    try:
        documents = [doc for doc in yaml.safe_load_all(content) if doc is not None]
    except yaml.YAMLError as exc:
        return {"valid": False, "kind": kind or "unknown", "errors": [f"Invalid YAML: {exc}"], "warnings": []}
    if not documents:
        return {"valid": False, "kind": kind or "unknown", "errors": ["YAML contains no documents"], "warnings": []}
    kinds = [doc.get("kind") for doc in documents if isinstance(doc, dict)]
    detected = kind or ("kubernetes_manifest" if any(k in {"Deployment", "Service", "ConfigMap", "Ingress"} for k in kinds) else "yaml")
    errors: list[str] = []
    warnings: list[str] = []
    if detected == "kubernetes_manifest":
        for index, doc in enumerate(documents, 1):
            if not isinstance(doc, dict) or not doc.get("apiVersion") or not doc.get("kind"):
                errors.append(f"Document {index} must contain apiVersion and kind")
            metadata = doc.get("metadata", {}) if isinstance(doc, dict) else {}
            if not metadata.get("name"):
                errors.append(f"Document {index} is missing metadata.name")
            if doc.get("kind") == "Deployment":
                spec = doc.get("spec", {})
                if not spec.get("selector") or not spec.get("template"):
                    errors.append("Deployment must contain spec.selector and spec.template")
                containers = spec.get("template", {}).get("spec", {}).get("containers", [])
                if not containers:
                    errors.append("Deployment must define at least one container")
                for container in containers:
                    image = container.get("image", "")
                    if image.endswith(":latest") or ":" not in image:
                        warnings.append(f"Container {container.get('name', '<unnamed>')} should use a pinned image tag")
    if detected in {"azure_pipeline", "yaml"} and not any("stages" in doc or "steps" in doc for doc in documents if isinstance(doc, dict)):
        warnings.append("The YAML does not appear to contain Azure Pipeline stages or steps")
    return {"valid": not errors, "kind": detected, "documents": len(documents), "kinds": kinds, "errors": errors, "warnings": warnings}


def generate_from_message(message: str) -> Artifact | None:
    if re.search(r"\b(?:azure|azure devops)\b", message, re.I) and re.search(r"\b(?:pipeline|pipelines|yaml)\b", message, re.I):
        return generate_azure_pipeline(message)
    if re.search(r"\b(?:manifesto|manifiesto|deployment|kubernetes|k8s)\b", message, re.I) and re.search(r"\b(?:crea|crear|genera|generar|escribe|construye)\b", message, re.I):
        return generate_kubernetes_manifest(message)
    return None
