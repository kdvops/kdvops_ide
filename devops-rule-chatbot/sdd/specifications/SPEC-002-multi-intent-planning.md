# SPEC 002 Planificación de solicitudes compuestas

## Objetivo

Separar un mensaje que contiene varias consultas de solo lectura en pasos ordenados y evaluables.

## Reglas

- El plan conserva el orden textual de las operaciones.
- El plan se rechaza completo si contiene una operación de escritura.
- Una operación no soportada debe identificarse sin ejecutarse.
- En v0.4.0, la propagación de entidades entre pasos no se considera garantizada.
- En v0.5.0, el plan debe producir estado global `compound` y parámetros por paso.

## Caso de aceptación principal

Entrada: “Muéstrame los pods en demo y sus eventos”.

Resultado v0.5.0 esperado:

```text
1. list_pods  namespace=demo  source=explicit
2. list_events namespace=demo  source=propagated
```

## Casos negativos

- “Muestra pods y reinicia el deployment”: plan bloqueado.
- “Muestra pods y consulta logs”: segundo paso no soportado; no afirmar que se ejecutó.

