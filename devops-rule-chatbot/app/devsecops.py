"""Deterministic DevSecOps maturity scoring; no model or external service required."""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class Control:
    key: str
    name: str
    weight: int
    patterns: tuple[str, ...]
    recommendation: str


# 100 points aligned with NIST SSDF, SLSA, Kubernetes security, GitOps/Argo CD
# and Azure Pipelines release controls. This is a maturity signal, not a certification.
CONTROLS = (
    Control("source_control_review", "Control de cambios y revisión", 3, (r"pull\s*request", r"merge\s*request", r"code\s+review", r"branch\s+protect", r"protected\s+branch", r"git\s+repository"), "Proteger ramas y exigir revisión/aprobación antes de integrar cambios."),
    Control("reproducible_build", "Build reproducible y dependencias fijadas", 3, (r"reproducible", r"lock(?:file|ed)", r"package-lock\.json", r"poetry\.lock", r"requirements\.txt", r"pin(?:ned)?\s+(?:version|digest)", r"sha256", r"digest:"), "Fijar versiones y digests, aislar el build y conservar entradas y resultados."),
    Control("unit_tests", "Pruebas unitarias", 7, (r"unit\s*test", r"pytest", r"jest", r"junit", r"dotnet\s+test", r"go\s+test", r"pruebas?\s+unitarias"), "Agregar una etapa de pruebas unitarias con umbral de fallo."),
    Control("integration_tests", "Pruebas de integración y extremo a extremo", 4, (r"integration\s*test", r"test\s+integration", r"e2e", r"end[- ]to[- ]end", r"contract\s+test", r"pruebas?\s+de\s+integraci[oó]n"), "Agregar pruebas de integración, contrato o extremo a extremo."),
    Control("quality_gate", "Calidad, cobertura y gates", 4, (r"coverage", r"cobertura", r"quality\s+gate", r"lint", r"flake8", r"ruff", r"eslint", r"sonar(?:cloud|qube)?"), "Definir umbrales verificables de cobertura, lint y calidad que bloqueen el pipeline."),
    Control("sast", "Análisis estático de código (SAST)", 8, (r"\bsast\b", r"static\s+(?:application\s+)?security", r"sonarqube", r"semgrep", r"bandit", r"checkmarx", r"an[aá]lisis\s+est[aá]tico"), "Incorporar SAST y bloquear hallazgos críticos o de alta severidad."),
    Control("sca", "Análisis de dependencias (SCA)", 6, (r"\bsca\b", r"dependency\s+(?:check|scan|audit)", r"pip-audit", r"npm\s+audit", r"osv-scanner", r"dependabot", r"renovate", r"an[aá]lisis\s+de\s+dependencias"), "Analizar dependencias directas y transitivas, con política de severidad."),
    Control("secrets", "Detección y prevención de secretos", 6, (r"gitleaks", r"trufflehog", r"secret\s+(?:scan|scanning)", r"push\s+protection", r"detecci[oó]n\s+de\s+secretos"), "Escanear antes de publicar y bloquear secretos en commits, artefactos y solicitudes."),
    Control("iac", "Seguridad de infraestructura como código", 5, (r"\biac\b", r"checkov", r"tfsec", r"kube(?:-?linter|scape)", r"conftest", r"opa\b", r"policy\s+as\s+code", r"infra(?:structure)?\s+as\s+code"), "Validar Terraform, Kubernetes y políticas IaC antes de desplegar."),
    Control("container", "Escaneo y hardening de imágenes", 5, (r"trivy\s+image", r"trivy.*container", r"grype", r"anchore", r"container\s+(?:scan|scanning)", r"distroless", r"non[- ]root", r"escaneo\s+de\s+im[aá]genes"), "Escanear imágenes, usar base mínima/no-root y bloquear vulnerabilidades críticas."),
    Control("sbom", "SBOM y trazabilidad de componentes", 4, (r"\bsbom\b", r"syft", r"cyclonedx", r"spdx", r"software\s+bom"), "Generar, publicar y conservar un SBOM por versión desplegable."),
    Control("provenance_signing", "Firma, integridad y provenance", 6, (r"slsa", r"provenance", r"in[- ]toto", r"cosign", r"sign(?:ed|ing)?\s+(?:image|artifact)", r"attestation", r"firma\s+de\s+artefactos"), "Generar provenance verificable y firmar artefactos; verificar firma antes de promover."),
    Control("dast", "Pruebas dinámicas (DAST)", 5, (r"\bdast\b", r"zap(?:roxy)?", r"owasp\s+zap", r"nikto", r"nuclei", r"dynamic\s+application"), "Agregar pruebas dinámicas contra un entorno desplegado y tratar hallazgos."),
    Control("pentest", "Pruebas de penetración", 3, (r"pentest", r"penetration\s+test", r"prueba[s]?\s+de\s+penetraci[oó]n", r"burp\s+suite", r"seguridad\s+ofensiva"), "Programar pentests periódicos y dar seguimiento a sus hallazgos."),
    Control("deploy_health_rollback", "Despliegue seguro, salud y rollback", 6, (r"helm", r"kustomize", r"kubectl\s+(?:apply|rollout)", r"readiness(?:probe)?", r"liveness(?:probe)?", r"startup(?:probe)?", r"rollback", r"blue[- ]green", r"canary", r"rolling\s+update"), "Definir estrategia de despliegue, probes de salud, espera de disponibilidad y rollback."),
    Control("gitops_argocd", "GitOps y Argo CD", 7, (r"argocd", r"argo\s*cd", r"gitops", r"syncpolicy", r"automated\s+sync", r"selfheal", r"sync[- ]wave", r"application(set)?", r"outofsync", r"prune"), "Declarar Applications/Projects, restringir fuentes y destinos, y controlar sync, drift y prune."),
    Control("approvals", "Aprobaciones y promoción entre entornos", 4, (r"manual\s+validation", r"environment\s+approval", r"approvals?", r"checks?", r"staging", r"production", r"aprobaci[oó]n", r"promoci[oó]n"), "Separar entornos y exigir checks/aprobación protegidos para producción."),
    Control("runtime_hardening", "Hardening de Kubernetes en runtime", 6, (r"\brbac\b", r"rolebinding", r"networkpolic(?:y|ies)", r"pod\s+security", r"securitycontext", r"seccomp", r"admission", r"resource\s+(?:requests?|limits?)", r"runAsNonRoot"), "Aplicar mínimo privilegio, Pod Security, NetworkPolicy, requests/limits y admission policies."),
    Control("observability", "Observabilidad, auditoría y respuesta", 8, (r"prometheus", r"grafana", r"opentelemetry", r"\bmetrics\b", r"logging", r"logs?", r"alert(?:ing|as)", r"tracing", r"audit\s+log", r"monitor(?:ing)?", r"incident\s+response"), "Centralizar métricas, logs, trazas y alertas; conservar auditoría y procedimientos de respuesta."),
)

# Completeness checks complement the weighted score. They catch pipeline
# mechanics that keyword scoring alone can miss without changing the 100-point
# security maturity scale.
STRUCTURAL_VALIDATIONS = (
    ("pipeline_stages", "Etapas CI/CD separadas", (r"stages?:", r"jobs?:", r"steps?:", r"build", r"test", r"deploy"), "Separar build, test, security, package y deploy con dependencias explícitas."),
    ("artifact_flow", "Publicación y promoción de artefactos", (r"publish", r"download", r"artifact", r"docker\s+push", r"container\s+registry", r"acr", r"ghcr", r"ecr"), "Publicar un artefacto inmutable una vez y promoverlo entre entornos."),
    ("failure_gates", "Fallos bloquean promoción", (r"dependsOn", r"condition:\s*succeeded", r"exit\s+1", r"continueOnError:\s*false", r"fail\s+(?:on|if)", r"quality\s+gate"), "Hacer que fallos de pruebas y seguridad detengan la promoción."),
    ("release_versioning", "Versionado y trazabilidad de releases", (r"build(?:\.build)?number", r"semver", r"version", r"git\s+tag", r"commit\s+(?:sha|hash)", r"image:\s*[^\n:]+:[^\n]+", r"digest:\s*sha256"), "Etiquetar releases con versión, commit y digest; evitar tags mutables como latest."),
    ("post_deploy_smoke", "Smoke test y verificación post-despliegue", (r"smoke\s+test", r"health\s*check", r"curl\s+https?", r"post[- ]deploy", r"rollout\s+status", r"kubectl\s+wait"), "Verificar salud y funcionalidad crítica después de desplegar."),
    ("performance_resilience", "Rendimiento y resiliencia", (r"load\s+test", r"k6", r"jmeter", r"stress\s+test", r"chaos", r"hpa", r"horizontalpodautoscaler", r"poddisruptionbudget", r"pdb", r"autoscal"), "Agregar pruebas de carga y controles de capacidad, disponibilidad y resiliencia."),
    ("vulnerability_response", "Gestión y remediación de vulnerabilidades", (r"cve", r"vulnerability\s+(?:management|triage|remediation)", r"remediat", r"security\s+(?:ticket|issue)", r"renovate", r"dependabot", r"risk\s+acceptance"), "Registrar, priorizar, remediar y volver a verificar hallazgos con SLA."),
    ("backup_dr", "Backup, restauración y continuidad", (r"backup", r"restore", r"disaster\s+recovery", r"business\s+continuity", r"\brpo\b", r"\brto\b"), "Probar restauración y documentar RPO/RTO para datos y configuración."),
    ("runtime_secret_management", "Gestión segura de secretos en runtime", (r"key\s*vault", r"secret\s+manager", r"external\s+secrets", r"sealed\s*secret", r"vault", r"secretproviderclass"), "Usar un gestor de secretos o integración externa; nunca incrustar credenciales en YAML."),
    ("sealed_secrets", "Sealed Secrets de Kubernetes", (r"sealed\s*secret", r"sealedsecret", r"kubeseal", r"bitnami\.com/v1alpha1", r"encryptedData:"), "Usar Sealed Secrets correctamente: cifrar con kubeseal, almacenar solo encryptedData y proteger la clave privada del controller."),
)


def evaluate_devsecops(content: str, filename: str = "pipeline.yml") -> dict:
    """Score a complete flow description using bounded deterministic rules."""
    text = content.lower()
    parsed = None
    parse_warning = None
    try:
        parsed = list(yaml.safe_load_all(content))
    except yaml.YAMLError as exc:
        parse_warning = f"YAML no válido: {str(exc).splitlines()[0][:180]}"
    controls = []
    detected_tools = set()
    score = 0
    for control in CONTROLS:
        evidence = []
        for pattern in control.patterns:
            match = re.search(pattern, text, re.I)
            if match:
                evidence.append(match.group(0)[:80])
                if len(detected_tools) < 40:
                    detected_tools.add(match.group(0)[:80])
        present = bool(evidence)
        if present:
            score += control.weight
        controls.append({
            "key": control.key,
            "name": control.name,
            "weight": control.weight,
            "points": control.weight if present else 0,
            "status": "present" if present else "missing",
            "evidence": list(dict.fromkeys(evidence)),
            "recommendation": None if present else control.recommendation,
        })
    if score >= 85:
        level = "advanced"
    elif score >= 65:
        level = "managed"
    elif score >= 40:
        level = "developing"
    else:
        level = "initial"
    missing = [item["name"] for item in controls if item["status"] == "missing"]
    validations = []
    for key, name, patterns, recommendation in STRUCTURAL_VALIDATIONS:
        evidence = []
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                evidence.append(match.group(0)[:80])
        validations.append({
            "key": key,
            "name": name,
            "status": "passed" if evidence else "missing",
            "evidence": list(dict.fromkeys(evidence)),
            "recommendation": None if evidence else recommendation,
        })
    passed_validations = sum(item["status"] == "passed" for item in validations)
    result = {
        "filename": filename,
        "model": "deterministic-devsecops-v2",
        "score": score,
        "maximum": sum(control.weight for control in CONTROLS),
        "percentage": score,
        "level": level,
        "controls": controls,
        "missing": missing,
        "validations": validations,
        "validation_summary": {
            "passed": passed_validations,
            "total": len(validations),
            "missing": len(validations) - passed_validations,
        },
        "detected_tools": sorted(detected_tools),
        "valid_yaml": parse_warning is None,
        "summary": f"Madurez DevSecOps {level}: {score}/100 puntos; {len(missing)} controles pendientes.",
    }
    if parse_warning:
        result["warning"] = parse_warning
    if parsed is not None and not any(item is not None for item in parsed):
        result["warning"] = "El documento YAML está vacío."
    return result
