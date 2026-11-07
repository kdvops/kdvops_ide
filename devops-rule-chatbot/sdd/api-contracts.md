# Contratos de API

## 1. Contrato observado de v0.4.0

### Solicitud

`POST /api/chat`

```json
{
  "message": "muestra los pods en ns demo y dame los eventos"
}
```

El historial muestra que el cliente envió UTF-8 explícito y recibió una respuesta JSON. No se afirma aquí autenticación, límites, códigos HTTP ni campos adicionales porque no están documentados en la evidencia disponible.

### Respuesta observada

```json
{
  "session_id": "<id-de-sesion>",
  "reply": "Consultas completadas: list_pods: 2, list_events: 1",
  "results": [
    {
      "intent": "list_pods",
      "namespace": "demo",
      "items": [
        {
          "name": "api-7db",
          "namespace": "demo",
          "phase": "Running",
          "problems": []
        }
      ],
      "count": 2
    },
    {
      "intent": "list_events",
      "namespace": null,
      "items": [
        {
          "namespace": "demo",
          "reason": "BackOff",
          "message": "Back-off restarting failed container"
        }
      ]
    }
  ]
}
```

La respuesta anterior es una representación fiel de la forma observada, no un esquema exhaustivo. En particular, la segunda operación mostró `namespace: null` en el resultado aunque el evento pertenecía a `demo`; esto motiva `SPEC-003`.

## 2. Contrato propuesto para v0.5.0

La evolución debe ser compatible con la solicitud v0.4.0 y puede añadir campos sin retirar los existentes.

```json
{
  "session_id": "<opcional>",
  "message": "muéstrame los pods en demo y sus eventos"
}
```

Respuesta propuesta:

```json
{
  "session_id": "<id-de-sesion>",
  "status": "compound",
  "reply": "Consultas completadas: list_pods: 2, list_events: 1",
  "plan": {
    "status": "ready",
    "steps": [
      {
        "step_id": "1",
        "intent": "list_pods",
        "parameters": { "namespace": "demo" },
        "source": "explicit",
        "status": "ready"
      },
      {
        "step_id": "2",
        "intent": "list_events",
        "parameters": { "namespace": "demo" },
        "source": "propagated",
        "status": "ready"
      }
    ]
  },
  "results": []
}
```

Los nombres `status`, `plan`, `steps`, `parameters` y `source` son diseño propuesto, no campos confirmados de v0.4.0.

## 3. Errores propuestos

```json
{
  "error": {
    "code": "AMBIGUOUS_REFERENCE",
    "message": "No se pudo determinar a qué pod se refiere 'ese pod'.",
    "candidates": ["api-7db", "pagos-8ac"]
  }
}
```

Los códigos y el formato deben fijarse junto con pruebas de contrato antes de declararse estables.

