# Componentes y contratos internos

## API de chat

Responsabilidad: recibir el mensaje y devolver JSON. Contrato observado: `POST /api/chat` con un campo `message`; respuesta con `session_id`, `reply` y `results`.

## Validador de entrada

Responsabilidad: verificar que el cuerpo tenga la forma mínima esperada y rechazar entradas vacías o inválidas. El esquema exacto y los códigos HTTP requieren confirmación en el código fuente.

## Reconocedor de intenciones

Responsabilidad: aplicar reglas deterministas y normalizar operaciones como `list_pods` y `list_events`. Debe diferenciar intención no soportada y operación de escritura.

## Planificador

Responsabilidad: convertir una o varias intenciones en pasos ordenados. El plan completo se bloquea cuando contiene una escritura reconocida.

## Contexto conversacional propuesto

Responsabilidad v0.5.0: almacenar solo namespace, recursos recientes y referencias resolubles. No posee permisos ni puede modificar la política de ejecución.

## Ejecutor

Responsabilidad: ejecutar únicamente operaciones de lectura que el catálogo permita. Debe devolver resultados normalizados y errores trazables.

## Adaptador de fuente

Responsabilidad: abstraer el modo demo y cualquier fuente DevOps real. La integración real no se afirma como existente porque no fue entregada junto al proyecto.

## Agregador y formateador

Responsabilidad: combinar resultados, generar `reply` y preservar información estructurada para el cliente.

