#!/usr/bin/env python3
"""
Validation gate for a pilot output directory (default: data/pilot). Read-
only: never modifies any file. Exits with status 1 if any CRITICAL check
fails, 0 otherwise (WARNINGs do not fail the run but are always printed).

Checks implemented (see notes/findings.md FASE 5 / user FASE 5 request):
  1. Every JSONL row validates against its schemas/*.schema.json.
  2. No duplicate node_id (documents+entities+sources) or relationship_id.
  3. No dangling relationship endpoint (from_id/to_id must resolve to a
     known node).
  4. Data types: confidence is a float in [0,1]; draft/unlisted are bool
     or null; sidebar_position is int or null.
  5. Every entity has a non-empty source_file and source_url-or-source_id
     (i.e. is traceable to something).
  6. Every relationship has a non-empty, literal `evidence` string.
  7. evidence_type=explicit rows: evidence must not contain an ellipsis
     ("...") and must appear verbatim (modulo markdown bold/italic
     markers) in the cited source_file, for every distinct source_file
     referenced by the loaded documents/entities/sources/relationships
     that exists on disk (not a fixed list - this generalizes to whatever
     document set the pilot's manifest covered).
  8. evidence_type=inferred rows: confidence must be <= 0.6, AND the
     evidence text must reference both endpoints of the relationship
     (checked heuristically against each endpoint node's `name`).
  9. TechnologyDomain entities must have `name` in the closed vocabulary
     from config/ontology.yaml (technology_domain_allowed_tags).
 10. Requirement/Constraint entities: flag as non-atomic (heuristic) any
     node whose `name` trips more than one of the sentence-trigger
     patterns used to detect it in the first place.
 11. Count parity: len(documents)+len(entities)+len(sources) in JSONL ==
     nodes in knowledge_graph.json == <node> elements in .graphml ==
     node MERGE statements in .cypher; same for relationships/edges.

Usage:
    python3 scripts/validate_pilot.py
    python3 scripts/validate_pilot.py --input-dir data/pilot2
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from path_safety import PathSecurityError, resolve_within, validate_ra_document_path

RESEARCH_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = os.path.abspath(os.path.join(RESEARCH_ROOT, "..", ".."))
DEFAULT_DIR = "data/pilot"
SCHEMAS_DIR = os.path.join(RESEARCH_ROOT, "schemas")
DATA_ROOT = os.path.join(RESEARCH_ROOT, "data")


def resolve_input_dir(raw: str) -> str:
    """Validate --input-dir: must resolve inside data/, must already exist
    as a directory. See audit finding F3."""
    return resolve_within(
        raw, base_dir=RESEARCH_ROOT, allowed_root=DATA_ROOT,
        must_exist=True, must_be_dir=True, label="--input-dir",
    )

try:
    import jsonschema
    HAVE_JSONSCHEMA = True
except ImportError:
    HAVE_JSONSCHEMA = False

import yaml

REQUIREMENT_TRIGGERS = [
    re.compile(r"\bmust be set up\b", re.IGNORECASE),
    re.compile(r"\bmust be provisioned\b", re.IGNORECASE),
    re.compile(r"\bmust be established\b", re.IGNORECASE),
    re.compile(r"\bmust utilize\b", re.IGNORECASE),
    re.compile(r"\bis required\b", re.IGNORECASE),
]
CONSTRAINT_TRIGGERS = [
    re.compile(r"\bnot yet generally available\b", re.IGNORECASE),
    re.compile(r"\bunidirectional\b", re.IGNORECASE),
]


class Report:
    def __init__(self):
        self.critical: list[str] = []
        self.warnings: list[str] = []

    def crit(self, msg: str):
        self.critical.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)

    def ok(self) -> bool:
        return not self.critical


def read_jsonl(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def strip_markdown_emphasis(text: str) -> str:
    return re.sub(r"[*_`]", "", text)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# 1. Schema validation
# ---------------------------------------------------------------------------
def check_schemas(report: Report, documents, entities, sources, relationships):
    if not HAVE_JSONSCHEMA:
        report.warn("jsonschema package not installed - skipping formal schema validation "
                    "(falling back to required-field presence only, done in other checks).")
        return
    schema_map = {
        "document.schema.json": documents,
        "entity.schema.json": entities,
        "source.schema.json": sources,
        "relationship.schema.json": relationships,
    }
    for schema_name, rows in schema_map.items():
        with open(os.path.join(SCHEMAS_DIR, schema_name), encoding="utf-8") as f:
            schema = json.load(f)
        validator = jsonschema.Draft7Validator(schema)
        for row in rows:
            for err in validator.iter_errors(row):
                ident = row.get("node_id") or row.get("relationship_id") or row.get("source_id") or "?"
                report.crit(f"[schema:{schema_name}] {ident}: {err.message}")


# ---------------------------------------------------------------------------
# 2. Duplicate IDs
# ---------------------------------------------------------------------------
def check_duplicate_ids(report: Report, documents, entities, sources, relationships):
    node_ids = [d["node_id"] for d in documents] + [e["node_id"] for e in entities] + [s["source_id"] for s in sources]
    seen = set()
    for nid in node_ids:
        if nid in seen:
            report.crit(f"[duplicate-id] node id appears more than once: {nid}")
        seen.add(nid)

    rel_ids = [r["relationship_id"] for r in relationships]
    seen_r = set()
    for rid in rel_ids:
        if rid in seen_r:
            report.crit(f"[duplicate-id] relationship_id appears more than once: {rid}")
        seen_r.add(rid)
    return seen  # all known node ids, reused by check_dangling_refs


# ---------------------------------------------------------------------------
# 3. Dangling references
# ---------------------------------------------------------------------------
def check_dangling_refs(report: Report, relationships, known_node_ids):
    for r in relationships:
        if r["from_id"] not in known_node_ids:
            report.crit(f"[dangling-ref] {r['relationship_id']}: from_id {r['from_id']} does not resolve to a known node")
        if r["to_id"] not in known_node_ids:
            report.crit(f"[dangling-ref] {r['relationship_id']}: to_id {r['to_id']} does not resolve to a known node")


# ---------------------------------------------------------------------------
# 4. Data types
# ---------------------------------------------------------------------------
def check_data_types(report: Report, documents, entities, sources, relationships):
    for label, rows in (("document", documents), ("entity", entities), ("source", sources), ("relationship", relationships)):
        for row in rows:
            ident = row.get("node_id") or row.get("relationship_id") or row.get("source_id") or "?"
            conf = row.get("confidence")
            if conf is not None and not (isinstance(conf, (int, float)) and not isinstance(conf, bool) and 0.0 <= conf <= 1.0):
                report.crit(f"[type:{label}] {ident}: confidence must be a float in [0,1], got {conf!r}")
            for bool_field in ("draft", "unlisted"):
                if bool_field in row and row[bool_field] is not None and not isinstance(row[bool_field], bool):
                    report.crit(f"[type:{label}] {ident}: {bool_field} must be bool or null, got {row[bool_field]!r}")
            if "sidebar_position" in row and row["sidebar_position"] is not None and not isinstance(row["sidebar_position"], int):
                report.crit(f"[type:{label}] {ident}: sidebar_position must be int or null, got {row['sidebar_position']!r}")


# ---------------------------------------------------------------------------
# 5. Entities without a traceable source
# ---------------------------------------------------------------------------
def check_entities_have_source(report: Report, entities):
    for e in entities:
        if not e.get("source_file"):
            report.crit(f"[no-source] {e['node_id']}: missing source_file")
        if not e.get("found_in_documents"):
            report.crit(f"[no-source] {e['node_id']}: found_in_documents is empty")


# ---------------------------------------------------------------------------
# 6/7/8. Evidence checks
# ---------------------------------------------------------------------------
def collect_source_files(*row_lists: list[dict]) -> set[str]:
    """Every distinct source_file referenced by any loaded row - not a
    fixed document list, so this generalizes to whatever manifest the
    pilot run under validation actually covered."""
    files: set[str] = set()
    for rows in row_lists:
        for row in rows:
            sf = row.get("source_file")
            if sf:
                files.add(sf)
    return files


def load_source_bodies(source_files: set[str]) -> tuple[dict[str, str], list[str]]:
    """Returns (bodies, rejected). Only source_file values that resolve
    (after collapsing ../ and symlinks) inside REPO_ROOT, under
    docs/ref-arch/, ending in .md, are opened - see audit finding F3.
    Anything else is skipped (its verbatim-evidence check is skipped, same
    as the pre-existing "file not found on disk" behavior) and reported
    back so main() can surface it as a WARNING."""
    bodies = {}
    rejected = []
    for rel_path in source_files:
        try:
            real = resolve_within(
                rel_path, base_dir=REPO_ROOT, allowed_root=REPO_ROOT,
                must_exist=True, must_be_file=True, label="source_file",
            )
            validate_ra_document_path(real, REPO_ROOT, label="source_file")
        except PathSecurityError:
            rejected.append(rel_path)
            continue
        with open(real, encoding="utf-8") as f:
            bodies[rel_path] = f.read()
    return bodies, rejected


# Structural relationship types whose "evidence" is a synthesized
# description of a fact derived from repo structure / front matter (folder
# membership, a tag being present) rather than a quote from the document
# BODY - the ontology explicitly allows this ("a structural fact derivable
# from the repository itself, e.g. folder membership or front matter
# fields" - config/ontology.yaml). These are exempt from the
# verbatim-in-body substring check, but NOT from the empty-evidence or
# ellipsis checks.
STRUCTURAL_RELATIONSHIP_TYPES = {"PART_OF", "BELONGS_TO_DOMAIN"}


def check_relationship_evidence(report: Report, relationships, entity_by_id, doc_by_id, source_bodies):
    for r in relationships:
        evidence = r.get("evidence") or ""
        if not evidence.strip():
            report.crit(f"[no-evidence] {r['relationship_id']}: empty evidence")
            continue

        if r["evidence_type"] == "explicit":
            if "..." in evidence or "…" in evidence:
                report.crit(f"[non-literal-evidence] {r['relationship_id']}: evidence contains an ellipsis (not verbatim): {evidence!r}")
            body = source_bodies.get(r["source_file"])
            if body is not None and r["type"] not in STRUCTURAL_RELATIONSHIP_TYPES:
                haystack = strip_markdown_emphasis(normalize_ws(body))
                needle = strip_markdown_emphasis(normalize_ws(evidence))
                if needle and needle not in haystack:
                    report.crit(f"[non-literal-evidence] {r['relationship_id']}: evidence not found verbatim in {r['source_file']}: {evidence!r}")

        elif r["evidence_type"] == "inferred":
            if r["confidence"] > 0.6:
                report.crit(f"[inferred-confidence] {r['relationship_id']}: inferred but confidence={r['confidence']} > 0.6")
            from_name = _node_name(r["from_id"], entity_by_id, doc_by_id)
            to_name = _node_name(r["to_id"], entity_by_id, doc_by_id)
            ev_lower = evidence.lower()
            from_hit = from_name is not None and _name_tokens_in(from_name, ev_lower)
            to_hit = to_name is not None and _name_tokens_in(to_name, ev_lower)
            if not (from_hit and to_hit):
                report.crit(
                    f"[inferred-one-sided] {r['relationship_id']}: evidence does not substantiate both endpoints "
                    f"(from={from_name!r} hit={from_hit}, to={to_name!r} hit={to_hit}): {evidence!r}"
                )


def _node_name(node_id, entity_by_id, doc_by_id):
    if node_id in entity_by_id:
        return entity_by_id[node_id].get("name")
    if node_id in doc_by_id:
        return doc_by_id[node_id].get("title")
    return None


def _name_tokens_in(name: str, evidence_lower: str) -> bool:
    # Loose heuristic: at least one "significant" word (len >= 4) from the
    # node's name appears in the evidence text.
    words = [w for w in re.findall(r"[a-zA-Z0-9]+", name.lower()) if len(w) >= 4]
    return any(w in evidence_lower for w in words)


# ---------------------------------------------------------------------------
# 9. TechnologyDomain closed vocabulary
# ---------------------------------------------------------------------------
def check_technology_domain_catalog(report: Report, entities, allowed_tags):
    for e in entities:
        if e["type"] == "TechnologyDomain" and e["name"] not in allowed_tags:
            report.crit(f"[technology-domain-out-of-catalog] {e['node_id']}: name={e['name']!r} not in {sorted(allowed_tags)}")


# ---------------------------------------------------------------------------
# 10. Requirement/Constraint atomicity heuristic
# ---------------------------------------------------------------------------
def check_atomic_requirements_constraints(report: Report, entities):
    for e in entities:
        if e["type"] not in ("Requirement", "Constraint"):
            continue
        name = e.get("name") or ""
        triggers = REQUIREMENT_TRIGGERS if e["type"] == "Requirement" else CONSTRAINT_TRIGGERS
        hits = sum(1 for t in triggers if t.search(name))
        if hits > 1:
            report.warn(f"[possibly-non-atomic] {e['node_id']} ({e['type']}): name matches {hits} trigger patterns: {name!r}")


# ---------------------------------------------------------------------------
# 11. Count parity across JSONL / JSON / GraphML / Cypher
# ---------------------------------------------------------------------------
def check_count_parity(report: Report, documents, entities, sources, relationships, input_dir: str):
    jsonl_node_count = len(documents) + len(entities) + len(sources)
    jsonl_edge_count = len(relationships)

    json_path = os.path.join(input_dir, "knowledge_graph.json")
    graphml_path = os.path.join(input_dir, "knowledge_graph.graphml")
    cypher_path = os.path.join(input_dir, "knowledge_graph.cypher")

    if os.path.isfile(json_path):
        with open(json_path, encoding="utf-8") as f:
            kg = json.load(f)
        if len(kg["nodes"]) != jsonl_node_count:
            report.crit(f"[count-parity] knowledge_graph.json nodes={len(kg['nodes'])} != JSONL node total={jsonl_node_count}")
        if len(kg["edges"]) != jsonl_edge_count:
            report.crit(f"[count-parity] knowledge_graph.json edges={len(kg['edges'])} != JSONL relationship total={jsonl_edge_count}")
    else:
        report.warn("[count-parity] knowledge_graph.json not found - run scripts/build_graph.py first")

    if os.path.isfile(graphml_path):
        with open(graphml_path, encoding="utf-8") as f:
            graphml_text = f.read()
        node_count = len(re.findall(r"<node\b", graphml_text))
        edge_count = len(re.findall(r"<edge\b", graphml_text))
        if node_count != jsonl_node_count:
            report.crit(f"[count-parity] knowledge_graph.graphml <node> count={node_count} != JSONL node total={jsonl_node_count}")
        if edge_count != jsonl_edge_count:
            report.crit(f"[count-parity] knowledge_graph.graphml <edge> count={edge_count} != JSONL relationship total={jsonl_edge_count}")
    else:
        report.warn("[count-parity] knowledge_graph.graphml not found - run scripts/build_graph.py first")

    if os.path.isfile(cypher_path):
        with open(cypher_path, encoding="utf-8") as f:
            cypher_text = f.read()
        node_merges = len(re.findall(r"^MERGE \(n:", cypher_text, re.MULTILINE))
        edge_merges = len(re.findall(r"^MATCH \(a ", cypher_text, re.MULTILINE))
        if node_merges != jsonl_node_count:
            report.crit(f"[count-parity] knowledge_graph.cypher node MERGE count={node_merges} != JSONL node total={jsonl_node_count}")
        if edge_merges != jsonl_edge_count:
            report.crit(f"[count-parity] knowledge_graph.cypher edge MERGE count={edge_merges} != JSONL relationship total={jsonl_edge_count}")
        if re.search(r"MERGE \(n:`[^`]+` \{id: [^,}]+, ", cypher_text):
            report.crit("[cypher-merge-not-by-id] a node MERGE pattern contains more than just `id` - not idempotent")
        if re.search(r"MERGE \(a\)-\[r:`[^`]+` \{relationship_id: [^,}]+, ", cypher_text):
            report.crit("[cypher-merge-not-by-id] a relationship MERGE pattern contains more than just `relationship_id` - not idempotent")
    else:
        report.warn("[count-parity] knowledge_graph.cypher not found - run scripts/build_graph.py first")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", default=DEFAULT_DIR,
                         help=f"directory to read *.jsonl and knowledge_graph.* from (default: {DEFAULT_DIR})")
    args = parser.parse_args()

    try:
        input_dir = resolve_input_dir(args.input_dir)
    except PathSecurityError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    report = Report()

    documents = read_jsonl(os.path.join(input_dir, "documents.jsonl"))
    entities = read_jsonl(os.path.join(input_dir, "entities.jsonl"))
    sources = read_jsonl(os.path.join(input_dir, "sources.jsonl"))
    relationships = read_jsonl(os.path.join(input_dir, "relationships.jsonl"))

    ontology = load_yaml(os.path.join(RESEARCH_ROOT, "config", "ontology.yaml"))
    allowed_tags = set(ontology["technology_domain_allowed_tags"])

    entity_by_id = {e["node_id"]: e for e in entities}
    doc_by_id = {d["node_id"]: d for d in documents}
    source_files = collect_source_files(documents, entities, sources, relationships)
    source_bodies, rejected_source_files = load_source_bodies(source_files)
    for rel_path in rejected_source_files:
        report.warn(
            f"[unsafe-source-file] source_file rejected by path validation "
            f"(outside docs/ref-arch/ or outside the repository); "
            f"verbatim-evidence check skipped: {rel_path!r}"
        )

    check_schemas(report, documents, entities, sources, relationships)
    known_ids = check_duplicate_ids(report, documents, entities, sources, relationships)
    check_dangling_refs(report, relationships, known_ids)
    check_data_types(report, documents, entities, sources, relationships)
    check_entities_have_source(report, entities)
    check_relationship_evidence(report, relationships, entity_by_id, doc_by_id, source_bodies)
    check_technology_domain_catalog(report, entities, allowed_tags)
    check_atomic_requirements_constraints(report, entities)
    check_count_parity(report, documents, entities, sources, relationships, input_dir)

    print(f"documents={len(documents)} entities={len(entities)} sources={len(sources)} relationships={len(relationships)}")
    print()
    if report.critical:
        print(f"CRITICAL ({len(report.critical)}):")
        for msg in report.critical:
            print(f"  - {msg}")
    else:
        print("CRITICAL: none")
    print()
    if report.warnings:
        print(f"WARNINGS ({len(report.warnings)}):")
        for msg in report.warnings:
            print(f"  - {msg}")
    else:
        print("WARNINGS: none")
    print()
    print("RESULT:", "PASS" if report.ok() else "FAIL")
    return 0 if report.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
