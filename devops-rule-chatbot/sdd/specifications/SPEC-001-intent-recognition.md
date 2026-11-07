# SPEC 001 Reconocimiento de intenciones

## Objetivo

Reconocer un conjunto cerrado de consultas DevOps mediante reglas deterministas y producir una intención normalizada sin afirmar capacidades que no estén en el catálogo.

## Entradas y salidas

- Entrada: mensaje textual del usuario.
- Salida mínima: intención, entidades detectadas y estado `recognized`, `unsupported` o `ambiguous`.
- Una operación de escritura reconocida debe marcarse como bloqueante.

## Criterios de aceptación

1. Una solicitud de pods se normaliza como `list_pods`.
2. Una solicitud de eventos se normaliza como `list_events`.
3. Una solicitud de logs se marca como no implementada si no existe un ejecutor confirmado.
4. Una solicitud de reinicio o escritura bloquea el plan completo.
5. La misma entrada no cambia de intención por azar.

## No incluido

No se define comprensión semántica libre, ranking probabilístico ni uso de IA.

