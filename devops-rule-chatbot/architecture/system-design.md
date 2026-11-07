# Diseño del sistema

## 1. Resumen

El sistema recibe un mensaje, identifica una o más intenciones mediante reglas, construye un plan y ejecuta solo consultas permitidas. La respuesta combina un resumen legible con resultados estructurados. En v0.4.0 el contexto entre pasos no está garantizado; v0.5.0 propone una capa explícita de contexto.

## 2. Vista de contexto

```mermaid
flowchart LR
    U[Usuario o cliente HTTP] --> API[API de chat]
    API --> ORQ[Orquestador conversacional]
    ORQ --> NLU[Reconocedor basado en reglas]
    ORQ --> PLAN[Planificador de consultas]
    PLAN --> POLICY[Política de solo lectura]
    POLICY --> EXEC[Ejecutor de consultas]
    EXEC --> SOURCE[Fuente DevOps o modo demo]
    EXEC --> ORQ
    ORQ --> API
    API --> U
```

La fuente exacta de datos y el adaptador de clúster no están documentados en la evidencia disponible; por eso el diagrama los trata como una dependencia abstracta con modo demo observado.

## 3. Vista de contenedores lógicos

```mermaid
flowchart TD
    A[POST /api/chat] --> B[Validación de entrada]
    B --> C[Reconocimiento de intenciones]
    C --> D{¿Una o varias?}
    D -->|Una| E[Plan de un paso]
    D -->|Varias| F[Plan compuesto]
    E --> G[Política de solo lectura]
    F --> G
    G -->|Permitido| H[Ejecutor]
    G -->|Bloqueado| I[Respuesta de bloqueo]
    H --> J[Agregación de resultados]
    J --> K[Respuesta JSON]
    I --> K
```

## 4. Evolución propuesta

```mermaid
flowchart LR
    P[Plan] --> CTX[Contexto acotado]
    CTX --> PROP[Propagación de entidades]
    PROP --> RES[Resolución de referencias]
    RES --> DEC{¿Ambiguo?}
    DEC -->|Sí| ASK[Solicitar aclaración]
    DEC -->|No| SAFE[Volver a política de solo lectura]
```

El contexto nunca debe saltarse la política de seguridad.

