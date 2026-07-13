# Agentic AI Knowledge Graph — research project

Research tooling to build a provenance-anchored knowledge graph from the
public SAP Architecture Center content in this repository (`docs/ref-arch/`,
plus `docs/agent/`, `docs/golden-path/`, `docs/north-star-arch/`), focused on:
agentic AI / AI agents, Joule & Joule Studio, SAP BTP, SAP Business Data
Cloud, SAP HANA Cloud, SAP Datasphere, SAP S/4HANA, knowledge graphs, and
RAG/GraphRAG.

**This folder is independent research tooling.** It only *reads* files under
`docs/` and `src/`; it never writes to them. Nothing here is part of the
Docusaurus site build.

## Status

Pilot phase (FASE 6). Only 3 documents have been deeply processed so far:

- `docs/ref-arch/RA0024/3-extend-joule-with-joule-studio/readme.md` (`/ref-arch/ff07b1`)
- `docs/ref-arch/RA0029/readme.md` (RA0029 root — "Agentic AI & AI Agents")
- `docs/ref-arch/RA0029/1-a2a-and-mcp/readme.md` (RA0029 subpage — A2A/MCP)

The full-repository run (all 118 real RA documents) has **not** been executed
yet — see `notes/findings.md` for what needs deciding before that happens.

## Layout

```
config/
  topics.yaml      target topics + keyword lexicon
  ontology.yaml     node/relationship types + canonical entity registry (dedup)
schemas/            JSON Schema for every record type (mandatory provenance fields)
scripts/
  extract_inventory.py   full front-matter inventory of all docs/ref-arch readmes -> data/raw/ra_inventory.json
  extract_document.py    deep, evidence-anchored extraction for a named list of documents -> data/pilot/*.jsonl
  build_graph.py         merges pilot jsonl -> knowledge_graph.json / .graphml / .cypher
data/
  raw/        full-site metadata inventory (cheap, front-matter only)
  pilot/      deep extraction output for the 3 pilot documents
  processed/  reserved for the full-repository run (empty for now)
notes/
  findings.md reconciliation analysis, ff07b1 lookup, pilot review
```

## Data model

Closed set of node types: `Document, ReferenceArchitecture, SAPProduct,
SAPService, Capability, Agent, Protocol, API, DataSource, IdentitySecurity,
UseCase, Requirement, Constraint, Diagram, Source, TechnologyDomain`.

Closed set of relationship types: `PART_OF, USES, CONNECTS_TO, REQUIRES,
DEPENDS_ON, EXPOSES, CONSUMES_DATA_FROM, AUTHENTICATES_WITH, EXTENDS,
SUPPORTS_PROTOCOL, DOCUMENTED_BY, HAS_SOURCE, HAS_DIAGRAM, HAS_USE_CASE,
BELONGS_TO_DOMAIN, MENTIONS`.

Full definitions: `config/ontology.yaml`.

Every node and every relationship carries mandatory provenance
(`schemas/*.schema.json`): `source_id, source_url, source_file,
source_section, evidence, evidence_type (explicit|inferred), confidence,
repository_commit, extraction_date`. **No relationship is written without a
verbatim `evidence` quote.** `explicit` facts are literal (named mentions,
markdown links, front matter, folder structure); `inferred` facts require
interpretation and are capped at `confidence <= 0.6`.

## Running the pilot

```bash
cd research/agentic-ai-knowledge-graph
pip install -r requirements.txt   # PyYAML (jsonschema optional)
python3 scripts/extract_inventory.py   # -> data/raw/ra_inventory.json
python3 scripts/extract_document.py    # -> data/pilot/*.jsonl
python3 scripts/build_graph.py         # -> data/pilot/knowledge_graph.{json,graphml,cypher}
```

All three scripts are read-only with respect to the SAP content — they only
write inside `research/agentic-ai-knowledge-graph/data/`.

## JSONL as source of truth

JSONL (`data/pilot/*.jsonl`) is the source of truth. `knowledge_graph.json`,
`.graphml` and `.cypher` are derived, regenerable exports (`build_graph.py`).
RDF/Turtle is explicitly out of scope for this phase.

## Loading into Neo4j

```
cat data/pilot/knowledge_graph.cypher | cypher-shell -u neo4j -p <password>
```

Nodes are `MERGE`d on their `id` property (dedupe-safe to re-run); edges are
matched by endpoint `id` then `MERGE`d with their own properties.
