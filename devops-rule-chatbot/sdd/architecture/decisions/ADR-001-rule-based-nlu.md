# ADR 001 NLU basado en reglas

## Decisión

Mantener un reconocedor determinista basado en reglas para la v0.4.0 y la propuesta v0.5.0.

## Motivo

El sistema se define como un asistente sin IA; las reglas hacen explícito qué consultas se aceptan y permiten pruebas reproducibles.

## Consecuencias

El vocabulario y las variantes lingüísticas deben mantenerse en un catálogo. La cobertura será limitada, pero el comportamiento será auditable.

