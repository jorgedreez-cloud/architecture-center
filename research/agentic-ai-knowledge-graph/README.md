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

Pilot phase, corrected and validated (see `notes/findings.md` FASE 1-5 audit
and corrections). Only 3 documents have been deeply processed so far:

- `docs/ref-arch/RA0024/3-extend-joule-with-joule-studio/readme.md` (`/ref-arch/ff07b1`)
- `docs/ref-arch/RA0029/readme.md` (RA0029 root — "Agentic AI & AI Agents")
- `docs/ref-arch/RA0029/1-a2a-and-mcp/readme.md` (RA0029 subpage — A2A/MCP)

Current pilot output: **5 documents, 32 entities, 27 sources, 95
relationships**. `scripts/validate_pilot.py` passes cleanly against this data:
`RESULT: PASS`, `CRITICAL: none`, `WARNINGS: none`.

The full-repository run (all 118 real RA documents) has **not** been executed
yet — see `notes/findings.md` for what needs deciding before that happens,
including the pending limitations listed below.

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
  validate_pilot.py      read-only validation gate (schemas, duplicate/dangling
                          IDs, data types, evidence literalness, inferred-confidence
                          discipline, TechnologyDomain catalog, Requirement/Constraint
                          atomicity heuristic, JSONL<->JSON<->GraphML<->Cypher count parity)
data/
  raw/        full-site metadata inventory (cheap, front-matter only)
  pilot/      deep extraction output for the 3 pilot documents
  processed/  reserved for the full-repository run (empty for now)
notes/
  findings.md reconciliation analysis, ff07b1 lookup, pilot review
```

## Data model

Closed set of node types: `Document, ReferenceArchitecture, SAPProduct,
SAPService, DeveloperTool, Capability, Agent, Protocol, API, DataSource,
IdentitySecurity, UseCase, Requirement, Constraint, Diagram, Source,
TechnologyDomain`. `TechnologyDomain` is further restricted to a closed
vocabulary: `genai, data, appdev, integration, opsec` - no other front-matter
tag (e.g. `aws, gcp, azure, ibm, cap, build`) is promoted to a node.

Closed set of relationship types: `PART_OF, USES, CONNECTS_TO, REQUIRES,
DEPENDS_ON, EXPOSES, CONSUMES_DATA_FROM, AUTHENTICATES_WITH, EXTENDS,
SUPPORTS_PROTOCOL, DOCUMENTED_BY, HAS_SOURCE, HAS_DIAGRAM, HAS_USE_CASE,
BELONGS_TO_DOMAIN, HAS_REQUIREMENT, HAS_CONSTRAINT, MENTIONS`.

Full definitions, including the SAPProduct/SAPService/DeveloperTool decision
rule and the `__introduction__` source_section convention: `config/ontology.yaml`.

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
pip install -r requirements.txt   # PyYAML (jsonschema optional but used by validate_pilot.py if present)
python3 scripts/extract_inventory.py   # -> data/raw/ra_inventory.json
python3 scripts/extract_document.py    # -> data/pilot/*.jsonl
python3 scripts/build_graph.py         # -> data/pilot/knowledge_graph.{json,graphml,cypher}
python3 scripts/validate_pilot.py      # -> exit 0 + "RESULT: PASS" if the pilot is clean
```

All four scripts are read-only with respect to the SAP content — they only
write inside `research/agentic-ai-knowledge-graph/data/` (`validate_pilot.py`
doesn't even write that; it only reads).

## Known pending limitations (see notes/findings.md for full detail)

- **Naive evidence-position lookup**: the sentence-trigger detector locates a
  matched sentence back in the original document body with a plain substring
  search (`body.find(sentence)`). It works for this 3-document pilot but is
  not hardened against a sentence that wraps across a hard line break -
  needs a more robust position lookup before the full 114-document run.
- **Requirement/Constraint trigger list is intentionally narrow**: only a
  handful of high-precision phrases (`must be set up`, `not yet generally
  available`, `unidirectional`, ...). Expanding it for the full corpus
  requires deliberate, reviewed additions, not silent broadening.
- **`CURATED_FACTS` is still 100% manual for 9 relationships** that need
  semantic judgement beyond a deterministic rule (`AUTHENTICATES_WITH`,
  `CONNECTS_TO`, `EXPOSES`, `CONSUMES_DATA_FROM`, `SUPPORTS_PROTOCOL`,
  `REQUIRES`, `HAS_USE_CASE`). Only the structural/generic relationship types
  (`PART_OF`, `HAS_SOURCE`, `HAS_DIAGRAM`, `BELONGS_TO_DOMAIN`,
  `HAS_REQUIREMENT`, `HAS_CONSTRAINT`, `MENTIONS`) have an automatic,
  deterministic extraction path.
- **Possible redundancy between generic and curated requirement facts**: the
  automatic detector can produce a generic, Document-level `HAS_REQUIREMENT`
  fact for a sentence that a curated fact ALSO models more specifically at
  the entity level (e.g. the IAS trust-relationship sentence in
  `RA0029/1-a2a-and-mcp` is both an auto-detected `HAS_REQUIREMENT` from the
  Document and a curated `REQUIRES` from `entity:agent-gateway`). Both are
  individually correct and verbatim-sourced; this is accepted as a known,
  documented overlap rather than silently merged.

## JSONL as source of truth

JSONL (`data/pilot/*.jsonl`) is the source of truth. `knowledge_graph.json`,
`.graphml` and `.cypher` are derived, regenerable exports (`build_graph.py`).
RDF/Turtle is explicitly out of scope for this phase.

## Loading into Neo4j

```
cat data/pilot/knowledge_graph.cypher | cypher-shell -u neo4j -p <password>
```

Nodes are `MERGE`d on their `id` property alone (never on the full property
map, which would include a fresh `extraction_date` every run); relationships
are matched by endpoint `id` and then `MERGE`d on their own stable
`relationship_id` alone. Both are safe to re-run without creating duplicates
- verified by running `extract_document.py` + `build_graph.py` twice in a
row and diffing the resulting `.cypher` MERGE identity keys (see
`notes/findings.md`).
