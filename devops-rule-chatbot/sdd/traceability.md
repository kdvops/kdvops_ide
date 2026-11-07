# Matriz de trazabilidad

| Requisito | Especificación | Prueba mínima | Estado |
|---|---|---|---|
| RF-002 | SPEC-001 | Mensaje de pods devuelve intención `list_pods` | Línea base observada |
| RF-003 | SPEC-002 | Pods y eventos producen dos pasos | Línea base observada |
| RF-004 | SPEC-001/002 | Solicitud mixta con escritura bloquea el plan | Línea base observada |
| RF-101 | SPEC-003 | Namespace `demo` aparece en ambos pasos | Implementado en `tests/test_planner.py` y `tests/test_api.py` |
| RF-102 | SPEC-002 | Clasificación global `compound` | Implementado en `tests/test_api.py` |
| RF-103 | SPEC-003 | Segundo mensaje reutiliza namespace de sesión | Implementado en `tests/test_api.py` |
| RF-105 | SPEC-003 | Referencia ambigua no ejecuta consultas | Pendiente |
| RF-106 | ADR-003 | Ningún caso de contexto habilita escritura | Regla obligatoria |

