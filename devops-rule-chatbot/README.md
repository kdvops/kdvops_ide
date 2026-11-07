# Asistente Conversacional DevOps basado en reglas — v0.5.0

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
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

## Seguridad

El contenedor publica solo en 127.0.0.1, no ejecuta comandos shell, y las consultas reales de Kubernetes requieren permisos RBAC de solo lectura. No exponer el endpoint públicamente sin autenticación.
