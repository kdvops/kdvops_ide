# Roadmap de evolución

## v0.5.0 línea base

Estado documentado: implementado en modo demo. Incluye motor basado en reglas, planificación determinista de hasta cinco consultas por mensaje, estado global de respuesta, propagación de namespace, consultas de pods y eventos, barrera de solo lectura y respuesta estructurada con sesión y resultados.

## v0.5.0 propuesta

| Incremento | Resultado verificable | Dependencias |
|---|---|---|
| 1. Modelo de plan explícito | Cada mensaje produce `single`, `compound`, `unsupported` o `ambiguous` | Contrato interno de plan |
| 2. Propagación de entidades | El namespace explícito se copia a pasos compatibles | Catálogo de intents |
| 3. Contexto de sesión acotado | Se conserva el último namespace y recursos consultados | Almacenamiento de sesión |
| 4. Desambiguación | Se solicita aclaración antes de ejecutar | Resolución de candidatos |
| 5. Compatibilidad API | Se mantienen campos v0.4.0 y se añaden campos nuevos | Pruebas de contrato |
| 6. Endurecimiento de solo lectura | El contexto no puede crear permisos ni operaciones | Política de ejecución |

## Fuera de alcance de v0.5.0

- Comprensión libre o uso de un modelo de IA.
- Operaciones de escritura, reinicios o mutaciones de clúster.
- Prometer integración con Kubernetes real sin un adaptador verificado.
- Alta disponibilidad, autenticación empresarial o despliegue K3s si no existen en la implementación.

## Puerta de aceptación

La versión no debe declararse lista hasta que los casos de `SPEC-001`, `SPEC-002` y `SPEC-003` pasen en modo demo y el contrato de compatibilidad v0.4.0 permanezca verde.

