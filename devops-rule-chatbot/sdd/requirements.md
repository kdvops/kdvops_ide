# Requisitos del Asistente Conversacional DevOps

## 1. Alcance

El sistema recibe solicitudes de lenguaje natural acotado, las transforma mediante reglas deterministas en operaciones DevOps de consulta y devuelve resultados estructurados y un resumen legible. La v0.4.0 permite planificar varias consultas en un mismo mensaje. El alcance no incluye comprensión libre, ejecución de escritura ni afirmaciones sobre capacidades no verificadas.

## 2. Estado de la línea base v0.5.0

| ID | Requisito | Estado | Evidencia disponible |
|---|---|---|---|
| RF-001 | Aceptar mensajes mediante `POST /api/chat` | Implementado según evidencia | Prueba manual con `Invoke-RestMethod` |
| RF-002 | Reconocer consultas de pods y eventos mediante reglas | Implementado según evidencia | Resultado `list_pods` y `list_events` |
| RF-003 | Construir un plan para varias operaciones del mismo mensaje | Implementado según evidencia | Solicitud “pods en demo y eventos” produjo dos operaciones |
| RF-004 | Bloquear el plan completo si contiene una escritura reconocida | Implementado según evidencia | Caso “mostrar pods y reiniciar deployment” |
| RF-005 | Indicar cuando una operación no está implementada | Implementado según evidencia | Caso de consulta de logs |
| RF-006 | Responder con identificador de sesión y resultados | Implementado según evidencia | Respuesta observada con `session_id`, `reply` y `results` |
| RF-007 | Consultar datos reales de un clúster | No afirmar | La evidencia muestra modo demo; el adaptador real no está documentado |
| RF-008 | Mantener contexto entre mensajes | Implementado en modo demo | Se conserva el último namespace válido durante la sesión |

## 3. Requisitos funcionales v0.5.0

| ID | Requisito | Prioridad | Criterio resumido |
|---|---|---|---|
| RF-101 | Propagar entidades entre pasos del mismo mensaje | Alta | `demo` llega a todos los pasos compatibles |
| RF-102 | Clasificar un mensaje compuesto como `compound` | Alta | La respuesta incluye estado global `compound` |
| RF-103 | Conservar contexto de sesión acotado | Alta | El último namespace válido puede reutilizarse explícitamente |
| RF-104 | Resolver referencias anafóricas simples | Media | “ese pod” se resuelve si existe un único candidato |
| RF-105 | Pedir aclaración ante ambigüedad | Alta | No ejecutar una consulta con referencia ambigua |
| RF-106 | Mantener la barrera de solo lectura | Crítica | El contexto nunca autoriza operaciones de escritura |
| RF-107 | Hacer trazable cada paso del plan | Media | Cada paso contiene `step_id`, intención, parámetros, origen y estado |

## 4. Requisitos no funcionales

- RNF-001 Determinismo: la misma entrada y el mismo estado deben producir el mismo plan.
- RNF-002 Seguridad: ninguna ruta de conversación puede habilitar escritura por inferencia.
- RNF-003 Compatibilidad: los clientes v0.4.0 deben seguir pudiendo enviar `{ "message": "..." }`.
- RNF-004 Observabilidad: los errores deben diferenciar entrada inválida, ambigüedad, operación no soportada y fallo de fuente.
- RNF-005 Pruebas: cada nueva regla debe incluir casos positivos, negativos y de regresión.
- RNF-006 Privacidad: no almacenar más contexto del necesario para resolver la conversación.

