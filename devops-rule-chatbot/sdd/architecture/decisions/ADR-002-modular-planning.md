# ADR 002 Separación entre reconocimiento y planificación

## Decisión

Separar reconocimiento de intenciones, construcción del plan y ejecución de consultas.

## Motivo

La v0.4.0 ya distingue solicitudes compuestas y necesita corregir la propagación de entidades sin mezclar reglas de lenguaje con acceso a datos.

## Consecuencias

Cada paso puede probarse de forma aislada. La API interna debe representar estado, parámetros y bloqueo antes de ejecutar.

