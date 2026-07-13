#!/usr/bin/env python3
"""
Merge data/pilot/{documents,entities,sources,relationships}.jsonl into a
single consolidated graph and export it three ways:

  data/pilot/knowledge_graph.json     - {"nodes": [...], "edges": [...]}
  data/pilot/knowledge_graph.graphml  - GraphML (Gephi, yEd, ...)
  data/pilot/knowledge_graph.cypher   - Cypher MERGE statements for Neo4j

external_links.jsonl is NOT merged into the graph - it is a raw inventory
artifact (one row per link occurrence, unresolved), kept separate from
sources.jsonl (deduplicated Source nodes) on purpose.

Idempotency (see notes/findings.md FASE 1 correction): the Cypher export
MERGEs nodes on their `id` property ALONE, and relationships on their
`relationship_id` property ALONE - never on the full property map, which
would include `extraction_date` (a fresh timestamp every run) and cause
Neo4j to treat every re-run as a brand new node/relationship, silently
duplicating the whole graph. All other properties are applied with
`SET n += {...}` / `SET r += {...}` after the MERGE, so re-running this
script's Cypher output against the same graph updates properties in place
instead of duplicating anything.

Usage:
    python3 scripts/build_graph.py
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom

RESEARCH_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PILOT_DIR = os.path.join(RESEARCH_ROOT, "data", "pilot")


def read_jsonl(name: str) -> list[dict]:
    path = os.path.join(PILOT_DIR, name)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def raw_props(row: dict, id_key: str) -> dict:
    """Every property except the id field and `type` (both writers emit
    `type` once, explicitly, before looping over these props - leaving it
    in here would silently duplicate it: a pre-existing bug in the
    pre-audit version, where GraphML emitted two <data key="n_type">
    elements per node/edge), with types preserved as-is (no flattening
    here) - GraphML/Cypher writers each decide how to render
    lists/dicts/bools/numbers/None in their own format."""
    return {k: v for k, v in row.items() if k not in (id_key, "type")}


def build_nodes() -> list[dict]:
    nodes = []
    for row in read_jsonl("documents.jsonl"):
        nodes.append({"id": row["node_id"], "type": row["type"], "props": raw_props(row, "node_id")})
    for row in read_jsonl("entities.jsonl"):
        nodes.append({"id": row["node_id"], "type": row["type"], "props": raw_props(row, "node_id")})
    for row in read_jsonl("sources.jsonl"):
        nodes.append({"id": row["source_id"], "type": "Source", "props": raw_props(row, "source_id")})
    return nodes


def build_edges() -> list[dict]:
    edges = []
    for row in read_jsonl("relationships.jsonl"):
        edges.append({
            "id": row["relationship_id"],
            "type": row["type"],
            "source": row["from_id"],
            "target": row["to_id"],
            "props": raw_props(row, "relationship_id"),
        })
    return edges


def write_json(nodes, edges):
    out_path = os.path.join(PILOT_DIR, "knowledge_graph.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes, "edges": edges}, f, indent=2, ensure_ascii=False, default=str)
    print(f"Wrote {out_path} ({len(nodes)} nodes, {len(edges)} edges)")


# ---------------------------------------------------------------------------
# GraphML export: attr.type reflects the ACTUAL Python type of the property
# (bool -> boolean, int -> int, float -> double, list/dict -> string via a
# JSON-encoded value, everything else -> string). A None value is rendered
# by OMITTING the <data> element for that key on that node/edge entirely -
# GraphML has no native null literal, so "attribute absent" is the only
# faithful representation; this is the explicit, documented convention
# (rather than silently emitting the text "None" as a string, which is
# what the pre-audit version did via str(v)).
# ---------------------------------------------------------------------------
def graphml_type_of(v) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "double"
    return "string"


def graphml_text_of(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def compute_key_types(items: list[dict]) -> dict[str, str]:
    """First non-None value observed for each property key decides its
    declared GraphML attr.type (types are consistent per key across rows
    in this dataset - e.g. `confidence` is always a float, `draft` is
    always a bool)."""
    types: dict[str, str] = {}
    for item in items:
        for k, v in item["props"].items():
            if k in types or v is None:
                continue
            types[k] = graphml_type_of(v)
    return types


def write_graphml(nodes, edges):
    ns = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}graphml")

    node_prop_types = compute_key_types(nodes)
    edge_prop_types = compute_key_types(edges)
    node_keys = sorted(set(node_prop_types) | {"type"})
    edge_keys = sorted(set(edge_prop_types) | {"type"})

    key_ids = {}
    for k in node_keys:
        kid = f"n_{k}"
        key_ids[("node", k)] = kid
        attr_type = "string" if k == "type" else node_prop_types[k]
        ET.SubElement(root, f"{{{ns}}}key", {"id": kid, "for": "node", "attr.name": k, "attr.type": attr_type})
    for k in edge_keys:
        kid = f"e_{k}"
        key_ids[("edge", k)] = kid
        attr_type = "string" if k == "type" else edge_prop_types[k]
        ET.SubElement(root, f"{{{ns}}}key", {"id": kid, "for": "edge", "attr.name": k, "attr.type": attr_type})

    graph = ET.SubElement(root, f"{{{ns}}}graph", {"id": "agentic-ai-kg-pilot", "edgedefault": "directed"})

    for n in nodes:
        node_el = ET.SubElement(graph, f"{{{ns}}}node", {"id": n["id"]})
        data_el = ET.SubElement(node_el, f"{{{ns}}}data", {"key": key_ids[("node", "type")]})
        data_el.text = n["type"]
        for k, v in n["props"].items():
            if v is None:
                continue  # see module docstring: null == attribute absent
            data_el = ET.SubElement(node_el, f"{{{ns}}}data", {"key": key_ids[("node", k)]})
            data_el.text = graphml_text_of(v)

    for e in edges:
        edge_el = ET.SubElement(graph, f"{{{ns}}}edge", {"id": e["id"], "source": e["source"], "target": e["target"]})
        data_el = ET.SubElement(edge_el, f"{{{ns}}}data", {"key": key_ids[("edge", "type")]})
        data_el.text = e["type"]
        for k, v in e["props"].items():
            if v is None:
                continue
            data_el = ET.SubElement(edge_el, f"{{{ns}}}data", {"key": key_ids[("edge", k)]})
            data_el.text = graphml_text_of(v)

    raw = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ")
    out_path = os.path.join(PILOT_DIR, "knowledge_graph.graphml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(pretty)
    print(f"Wrote {out_path}")


# ---------------------------------------------------------------------------
# Cypher export: numbers unquoted, booleans as bare true/false, lists as
# Cypher list literals, null as the bare `null` keyword, strings single-
# quoted with backslashes/quotes/newlines escaped.
# ---------------------------------------------------------------------------
def cypher_escape_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")


def cypher_literal(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(cypher_literal(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {cypher_literal(val)}" for k, val in v.items()) + "}"
    return "'" + cypher_escape_string(str(v)) + "'"


def write_cypher(nodes, edges):
    lines = [
        "// Auto-generated by scripts/build_graph.py - pilot data only.",
        "// Idempotent: nodes MERGE on `id` alone; relationships MERGE on",
        "// `relationship_id` alone. Re-running this file against the same",
        "// graph updates properties via SET, it does not duplicate anything.",
        "",
    ]
    for n in nodes:
        set_str = ", ".join(f"{k}: {cypher_literal(v)}" for k, v in n["props"].items())
        merge_clause = f"MERGE (n:`{n['type']}` {{id: {cypher_literal(n['id'])}}})"
        lines.append(f"{merge_clause} SET n += {{{set_str}}};" if set_str else f"{merge_clause};")

    lines.append("")
    for e in edges:
        set_str = ", ".join(f"{k}: {cypher_literal(v)}" for k, v in e["props"].items())
        match_clause = (
            f"MATCH (a {{id: {cypher_literal(e['source'])}}}), (b {{id: {cypher_literal(e['target'])}}}) "
            f"MERGE (a)-[r:`{e['type']}` {{relationship_id: {cypher_literal(e['id'])}}}]->(b)"
        )
        lines.append(f"{match_clause} SET r += {{{set_str}}};" if set_str else f"{match_clause};")

    out_path = os.path.join(PILOT_DIR, "knowledge_graph.cypher")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {out_path}")


def main():
    nodes = build_nodes()
    edges = build_edges()

    # sanity check: every edge endpoint must resolve to a known node id
    node_ids = {n["id"] for n in nodes}
    dangling = [e for e in edges if e["source"] not in node_ids or e["target"] not in node_ids]
    if dangling:
        print(f"WARNING: {len(dangling)} relationship(s) reference an unknown node id:")
        for e in dangling:
            print(f"  {e['id']}: {e['source']} -> {e['target']}")

    write_json(nodes, edges)
    write_graphml(nodes, edges)
    write_cypher(nodes, edges)


if __name__ == "__main__":
    main()
