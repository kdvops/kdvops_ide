"""Deterministic local workspace actions for rules mode.

This module deliberately does not interpret arbitrary code or execute a shell.
It recognizes a small set of bounded Spanish requests and delegates only
artifact generation and secret scanning to the local rule service.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from .workspace import Workspace
from .privacy import PrivacyFilter


@dataclass(frozen=True)
class LocalActionResult:
    handled: bool
    message: str = ""


def _relative_path(prompt: str, default: str | None = None) -> str | None:
    matches = re.findall(r"(?:^|\s)([\w./-]+\.(?:ya?ml|json|txt|md))(?:\s|$|[,.:])", prompt, re.I)
    return matches[0] if matches else default


def _scan_before_write(settings, content: str, filename: str) -> None:
    safe_content = PrivacyFilter(require_scanner=True).mask_text(content)
    if safe_content != content:
        raise ValueError("Escritura bloqueada: se detectó información sensible")
    response = httpx.post(
        f"{settings.rule_endpoint}/api/security/scan",
        json={"content": safe_content, "filename": filename},
        timeout=settings.timeout,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("status") != "clean" or not result.get("scan", {}).get("clean"):
        raise ValueError("Escritura bloqueada: el contenido contiene posibles secretos")


def _validate_kubernetes(content: str, filename: str) -> None:
    if Path(filename).suffix.lower() not in {".yaml", ".yml"}:
        return
    documents = [item for item in yaml.safe_load_all(content) if item is not None]
    kubernetes_documents = [item for item in documents if isinstance(item, dict) and item.get("apiVersion")]
    for document in kubernetes_documents:
        if not document.get("kind") or not isinstance(document.get("metadata"), dict) or not document["metadata"].get("name"):
            raise ValueError("Manifiesto Kubernetes inválido: requiere apiVersion, kind y metadata.name")
    if not kubernetes_documents or not shutil_which("kubectl"):
        return
    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=client", "--validate=strict", "-f", "-"],
        input=content,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Validación Kubernetes fallida: " + (result.stderr.strip() or "error desconocido")[:500])


def shutil_which(command: str) -> str | None:
    """Small dependency-free equivalent of shutil.which for the fixed binary."""
    for directory in os.getenv("PATH", "").split(os.pathsep):
        candidate = Path(directory) / command
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return str(candidate)
    return None


def _write_with_approval(ws: Workspace, settings, path: str, content: str, console: Console) -> str:
    old = ws.read_file(path) if ws.file_exists(path) else ""
    if old == content:
        return f"No hay cambios en {path}."
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if len(diff) > 16_000:
        raise ValueError("El diff excede el límite de revisión")
    if console is not None:
        console.print(Panel(diff or "(archivo nuevo)", title=f"Propuesta: {path}"))
    if not Confirm.ask(f"¿Aplicar cambios en {path}?", default=False):
        return f"Cambios rechazados; no se modificó {path}."
    _scan_before_write(settings, content, path)
    _validate_kubernetes(content, path)
    ws.write_file(path, content)
    return f"Cambios aprobados y guardados en {path}."


def _analyze_directory(ws: Workspace, prompt: str) -> str:
    recursive = any(word in prompt.lower() for word in ("recursivo", "recursiva", "todo", "completo"))
    directory_match = re.search(r"(?:directorio|carpeta)\s+([\w./-]+)", prompt, re.I)
    directory = directory_match.group(1) if directory_match and directory_match.group(1).lower() not in {
        "completo", "completa", "actual", "raíz", "raiz"
    } else "."
    directory = _relative_path(prompt) or directory
    if directory != ".":
        directory = str(Path(directory).parent) if Path(directory).suffix else directory
    files = ws.list_files(directory, recursive=recursive or directory != ".")
    visible = [item for item in files if not item.endswith("/")]
    extensions: dict[str, int] = {}
    for item in visible:
        suffix = Path(item).suffix.lower() or "[sin extensión]"
        extensions[suffix] = extensions.get(suffix, 0) + 1
    summary = ", ".join(f"{key}: {value}" for key, value in sorted(extensions.items())) or "sin archivos visibles"
    names = {Path(item.rstrip("/")).name.lower() for item in visible}
    indicators = []
    if "dockerfile" in names:
        indicators.append("Dockerfile")
    if "azure-pipelines.yml" in names or "azure-pipelines.yaml" in names:
        indicators.append("Azure Pipeline")
    if "compose.yaml" in names or "docker-compose.yml" in names:
        indicators.append("Docker Compose")
    if any("helm" in item.lower() for item in visible):
        indicators.append("Helm")
    if any("k8s" in item.lower() or "kubernetes" in item.lower() for item in visible):
        indicators.append("Kubernetes")
    detected = ", ".join(dict.fromkeys(indicators)) or "ninguno"
    sample = "\n".join(visible[:40]) or "(vacío)"
    return f"Directorio analizado: {directory}\nArchivos: {len(visible)}\nTipos: {summary}\nTecnologías detectadas: {detected}\n{sample}"


def _read_yaml(ws: Workspace, prompt: str) -> str | None:
    path = _relative_path(prompt)
    if not path:
        return None
    content = ws.read_file(path)
    parsed = yaml.safe_load(content)
    if parsed is None:
        return f"{path}: YAML vacío."
    if isinstance(parsed, dict):
        keys = ", ".join(str(key) for key in parsed.keys())
        kind = parsed.get("kind", "documento")
        return f"{path}: YAML válido, tipo {kind}. Claves principales: {keys}."
    return f"{path}: YAML válido ({type(parsed).__name__})."


def _devsecops_files(ws: Workspace, prompt: str) -> list[dict[str, str]]:
    match = re.search(r"(?:carpetas?|directorios?)\s+(.+)$", prompt, re.I)
    if match:
        raw_targets = re.split(r"\s+y\s+|,", match.group(1), flags=re.I)
        targets = [item.strip(" .,:;") for item in raw_targets if item.strip(" .,:;")]
    else:
        targets = ["."]
    files: list[dict[str, str]] = []
    allowed_suffixes = {".yml", ".yaml", ".json", ".toml", ".tf", ".py", ".js", ".ts", ".ps1", ".sh"}
    allowed_names = {"dockerfile", "makefile", "package.json", "requirements.txt", "pyproject.toml"}
    for target in targets[:10]:
        entries = ws.list_files(target, recursive=True)
        for entry in entries:
            if entry.endswith("/") or any(part in entry.replace("\\", "/").split("/") for part in (".git", "node_modules", ".venv")):
                continue
            path = Path(entry)
            if path.suffix.lower() not in allowed_suffixes and path.name.lower() not in allowed_names:
                continue
            try:
                content = ws.read_file(entry)
            except (OSError, UnicodeError, ValueError):
                continue
            files.append({"filename": entry, "content": content})
            if len(files) >= 100:
                break
        if len(files) >= 100:
            break
    combined = "\n\n".join(f"# FILE: {item['filename']}\n{item['content']}" for item in files)
    if PrivacyFilter(require_scanner=True).mask_text(combined) != combined:
        raise ValueError("Evaluación bloqueada: se detectó información sensible en el conjunto de archivos")
    return files


def _modify_yaml(ws: Workspace, settings, prompt: str, console: Console) -> str | None:
    path = _relative_path(prompt)
    if not path or not re.search(r"\b(modifica|modificar|actualiza|actualizar|cambia|cambiar)\b", prompt, re.I):
        return None
    content = ws.read_file(path)
    documents = list(yaml.safe_load_all(content))
    if not documents or any(document is not None and not isinstance(document, dict) for document in documents):
        raise ValueError("Los documentos YAML deben tener objetos como raíz")
    replica = re.search(r"(?:réplicas|replicas)\s+(?:a|en|por)\s*(\d+)", prompt, re.I)
    image = re.search(r"imagen\s+(?:a|en|por)\s*([\w./:@-]+)", prompt, re.I)
    namespace = re.search(r"namespace\s+(?:a|en|por)\s*([\w.-]+)", prompt, re.I)
    port = re.search(r"puerto\s+(?:a|en|por)\s*(\d+)", prompt, re.I)
    memory = re.search(r"memoria\s+(?:a|en|por)\s*([\w.-]+)", prompt, re.I)
    cpu = re.search(r"cpu\s+(?:a|en|por)\s*([\w.-]+)", prompt, re.I)
    environment = re.search(r"variable\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:a|en|por)\s*([^\s]+)", prompt, re.I)
    changed = False
    for document in documents:
        if document is None:
            continue
        if replica and document.get("kind") in {
            "Deployment", "StatefulSet", "ReplicaSet", "DaemonSet", "Job", "CronJob"
        } and isinstance(document.get("spec"), dict):
            document["spec"]["replicas"] = int(replica.group(1))
            changed = True
        if image:
            template = document.get("spec", {}).get("template", {}) if isinstance(document.get("spec"), dict) else {}
            containers = template.get("spec", {}).get("containers", []) if isinstance(template, dict) else []
            if containers:
                containers[0]["image"] = image.group(1)
                changed = True
        if namespace and isinstance(document.get("metadata"), dict):
            document["metadata"]["namespace"] = namespace.group(1)
            changed = True
        template = document.get("spec", {}).get("template", {}) if isinstance(document.get("spec"), dict) else {}
        containers = template.get("spec", {}).get("containers", []) if isinstance(template, dict) else []
        if containers:
            container = containers[0]
            if port:
                ports = container.setdefault("ports", [])
                if ports:
                    ports[0]["containerPort"] = int(port.group(1))
                else:
                    ports.append({"containerPort": int(port.group(1))})
                changed = True
            if memory or cpu:
                resources = container.setdefault("resources", {})
                limits = resources.setdefault("limits", {})
                if memory:
                    limits["memory"] = memory.group(1)
                if cpu:
                    limits["cpu"] = cpu.group(1)
                changed = True
            if environment:
                env = container.setdefault("env", [])
                existing = next((item for item in env if item.get("name") == environment.group(1)), None)
                if existing:
                    existing["value"] = environment.group(2)
                else:
                    env.append({"name": environment.group(1), "value": environment.group(2)})
                changed = True
    if not changed:
        raise ValueError("Solo se admiten cambios deterministas de replicas o imagen en YAML")
    rendered = yaml.safe_dump_all(documents, sort_keys=False, allow_unicode=True)
    return _write_with_approval(ws, settings, path, rendered, console)


def handle_local_request(prompt: str, ws: Workspace, settings, console: Console) -> LocalActionResult:
    """Handle a recognized local request; return handled=False for chatbot NLU."""
    normalized = prompt.lower().strip()
    try:
        if re.search(r"\b(eval[uú]a|evaluar|analiza|revisa)\b", normalized) and re.search(
            r"\b(devsecops|flujo de seguridad|pipeline|proyecto)\b", normalized
        ):
            files = _devsecops_files(ws, prompt)
            if not files:
                raise ValueError("No se encontraron archivos evaluables en las carpetas indicadas")
            response = httpx.post(
                f"{settings.rule_endpoint}/api/devsecops/evaluate",
                json={"files": files},
                timeout=settings.timeout,
            )
            response.raise_for_status()
            evaluation = response.json().get("evaluation", {})
            missing = ", ".join(evaluation.get("missing", [])[:4])
            suffix = f" Pendientes: {missing}." if missing else ""
            return LocalActionResult(True, evaluation.get("summary", "Evaluación completada.") + suffix)
        if re.search(r"\b(analiza|analizar|revisa|inspecciona)\b", normalized) and re.search(
            r"\b(directorio|carpeta|proyecto)\b", normalized
        ):
            return LocalActionResult(True, _analyze_directory(ws, prompt))
        if re.search(r"\b(lee|leer|muestra|mostrar|abre|analiza)\b", normalized) and re.search(
            r"\.(?:ya?ml)\b", normalized, re.I
        ):
            return LocalActionResult(True, _read_yaml(ws, prompt) or "No se encontró el archivo YAML.")
        if re.search(r"\b(modifica|modificar|actualiza|actualizar|cambia|cambiar)\b", normalized):
            return LocalActionResult(True, _modify_yaml(ws, settings, prompt, console) or "Solicitud de modificación incompleta.")
        if re.search(r"\b(crea|crear|genera|generar)\b", normalized) and re.search(
            r"\b(manifiesto|kubernetes|pipeline|azure)\b", normalized
        ):
            kind = "azure_pipeline" if re.search(r"pipeline|azure", normalized) else "kubernetes_manifest"
            safe_prompt = PrivacyFilter(require_scanner=True).mask_text(prompt)
            response = httpx.post(
                f"{settings.rule_endpoint}/api/generate",
                json={"kind": kind, "message": safe_prompt},
                timeout=settings.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "generated":
                raise ValueError(payload.get("reply", "El artefacto fue bloqueado"))
            artifact = payload["artifact"]
            path = _relative_path(prompt, artifact["filename"]) or artifact["filename"]
            return LocalActionResult(True, _write_with_approval(ws, settings, path, artifact["content"], console))
    except (OSError, UnicodeError, ValueError, httpx.HTTPError, yaml.YAMLError) as exc:
        return LocalActionResult(True, f"ERROR: {type(exc).__name__}: {exc}")
    return LocalActionResult(False)
