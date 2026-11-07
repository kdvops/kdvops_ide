# SPEC 003 Motor de contexto conversacional

## Estado

Propuesta para v0.5.0. No debe presentarse como funcionalidad disponible de v0.4.0.

## Objetivo

Conservar un contexto mínimo y seguro para completar parámetros de consultas relacionadas sin convertir el contexto en autorización de escritura.

## Requisitos

- RF-CTX-001: conservar el último namespace explícito válido durante la sesión.
- RF-CTX-002: conservar una lista limitada de recursos devueltos.
- RF-CTX-003: propagar entidades a pasos compatibles del mismo plan.
- RF-CTX-004: resolver “el primero” o “ese pod” solo con un único candidato claro.
- RF-CTX-005: pedir aclaración si hay cero o múltiples candidatos.
- RF-CTX-006: limpiar o expirar el contexto según una política definida.
- RF-CTX-007: ignorar el contexto para autorizar operaciones de escritura.

## Aceptación

Para “Muéstrame los pods en demo y sus eventos”, ambos pasos deben contener `namespace=demo`, y el segundo debe indicar que el valor fue propagado. Para una referencia ambigua, la respuesta debe solicitar aclaración y no consultar la fuente.

