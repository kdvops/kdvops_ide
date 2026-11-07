# ADR 003 Ejecución de solo lectura

## Decisión

Mantener una barrera explícita que impida ejecutar operaciones de escritura, incluso cuando aparezcan mezcladas con consultas válidas o se intenten introducir mediante contexto.

## Motivo

La seguridad operativa es prioritaria en un asistente DevOps basado en reglas. La evidencia de v0.4.0 ya describe el bloqueo de una solicitud mixta con reinicio.

## Consecuencias

La v0.5.0 puede ampliar el contexto y la resolución de referencias, pero nunca debe usar esos mecanismos para conceder permisos ni inferir autorización.

