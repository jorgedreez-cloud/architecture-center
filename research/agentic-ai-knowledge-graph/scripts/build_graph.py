#!/usr/bin/env python3
"""
Merge data/pilot/{documents,entities,sources,relationships}.jsonl into a
single consolidated graph and export it three ways:

  data/pilot/knowledge_graph.json     - {"nodes": [...], "edges": [...]}
  data/pilot/knowledge_graph.graphml  - GraphML (Gephi, yEd, ...)
  data/pilot/knowledge_graph.cypher   - Cypher CREATE statements for Neo4j

external_links.jsonl is NOT merged into the graph - it is a raw inventory
artifact (one row per link occurrence, unresolved), kept separate from
sources.jsonl (deduplicated Source nodes) on purpose.

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


def node_scalar_props(row: dict, id_key: str) -> dict:
    """Flatten a node row into GraphML/Cypher-safe scalar properties."""
    props = {}
    for k, v in row.items():
        if k == id_key:
            continue
        if isinstance(v, (list, dict)):
            props[k] = json.dumps(v, ensure_ascii=False)
        elif v is None:
            continue
        else:
            props[k] = v
    return props


def build_nodes() -> list[dict]:
    nodes = []
    for row in read_jsonl("documents.jsonl"):
        nodes.append({"id": row["node_id"], "type": row["type"], "props": node_scalar_props(row, "node_id")})
    for row in read_jsonl("entities.jsonl"):
        nodes.append({"id": row["node_id"], "type": row["type"], "props": node_scalar_props(row, "node_id")})
    for row in read_jsonl("sources.jsonl"):
        nodes.append({"id": row["source_id"], "type": "Source", "props": node_scalar_props(row, "source_id")})
    return nodes


def build_edges() -> list[dict]:
    edges = []
    for row in read_jsonl("relationships.jsonl"):
        edges.append({
            "id": row["relationship_id"],
            "type": row["type"],
            "source": row["from_id"],
            "target": row["to_id"],
            "props": node_scalar_props(row, "relationship_id"),
        })
    return edges


def write_json(nodes, edges):
    out_path = os.path.join(PILOT_DIR, "knowledge_graph.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes, "edges": edges}, f, indent=2, ensure_ascii=False, default=str)
    print(f"Wrote {out_path} ({len(nodes)} nodes, {len(edges)} edges)")


def write_graphml(nodes, edges):
    ns = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}graphml")

    # declare attribute keys generically as strings, discovered from the data
    node_keys, edge_keys = set(), set()
    for n in nodes:
        node_keys.update(n["props"].keys())
        node_keys.add("type")
    for e in edges:
        edge_keys.update(e["props"].keys())
        edge_keys.add("type")

    key_ids = {}
    for k in sorted(node_keys):
        kid = f"n_{k}"
        key_ids[("node", k)] = kid
        ET.SubElement(root, f"{{{ns}}}key", {"id": kid, "for": "node", "attr.name": k, "attr.type": "string"})
    for k in sorted(edge_keys):
        kid = f"e_{k}"
        key_ids[("edge", k)] = kid
        ET.SubElement(root, f"{{{ns}}}key", {"id": kid, "for": "edge", "attr.name": k, "attr.type": "string"})

    graph = ET.SubElement(root, f"{{{ns}}}graph", {"id": "agentic-ai-kg-pilot", "edgedefault": "directed"})

    for n in nodes:
        node_el = ET.SubElement(graph, f"{{{ns}}}node", {"id": n["id"]})
        data_el = ET.SubElement(node_el, f"{{{ns}}}data", {"key": key_ids[("node", "type")]})
        data_el.text = n["type"]
        for k, v in n["props"].items():
            data_el = ET.SubElement(node_el, f"{{{ns}}}data", {"key": key_ids[("node", k)]})
            data_el.text = str(v)

    for e in edges:
        edge_el = ET.SubElement(graph, f"{{{ns}}}edge", {"id": e["id"], "source": e["source"], "target": e["target"]})
        data_el = ET.SubElement(edge_el, f"{{{ns}}}data", {"key": key_ids[("edge", "type")]})
        data_el.text = e["type"]
        for k, v in e["props"].items():
            data_el = ET.SubElement(edge_el, f"{{{ns}}}data", {"key": key_ids[("edge", k)]})
            data_el.text = str(v)

    raw = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ")
    out_path = os.path.join(PILOT_DIR, "knowledge_graph.graphml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(pretty)
    print(f"Wrote {out_path}")


def cypher_escape(v) -> str:
    return str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")


def write_cypher(nodes, edges):
    lines = [
        "// Auto-generated by scripts/build_graph.py - pilot data only.",
        "// Import order: nodes first (MERGE, deduplicated by id), then edges.",
        "",
    ]
    for n in nodes:
        prop_str = ", ".join(
            f"{k}: '{cypher_escape(v)}'" for k, v in {"id": n["id"], **n["props"]}.items()
        )
        lines.append(f"MERGE (:`{n['type']}` {{{prop_str}}});")

    lines.append("")
    for e in edges:
        prop_str = ", ".join(f"{k}: '{cypher_escape(v)}'" for k, v in e["props"].items())
        lines.append(
            f"MATCH (a {{id: '{cypher_escape(e['source'])}'}}), (b {{id: '{cypher_escape(e['target'])}'}}) "
            f"MERGE (a)-[:`{e['type']}` {{{prop_str}}}]->(b);"
        )

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
