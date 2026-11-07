# Supuestos y estado de implementación

## Hechos usados como línea base

- El proyecto se denomina Asistente Conversacional DevOps o DevOps Rule Chatbot.
- La v0.4.0 fue descrita como basada en reglas y sin IA.
- Se observó `POST /api/chat`.
- Se observó una respuesta con `session_id`, `reply` y `results`.
- Se observó planificación de pods y eventos en una solicitud compuesta.
- Se describió una protección que bloquea escrituras reconocidas.
- Se indicó un máximo de cinco consultas por mensaje.

## Riesgos de documentación

- No se recibió el repositorio ni el ZIP original en este espacio.
- Los nombres de módulos, clases, archivos, códigos HTTP y configuración de despliegue no están verificados.
- La integración con una fuente DevOps real no debe darse por implementada.

## Convención

Cuando un elemento no está confirmado, este documento usa “propuesto”, “abstracto” o “no afirmado”. La implementación v0.5.0 debe actualizar esta página y las pruebas cuando exista evidencia reproducible.

