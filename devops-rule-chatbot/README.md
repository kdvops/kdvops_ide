# Asistente Conversacional DevOps basado en reglas — v0.6.0

Chatbot sin LLM ni ML. Esta versión agrega un **planificador determinista de consultas compuestas** de solo lectura. Conserva el catálogo de vocabulario, extracción de entidades y las integraciones demo/Kubernetes de la v0.3.0.

## Docker Compose

```powershell
docker compose up -d --build
docker compose ps
```

Interfaz: http://127.0.0.1:8000 — salud: http://127.0.0.1:8000/healthz

Ejemplo PowerShell 5.1/7 con UTF-8 explícito:

```powershell
$json = @{ message = 'muestra los pods en ns demo y dame los eventos' } | ConvertTo-Json -Compress
$bytes = [Text.Encoding]::UTF8.GetBytes($json)
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/chat' -Method POST -ContentType 'application/json; charset=utf-8' -Body $bytes | ConvertTo-Json -Depth 10
```

## Alcance v0.5

- Admite hasta cinco solicitudes explícitas de lectura en un mensaje, separadas por `y`, `luego` o `;` cuando cada segmento expresa una acción.
- Si algún paso es ambiguo, desconocido o no implementado, **no ejecuta ninguno**.
- Si el mensaje contiene verbos de modificación reconocidos, bloquea toda la solicitud.
- Responde con `results` (por paso) y `plan`; conserva `items` para consultas individuales.
- El modo demo (`DEMO_MODE=true`) responde con datos simulados, no consulta el clúster.
- **Limitación:** no interpreta cualquier oración compuesta; la segmentación se basa en patrones. La detección de verbos peligrosos también se basa en reglas y debe reforzarse antes de permitir escritura. No habilitar operaciones de escritura en producción.

## Pruebas

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## Generación y análisis sin IA

El servicio puede generar artefactos deterministas sin LLM:

```powershell
$body = @{ kind = 'kubernetes_manifest'; message = 'crea un manifiesto kubernetes llamado api en ns demo con 2 replicas, imagen ghcr.io/acme/api:1.0 y puerto 8080' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/generate' -Method POST -ContentType 'application/json' -Body $body
```

Para un pipeline de Azure DevOps:

```powershell
$body = @{ kind = 'azure_pipeline'; message = 'crea un azure pipeline para una app python llamada api' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/generate' -Method POST -ContentType 'application/json' -Body $body
```

Para analizar YAML existente, envía su contenido a `POST /api/analyze`:

```json
{
  "kind": "kubernetes_manifest",
  "content": "apiVersion: v1\nkind: Service\nmetadata:\n  name: api"
}
```

Para escanear secretos rápidamente sin depender de Git, usa `POST /api/security/scan`.
El servicio usa Gitleaks fijado a la versión 8.30.1, devuelve solo metadatos y reemplaza el valor detectado por `[REDACTED]`.

También reconoce solicitudes de generación directamente desde `/api/chat`. La generación usa plantillas
acotadas, valida nombres, réplicas y puertos, y devuelve advertencias cuando detecta riesgos como `:latest`.

## Evaluación DevSecOps

`POST /api/devsecops/evaluate` evalúa un flujo completo sin IA. Asigna 100 puntos a control de cambios,
build reproducible, pruebas unitarias/integración, calidad, SAST, SCA, secretos, IaC, imágenes, SBOM,
provenance/firma, DAST, pentest, despliegue con salud/rollback, GitOps/Argo CD, aprobaciones, hardening
de Kubernetes y observabilidad.
Devuelve el nivel (`initial`, `developing`, `managed` o `advanced`), evidencia detectada, controles faltantes
y recomendaciones. Acepta `content` para un archivo o `files` para evaluar múltiples archivos y carpetas
como un mismo flujo. Cada archivo pasa por Gitleaks antes de evaluarse. El resultado es una señal de madurez
determinista y no equivale por sí sola a una certificación de cumplimiento.

Además del puntaje, devuelve `validations` para comprobar la completitud operativa: etapas CI/CD, publicación
y promoción de artefactos inmutables, gates de fallo, versionado, smoke tests post-despliegue, rendimiento y
resiliencia, gestión de vulnerabilidades, backup/restore y gestión de secretos en runtime.
También identifica explícitamente Kubernetes Sealed Secrets mediante `SealedSecret`, `kubeseal`,
`bitnami.com/v1alpha1` o `encryptedData`.

La matriz toma como referencia NIST SSDF para prácticas de desarrollo seguro, SLSA para provenance e integridad
de builds, las recomendaciones de seguridad de Kubernetes, los controles GitOps/RBAC de Argo CD y los checks de
entornos de Azure Pipelines. La evidencia se detecta en todos los archivos recibidos como un único flujo.

Ejemplo compuesto:

```json
{
  "files": [
    {"filename": "azure-pipelines.yml", "content": "..."},
    {"filename": "security/gitleaks.yml", "content": "..."},
    {"filename": "infra/k8s/deployment.yaml", "content": "..."}
  ]
}
```

## Seguridad

El contenedor publica solo en 127.0.0.1, no ejecuta comandos shell, y las consultas reales de Kubernetes requieren permisos RBAC de solo lectura. No exponer el endpoint públicamente sin autenticación.
