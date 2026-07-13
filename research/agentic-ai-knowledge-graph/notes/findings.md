# Findings

## FASE 1 — Rama de trabajo

Verificado con `git rev-parse`, `git log`, `git diff --stat` y `git merge-base`:

| Rama | HEAD |
|---|---|
| `origin/dev` | `4635f734f2f82c6497bc53d5cf972b5cbfec5e52` |
| `agentic-ai-research` | `4635f734f2f82c6497bc53d5cf972b5cbfec5e52` |
| `claude/agentic-ai-research-mn4j8f` | `4635f734f2f82c6497bc53d5cf972b5cbfec5e52` |

Las tres apuntan exactamente al mismo commit. `git diff origin/dev..agentic-ai-research --stat` no produce salida (cero diferencias). `git status` en el momento de la verificación: working tree limpio. `agentic-ai-research` parte, sin ninguna duda, del mismo contenido que `origin/dev`.

**Bloqueo de publicación**: `git push -u origin agentic-ai-research` y `mcp__github__create_branch` devuelven ambos `403 Resource not accessible by integration`. Adicionalmente se confirmó que `claude/agentic-ai-research-mn4j8f` (la rama "designada" por el arnés de la sesión) **nunca existió en origin** — no aparece en `git ls-remote origin` ni en `list_branches` vía API, solo como ref local de bootstrap. Es una restricción de permisos de la integración GitHub de esta sesión, no algo resoluble localmente. Todo el trabajo de esta fase se ha hecho y committeado en local sobre `agentic-ai-research`; queda pendiente el `push` cuando se habiliten permisos de escritura.

## FASE 3 — Reconciliación del inventario (114 vs 120 vs 31)

Verificado de forma independiente con `scripts/extract_inventory.py` (ver `data/raw/ra_inventory.json`, campo `summary`):

```json
{
  "total_readme_files": 120,
  "landing_page_count": 1,
  "ra_folder_count_total": 32,
  "ra_folder_count_real": 31,
  "real_ra_docs": 118,
  "real_ra_root_docs": 31,
  "real_ra_subpage_docs": 87,
  "draft_count": 4,
  "unlisted_count": 2,
  "published_and_listed_count": 114
}
```

**La aritmética exacta es 120 − 4 − 2 = 114.**

| Categoría | Cantidad | Detalle |
|---|---|---|
| Total `readme.md` bajo `docs/ref-arch/` | 120 | 1 landing page (`docs/ref-arch/readme.md`) + 119 dentro de carpetas `RAxxxx` |
| Landing page de la sección | 1 | `docs/ref-arch/readme.md` — página conceptual "What are SAP Reference Architectures?", no es ella misma una RA |
| Carpetas `RAxxxx` | 32 | `RA0000`–`RA0031` |
| — Plantilla demo | 1 | `RA0000` (contenido lorem ipsum, `unlisted: true`) |
| — Arquitecturas reales | **31** | `RA0001`–`RA0031` |
| Documentos de arquitecturas reales | 118 | 31 páginas raíz + 87 subpáginas |
| `draft: true` (excluidos del build de Docusaurus por completo) | 4 | `RA0005/4-conversational-ai`, `RA0005/5-pillars`, `RA0005/6-vibe-code-with-cline`, `RA0024/4-integrate-joule-and-microsoft-copilot` |
| `unlisted: true` (compilados y desplegados, pero ocultos de navegación/búsqueda) | 2 | `RA0000` (raíz demo) y `RA0024/1-joule-for-sap-s4-hana` |
| **Publicados y listados en la interfaz pública** | **114** | 120 − 4 (draft) − 2 (unlisted) |

**Documentos que NO deberían incluirse en el knowledge graph**: `RA0000` (plantilla, contenido de relleno) y los 4 `draft: true` (contenido no estable/no publicado). Los 2 `unlisted: true` sí son contenido real y publicado — deberían incluirse (solo están ocultos de la navegación del sitio, no del build).

**No hay documentos duplicados.** Los 87 "subdocumentos" son páginas jerárquicas legítimas con `id`/`slug` propios y únicos (verificado: 120 valores de `id` en front matter, sin colisiones).

## FASE 4 — Localización de `ff07b1`

Búsqueda exacta (`grep -rn "ff07b1"` sobre el árbol completo, sin necesidad de historial):

- **Archivo fuente**: `docs/ref-arch/RA0024/3-extend-joule-with-joule-studio/readme.md`
- **Título**: "Extend Joule with Joule Studio"
- **RA**: RA0024 — "Integrating and Extending Joule", subpágina 3
- **URL pública**: `https://architecture.learning.sap.com/docs/ref-arch/ff07b1`
- **Estado**: publicado (`draft: false`, `unlisted: false`), `last_update`: 2026-03-12, autor `fabianleh`
- **Evidencia de historial** (`src/constant/config-plugin-client-redirects.ts:368` + `git log`):
  - `83b76e9` (2026-06-29) "Migration to flat unique document slugs independent from any parent document slug (#1127)" — migró todo el sitio de un esquema `RAxxxx-hash/N` a IDs planos por documento. URL antigua: `/docs/ref-arch/06ff6062dc/3` (donde `06ff6062dc` era el ID antiguo de RA0024, `/3` el índice de subpágina). Redirect explícito en el código: `{ from: '/docs/ref-arch/06ff6062dc/3', to: '/docs/ref-arch/ff07b1' }`.
  - `5ff9fcf` (2026-05-08) "Restructure AI Content (#1022)" reorganizó la carpeta a su ubicación actual.
  - No aparece en ningún tag todavía — el cambio vive en `dev`, posterior al último release etiquetado.

**Conclusión**: `ff07b1` no es un slug histórico ni eliminado — es el slug canónico vigente, resultado de una migración documentada de IDs (no se ha inventado ninguna correspondencia; toda la cadena de evidencia está en el propio repositorio).

## FASE 7 — Revisión del piloto

### Archivos creados

```
research/agentic-ai-knowledge-graph/
├── README.md
├── requirements.txt
├── config/topics.yaml
├── config/ontology.yaml
├── schemas/document.schema.json
├── schemas/entity.schema.json
├── schemas/relationship.schema.json
├── schemas/source.schema.json
├── scripts/extract_inventory.py
├── scripts/extract_document.py
├── scripts/build_graph.py
├── data/raw/ra_inventory.json          (120 documentos, metadatos completos)
├── data/pilot/documents.jsonl          (5 filas: 3 Document + 2 ReferenceArchitecture)
├── data/pilot/entities.jsonl           (34 filas, 13 tipos de nodo distintos)
├── data/pilot/sources.jsonl            (27 filas, deduplicadas por URL)
├── data/pilot/external_links.jsonl     (32 filas, sin deduplicar, un registro por aparición)
├── data/pilot/relationships.jsonl      (105 filas)
├── data/pilot/knowledge_graph.json     (66 nodos, 105 aristas)
├── data/pilot/knowledge_graph.graphml  (XML bien formado, validado)
├── data/pilot/knowledge_graph.cypher   (66 MERGE de nodo + 105 MERGE de arista)
└── notes/findings.md                   (este archivo)
```

Ningún archivo dentro de `docs/`, `src/` u otras carpetas originales de SAP ha sido modificado.

### Entidades detectadas (34, `entities.jsonl`)

| Tipo | Cantidad | Ejemplos |
|---|---|---|
| TechnologyDomain | 9 | genai, agents, appdev, build, aws, gcp, azure, ibm, cap (de `tags` del front matter) |
| SAPService | 7 | SAP BTP, Joule Studio, SAP HANA Cloud, SAP AI Core, SAP Build Process Automation, Generative AI Hub, SAP Cloud SDK for AI |
| Diagram | 3 | `joule-studio-ref-arch.drawio`, `architecture.drawio` (×2, uno por documento RA0029) |
| SAPProduct | 3 | SAP S/4HANA, SAP Build, SAP Business Data Cloud |
| Protocol | 2 | Agent2Agent (A2A) Protocol, Model Context Protocol (MCP) |
| API | 2 | Agent Gateway, MCP Gateway (SAP Integration Suite) |
| UseCase | 2 | Automated Customer Support Agent Deployment, Procurement Negotiation and Compliance |
| Agent | 1 | Joule |
| IdentitySecurity | 1 | SAP Cloud Identity Services |
| Capability | 1 | Retrieval Augmented Generation (RAG) |
| DataSource | 1 | SAP Knowledge Graph |
| Requirement | 1 | "Joule + SAP Build Process Automation must be provisioned before Joule Studio" |
| Constraint | 1 | "Agent Gateway not GA — unidirectional (outbound) only" |

Todas con `evidence_type: explicit` salvo ninguna entidad (la distinción explicit/inferred se aplica a las relaciones, no a la existencia de la entidad — una entidad solo se crea si hay una mención literal).

### Relaciones generadas (105 total)

Genéricas (estructurales/deterministas, todas `explicit`, confianza ≥0.85):

| Tipo | Cantidad | Origen |
|---|---|---|
| MENTIONS | 35 | Document → Entity (lexicón canónico, primera aparición literal) |
| HAS_SOURCE | 32 | Document → Source (cada enlace markdown externo) |
| BELONGS_TO_DOMAIN | 18 | Document → TechnologyDomain (tags del front matter) |
| PART_OF | 3 | Document → ReferenceArchitecture (estructura de carpetas) |
| HAS_DIAGRAM | 3 | Document → Diagram (embeds `![](drawio/....drawio)`) |

Específicas (curadas manualmente contra el texto exacto — ver advertencia más abajo):

| Tipo | Origen → Destino | evidence_type | confianza |
|---|---|---|---|
| AUTHENTICATES_WITH | Joule Studio → SAP Cloud Identity Services | explicit | 0.95 |
| REQUIRES | Joule Studio → Requirement (provisioning) | explicit | 0.90 |
| HAS_USE_CASE | RA0024 → UseCase (Customer Support Agent) | explicit | 0.90 |
| HAS_USE_CASE | RA0024 → UseCase (Procurement Negotiation) | explicit | 0.90 |
| CONNECTS_TO | Joule Studio → SAP S/4HANA | **inferred** | **0.50** |
| USES | Joule → Generative AI Hub | **inferred** | **0.55** |
| CONSUMES_DATA_FROM | Generative AI Hub → SAP HANA Cloud | explicit | 0.85 |
| AUTHENTICATES_WITH | Joule → SAP Cloud Identity Services | explicit | 0.85 |
| SUPPORTS_PROTOCOL | Joule → A2A Protocol | explicit | 0.95 |
| SUPPORTS_PROTOCOL | Joule → MCP Protocol | explicit | 0.85 |
| EXPOSES | Agent Gateway → A2A Protocol | explicit | 0.95 |
| AUTHENTICATES_WITH | Agent Gateway → SAP Cloud Identity Services | explicit | 0.95 |
| CONSUMES_DATA_FROM | MCP Protocol → SAP Knowledge Graph | explicit | 0.90 |
| REQUIRES | Agent Gateway → SAP Cloud Identity Services | explicit | 0.85 |

Cada fila lleva su cita textual verbatim en `evidence` (ver `data/pilot/relationships.jsonl`); ninguna relación existe sin evidencia, tal como exige FASE 2.

### Relaciones dudosas o inferidas (2 de 105)

Marcadas explícitamente `evidence_type: inferred`, confianza ≤0.6:

1. **`CONNECTS_TO` Joule Studio → SAP S/4HANA** (conf. 0.50) — la frase fuente lista varios sistemas ("SAP ECC, SAP S/4HANA, and S/4HANA Cloud Private Edition") sin un verbo que conecte explícitamente Joule Studio a cada uno por separado; se interpretó la pertenencia a la lista como relación.
2. **`USES` Joule → Generative AI Hub** (conf. 0.55) — la arquitectura describe Generative AI Hub como un bloque junto a Joule en el diagrama/texto, pero la frase no dice literalmente "Joule usa Generative AI Hub"; es una inferencia razonable a partir del contexto arquitectónico, no una afirmación textual directa.

### Errores y ausencias de datos

1. **Push bloqueado (FASE 1)** — ver arriba; no resoluble desde esta sesión.
2. **Brecha de ontología detectada**: el conjunto cerrado de tipos de relación (FASE 2) no incluye un tipo específico "tiene restricción" ni "tiene requisito" a nivel de documento — solo existe `HAS_USE_CASE` para casos de uso. Por eso el nodo `Constraint` ("Agent Gateway not GA...") quedó enlazado con el genérico `MENTIONS` en vez de una relación semánticamente más precisa. Recomendación para la siguiente fase: añadir `HAS_CONSTRAINT` y `HAS_REQUIREMENT` al conjunto cerrado, o documentar `MENTIONS` como el catch-all intencional para estos dos tipos.
3. **Extracción de relaciones específicas no está automatizada todavía**: las 14 relaciones "curadas" (tabla de arriba) fueron verificadas manualmente frase por frase contra el texto de los 3 documentos piloto (diccionario `CURATED_FACTS` en `scripts/extract_document.py`), no producidas por un motor NLP genérico. Esto es intencional y transparente para un piloto de 3 documentos, pero **no escala** a los 118 documentos reales sin: (a) un extractor de relaciones más sistemático (reglas ampliadas o asistido por LLM con el mismo requisito de cita verbatim), o (b) aceptar que la fase de relaciones específicas seguirá siendo manual/curada por lotes.
4. **`GraphRAG` no aparece en ningún documento del corpus** (0 coincidencias en 120 archivos, confirmado en el análisis inicial) — dato relevante en sí mismo para la investigación, no un error.
5. **Ningún archivo `.drawio` referenzado por los 3 documentos piloto falta en disco** — el chequeo de `extract_document.py` no emitió advertencias.
6. **Ningún extremo de relación queda "colgante"** (`build_graph.py` verifica que cada `from_id`/`to_id` resuelva a un nodo conocido) — 0 advertencias en la ejecución del piloto.

### No se ha hecho (respetando el alcance de FASE 6/7)

- No se ha procesado el resto de las 31 arquitecturas (115 documentos reales restantes).
- No se ha abierto ninguna pull request contra SAP.
- No se ha fusionado nada con `dev`.
- No se ha descargado el contenido completo de ninguna fuente externa — `external_links.jsonl` solo inventaria URL, título del enlace, dominio clasificado y documento de procedencia.

## FASE 8 — Auditoría de calidad del piloto y correcciones aplicadas

Tras la revisión de FASE 7 se ejecutó una auditoría crítica dedicada del piloto (13 puntos: duplicados, SAPProduct/SAPService, entidades genéricas, MENTIONS ruidoso, relaciones inferred, evidencia insuficiente, evidencia demasiado larga/imprecisa, Requirement/Constraint no atómicos, normalización de entidades, enlaces mal clasificados, campos obligatorios, GraphML/Cypher vs JSONL, riesgo de escalado a 114 documentos). Se encontraron y corrigieron los siguientes problemas, todos dentro de `research/agentic-ai-knowledge-graph/` (ningún archivo de `docs/`/`src/` fue tocado):

**Críticos:**
- `scripts/build_graph.py` generaba `MERGE` en Cypher sobre el mapa de propiedades **completo** (incluyendo `extraction_date`, un timestamp distinto en cada ejecución), lo que habría duplicado todo el grafo en Neo4j en cada re-extracción. Corregido: `MERGE (n:Label {id: ...}) SET n += {...}` para nodos, `MERGE (a)-[r:TYPE {relationship_id: ...}]->(b) SET r += {...}` para relaciones. Verificado empíricamente ejecutando `extract_document.py` + `build_graph.py` dos veces seguidas y comparando las claves de `MERGE`: idénticas byte a byte pese al cambio de `extraction_date`.
- `CURATED_FACTS` era la única vía de extracción de relaciones específicas (14 hechos para 3 documentos, no escalable a 114). Se implementó una capa automática y determinista para `PART_OF`, `HAS_SOURCE`, `HAS_DIAGRAM` (ahora también `.svg`, no solo `.drawio`), `BELONGS_TO_DOMAIN` y `MENTIONS`, más un detector de frases-disparador para `HAS_REQUIREMENT`/`HAS_CONSTRAINT`. `CURATED_FACTS` quedó reducido a 9 hechos que requieren juicio semántico genuino (`AUTHENTICATES_WITH`, `CONNECTS_TO`, `EXPOSES`, `CONSUMES_DATA_FROM`, `SUPPORTS_PROTOCOL`, `REQUIRES`, `HAS_USE_CASE`), documentado explícitamente como capa de *override*, no como vía principal.

**Altos:**
- `TechnologyDomain` promovía cualquier tag de front matter a nodo (incluyendo `aws/gcp/azure/ibm/cap/build`), contradiciendo el propio comentario de `ontology.yaml`. Restringido al vocabulario cerrado `genai, data, appdev, integration, opsec`, leído desde `config/ontology.yaml` (no hardcodeado). Resultado: de 9 `TechnologyDomain` a 2 (`genai`, `appdev`), cada uno correctamente atribuido a los 2-3 documentos donde aparece.
- GraphML/Cypher perdían el tipo real de `confidence`/`draft`/`unlisted`/`sidebar_position` (todo forzado a `string`). Corregido: `attr.type` real por campo en GraphML (`double`/`int`/`boolean`/`string`), `null` tratado explícitamente (se omite el `<data>` en vez de escribir el texto `"None"`); Cypher con números sin comillas, booleanos `true`/`false`, listas como listas, `null` como palabra clave.
- `Requirement`/`Constraint` no eran atómicos ni tenían relación específica. Añadidos `HAS_REQUIREMENT`/`HAS_CONSTRAINT` al conjunto cerrado de `ontology.yaml`. El requisito compuesto de Joule Studio se dividió en 3 hechos atómicos (`req-joule-provisioned`, `req-build-process-automation-provisioned`, `req-ias-utilized-for-provisioning`); la restricción del Agent Gateway se dividió en 2 (`constraint-agent-gateway-not-ga`, `constraint-unidirectional-outbound-only`), esta última ahora correctamente atribuida a **ambos** documentos que comparten el disclaimer (`doc:98efa0` y `doc:76ec36` — el piloto anterior solo la capturaba de uno).

**Medios:**
- `MENTIONS` se generaba desde títulos de enlace y secciones secundarias (`Resources`/`Examples`). Corregido con `find_substantive_alias_match`: excluye el texto ancla de enlaces markdown y las secciones del vocabulario cerrado `secondary_section_headings` (`Resources`, `Examples`, `Examples in an SAP context` — deliberadamente **sin** incluir `Services and Components`, que es contenido arquitectónico primario). Efecto colateral positivo: `MENTIONS doc:76ec36 → entity:sap-btp` no desapareció, sino que su evidencia mejoró de un título de enlace incidental a una mención sustantiva real en "SAP's Commitment to Open Standards".
- Evidencia con elipsis (`CONSUMES_DATA_FROM generative-ai-hub → sap-hana-cloud`) sustituida por la cita completa verbatim.
- `USES entity:joule → entity:generative-ai-hub` (inferred, 0.55) eliminada por completo: su evidencia solo describía Generative AI Hub, nunca mencionaba a Joule.
- Alias de `sap-ai-core` ampliado con `"ai core"` (bare) y `sap-btp` con `"btp"` (bare) — únicas 2 ampliaciones autorizadas más allá de las ya existentes. Efecto: `entity:sap-ai-core` ahora usa como evidencia la frase sustantiva de la sección Flow ("AI Core plays a crucial role...") en vez del enlace de bajo valor en Services and Components.
- `found_in_document` (string, solo el primer documento visto) → `found_in_documents` (lista, acumulada en cada documento donde la entidad reaparece).
- `source_section: null` para texto anterior al primer encabezado → convención explícita `"__introduction__"`.
- `domain_category` ampliado con `SAP.com` (`www.sap.com` y cualquier otro subdominio `*.sap.com` no cubierto por una regla más específica) y `SAP BTP Hosted App` (`*.hana.ondemand.com`, incluye `*.cfapps.<region>.hana.ondemand.com`). Sin inspeccionar nunca parámetros de query ni credenciales — la clasificación usa solo el hostname.
- `SAP Cloud SDK for AI` reclasificado de `SAPService` a un nuevo tipo de nodo `DeveloperTool` (SDK cliente sin runtime propio aprovisionable), con la regla de decisión SAPProduct/SAPService/DeveloperTool documentada en `config/ontology.yaml`.

**Bugs adicionales encontrados durante la implementación (no estaban en el diagnóstico original de FASE 7):**
1. El fence de admonición de Docusaurus (`:::info Disclaimer`) se colaba en la primera frase detectada automáticamente, rompiendo el *override* atómico documentado para esa frase exacta. Corregido con `ADMONITION_FENCE_RE` en `scripts/extract_document.py`.
2. `type` se serializaba dos veces por nodo/arista en GraphML (bug preexistente al pipeline original, no introducido en esta ronda, detectado al inspeccionar la salida). Corregido excluyendo `type` de `raw_props()` en `scripts/build_graph.py`.
3. La evidencia de `AUTHENTICATES_WITH agent-gateway → sap-cloud-identity-services` tenía un punto final que no existe en el texto fuente. Detectado por `scripts/validate_pilot.py` (comprobación de literalidad contra el archivo fuente) y corregido.

### Resultado final tras las correcciones

| Métrica | Antes de FASE 8 | Después de FASE 8 |
|---|---|---|
| Documentos | 5 | 5 |
| Entidades | 34 | **32** |
| Fuentes | 27 | **27** |
| Relaciones | 105 | **95** |
| Nodos totales (grafo) | 66 | 64 |
| Aristas (grafo) | 105 | 95 |

`python3 scripts/validate_pilot.py` (11 categorías de chequeo: esquemas, IDs duplicados, referencias colgantes, tipos de datos, entidades sin fuente, evidencia vacía/no literal, `inferred` con `confidence > 0.6` o evidencia unilateral, `TechnologyDomain` fuera de catálogo, atomicidad de `Requirement`/`Constraint`, paridad de conteos JSONL↔JSON↔GraphML↔Cypher):

```
documents=5 entities=32 sources=27 relationships=95

CRITICAL: none

WARNINGS: none

RESULT: PASS
```

Verificado además, ejecutando el pipeline dos veces seguidas: el número de nodos (64) y de relaciones (95) no cambia, y las claves de identidad de `MERGE` en el `.cypher` (por `id` en nodos, por `relationship_id` en relaciones) son idénticas entre ejecuciones — re-ejecutar el pipeline **no** produciría duplicados en Neo4j.

### Cómo reproducir

```bash
cd research/agentic-ai-knowledge-graph
python3 scripts/extract_document.py
python3 scripts/build_graph.py
python3 scripts/validate_pilot.py
```

### Limitaciones pendientes (no bloqueantes para este piloto de 3 documentos, sí antes de los 114)

1. **Búsqueda de posición de evidencia ingenua**: el detector de `Requirement`/`Constraint` localiza la frase coincidente en el cuerpo original con `body.find(sentence)` (substring simple). Funciona en este piloto pero no está probado contra frases que crucen un salto de línea duro.
2. **Triggers de `Requirement`/`Constraint` todavía estrechos**: solo 2-4 patrones de alta precisión por tipo (`must be set up`, `not yet generally available`, `unidirectional`, ...). Ampliarlos para el corpus completo requiere revisión deliberada, no ampliación silenciosa.
3. **`CURATED_FACTS` sigue siendo 100% manual** para las 9 relaciones que requieren juicio semántico (`AUTHENTICATES_WITH`, `CONNECTS_TO`, `EXPOSES`, `CONSUMES_DATA_FROM`, `SUPPORTS_PROTOCOL`, `REQUIRES`, `HAS_USE_CASE`). Solo las relaciones estructurales/genéricas tienen ruta automática.
4. **Posible redundancia entre requisito genérico y hecho curado**: `HAS_REQUIREMENT doc:76ec36 → Requirement:dcae226f5b` (genérico, a nivel de Documento, auto-detectado) coexiste con `REQUIRES entity:agent-gateway → entity:sap-cloud-identity-services` (curado, a nivel de entidad) para el mismo hecho real de la frase sobre el trust relationship IAS App2App. Ambos son correctos y verbatim; se deja documentado como solapamiento aceptado, no fusionado.
