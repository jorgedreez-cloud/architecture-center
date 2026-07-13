#!/usr/bin/env python3
"""
Deep, evidence-anchored extraction for a small, explicit list of documents
(the FASE 6 pilot: /ref-arch/ff07b1, RA0029 root, RA0029/1-a2a-and-mcp).

For each document this script produces, in memory, rows for:
  - documents.jsonl   (Document + ReferenceArchitecture nodes)
  - entities.jsonl    (SAPProduct/SAPService/Agent/Protocol/API/DataSource/
                        IdentitySecurity/Capability/TechnologyDomain/Diagram/
                        UseCase/Requirement/Constraint nodes)
  - sources.jsonl     (Source nodes, deduplicated by URL)
  - external_links.jsonl (raw, one row per link occurrence - no dedup)
  - relationships.jsonl  (edges between the above)

Every row carries the mandatory provenance fields defined in
schemas/*.schema.json (source_id, source_url, source_file, source_section,
evidence, evidence_type, confidence, repository_commit, extraction_date).
No relationship is written without a quoted `evidence` string.

Two extraction methods are used, and are labelled as such in every record:

1. Structural/generic (reusable, would scale to all 118 RA documents):
   front matter -> Document/ReferenceArchitecture/TechnologyDomain nodes,
   markdown drawio embeds -> Diagram nodes, markdown links -> Source nodes
   and external_links rows, canonical lexicon (config/ontology.yaml) scan
   of the body -> Entity nodes + MENTIONS edges.

2. Curated pilot facts (NOT automated, manually verified against the exact
   document text, kept in CURATED_FACTS below): a handful of higher-value
   relationships (AUTHENTICATES_WITH, SUPPORTS_PROTOCOL, EXPOSES, REQUIRES,
   HAS_USE_CASE, plus one deliberately-inferred CONNECTS_TO) that a generic
   lexicon scan cannot safely produce on its own. This is explicitly a
   manual/pilot-only step - see notes/findings.md.

Usage:
    python3 scripts/extract_document.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RESEARCH_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SITE_URL = "https://architecture.learning.sap.com"
FRONT_MATTER_RE = re.compile(r"^---\n(.*?\n)---\n", re.DOTALL)

PILOT_DOCS = [
    "docs/ref-arch/RA0024/3-extend-joule-with-joule-studio/readme.md",  # /ref-arch/ff07b1
    "docs/ref-arch/RA0029/readme.md",                                    # RA0029 root
    "docs/ref-arch/RA0029/1-a2a-and-mcp/readme.md",                      # RA0029 subpage
]


def repo_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_front_matter(content: str) -> tuple[dict, str]:
    m = FRONT_MATTER_RE.match(content)
    if not m:
        return {}, content
    fm = yaml.safe_load(m.group(1)) or {}
    body = content[m.end():]
    return fm, body


def public_url(slug: str | None) -> str | None:
    return f"{SITE_URL}/docs{slug}" if slug else None


DOMAIN_RULES = [
    ("SAP Architecture Center", ["architecture.learning.sap.com"]),
    ("SAP Help", ["help.sap.com"]),
    ("SAP Discovery Center", ["discovery-center.cloud.sap"]),
    ("SAP Community", ["community.sap.com"]),
    ("SAP Business Accelerator Hub", ["api.sap.com"]),
    ("SAP Developer", ["developer.sap.com", "developers.sap.com"]),
    ("SAP Learning", ["learning.sap.com"]),
    ("GitHub", ["github.com"]),
    ("YouTube", ["youtube.com", "youtu.be"]),
]


def classify_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for category, matches in DOMAIN_RULES:
        if any(host == d or host.endswith("." + d) for d in matches):
            return category
    return "Other"


def find_section(body: str, char_offset: int) -> str | None:
    """Return the nearest preceding markdown heading ('## X') for a body offset."""
    headings = list(re.finditer(r"^#{2,4}\s+(.*)$", body[:char_offset], re.MULTILINE))
    return headings[-1].group(1).strip() if headings else None


def sentence_containing(body: str, needle_idx: int, needle_len: int) -> str:
    """Extract the sentence (or list item) surrounding a match, as evidence."""
    start = max(body.rfind(".", 0, needle_idx), body.rfind("\n", 0, needle_idx))
    end_candidates = [e for e in (body.find(".", needle_idx + needle_len), body.find("\n", needle_idx + needle_len)) if e != -1]
    end = min(end_candidates) if end_candidates else len(body)
    snippet = body[start + 1:end + 1].strip()
    snippet = re.sub(r"\s+", " ", snippet)
    return snippet[:500]


class Extractor:
    def __init__(self):
        self.commit = repo_commit()
        self.extraction_date = datetime.now(timezone.utc).isoformat()
        self.ontology = load_yaml(os.path.join(RESEARCH_ROOT, "config", "ontology.yaml"))
        self.canonical = self.ontology["canonical_entities"]

        self.documents: list[dict] = []
        self.entities: list[dict] = []
        self.sources: list[dict] = []
        self.external_links: list[dict] = []
        self.relationships: list[dict] = []

        self._ra_nodes_seen: set[str] = set()
        self._entity_nodes_seen: set[str] = set()
        self._source_nodes_seen: dict[str, str] = {}  # url -> node_id
        self._domain_nodes_seen: set[str] = set()

    # ---------- shared provenance helper ----------
    def _prov(self, source_file, source_section, source_url, evidence, evidence_type, confidence):
        return {
            "source_id": f"doc:{source_file}",
            "source_url": source_url,
            "source_file": source_file,
            "source_section": source_section,
            "evidence": evidence,
            "evidence_type": evidence_type,
            "confidence": confidence,
            "repository_commit": self.commit,
            "extraction_date": self.extraction_date,
        }

    def add_relationship(self, rel_type, from_id, to_id, from_type, to_type,
                          source_file, source_section, source_url, evidence,
                          evidence_type, confidence):
        rid = f"rel:{from_id}:{rel_type}:{to_id}:{short_hash(source_file)}"
        row = {
            "relationship_id": rid,
            "type": rel_type,
            "from_id": from_id,
            "to_id": to_id,
            "from_type": from_type,
            "to_type": to_type,
            **self._prov(source_file, source_section, source_url, evidence, evidence_type, confidence),
        }
        self.relationships.append(row)

    # ---------- structural / generic extraction ----------
    def process_document(self, rel_path: str):
        abs_path = os.path.join(REPO_ROOT, rel_path)
        with open(abs_path, encoding="utf-8") as f:
            raw = f.read()
        fm, body = parse_front_matter(raw)

        parts = rel_path.split("/")
        ra_folder = parts[2]
        doc_node_id = f"doc:{fm.get('id')}"
        ra_node_id = f"ra:{ra_folder}"
        doc_url = public_url(fm.get("slug"))

        # --- Document node ---
        self.documents.append({
            "node_id": doc_node_id,
            "type": "Document",
            "ra_folder": ra_folder,
            "front_matter_id": fm.get("id"),
            "slug": fm.get("slug"),
            "title": fm.get("title"),
            "description": fm.get("description"),
            "tags": fm.get("tags") or [],
            "keywords": fm.get("keywords") or [],
            "contributors": fm.get("contributors") or [],
            "last_update_date": str((fm.get("last_update") or {}).get("date")),
            "last_update_author": (fm.get("last_update") or {}).get("author"),
            "draft": fm.get("draft", False),
            "unlisted": fm.get("unlisted", False),
            "sidebar_position": fm.get("sidebar_position"),
            **self._prov(rel_path, None, doc_url,
                         f"front matter of {rel_path}", "explicit", 1.0),
        })

        # --- ReferenceArchitecture node (deduplicated) ---
        if ra_node_id not in self._ra_nodes_seen:
            self._ra_nodes_seen.add(ra_node_id)
            self.documents.append({
                "node_id": ra_node_id,
                "type": "ReferenceArchitecture",
                "ra_folder": ra_folder,
                "front_matter_id": None,
                "slug": None,
                "title": f"Reference Architecture folder {ra_folder}",
                **self._prov(rel_path, None, None,
                             f"folder membership: {rel_path} lives under docs/ref-arch/{ra_folder}/",
                             "explicit", 1.0),
            })

        # PART_OF
        self.add_relationship(
            "PART_OF", doc_node_id, ra_node_id, "Document", "ReferenceArchitecture",
            rel_path, None, doc_url,
            f"{rel_path} is stored under docs/ref-arch/{ra_folder}/", "explicit", 1.0,
        )

        # --- TechnologyDomain nodes from tags ---
        for tag in fm.get("tags") or []:
            dom_id = f"entity:TechnologyDomain:{slugify(tag)}"
            if dom_id not in self._domain_nodes_seen:
                self._domain_nodes_seen.add(dom_id)
                self.entities.append({
                    "node_id": dom_id, "type": "TechnologyDomain", "name": tag,
                    "canonical_id": None, "extraction_method": "front_matter_tag",
                    "found_in_document": doc_node_id,
                    **self._prov(rel_path, None, doc_url,
                                 f"tags: [{', '.join(fm.get('tags') or [])}]", "explicit", 1.0),
                })
            self.add_relationship(
                "BELONGS_TO_DOMAIN", doc_node_id, dom_id, "Document", "TechnologyDomain",
                rel_path, None, doc_url, f"front matter tag '{tag}'", "explicit", 1.0,
            )

        # --- Diagram nodes from drawio embeds ---
        for m in re.finditer(r"!\[.*?\]\(\.?/?(drawio/[\w\-./]+\.drawio)\)", body):
            drawio_rel = m.group(1)
            drawio_path = os.path.normpath(os.path.join(os.path.dirname(rel_path), drawio_rel)).replace(os.sep, "/")
            section = find_section(body, m.start())
            diagram_id = f"diagram:{doc_node_id}:{os.path.basename(drawio_rel)}"
            self.entities.append({
                "node_id": diagram_id, "type": "Diagram", "name": os.path.basename(drawio_rel),
                "canonical_id": None, "extraction_method": "drawio_reference",
                "found_in_document": doc_node_id,
                **self._prov(rel_path, section, doc_url,
                             m.group(0), "explicit", 1.0),
            })
            self.add_relationship(
                "HAS_DIAGRAM", doc_node_id, diagram_id, "Document", "Diagram",
                rel_path, section, doc_url, m.group(0), "explicit", 1.0,
            )
            if not os.path.isfile(os.path.join(REPO_ROOT, drawio_path)):
                print(f"WARNING: referenced drawio file not found on disk: {drawio_path}", file=sys.stderr)

        # --- markdown links: Source nodes + external_links.jsonl ---
        for m in re.finditer(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", body):
            link_text, url = m.group(1), m.group(2)
            section = find_section(body, m.start())
            domain_category = classify_domain(url)

            self.external_links.append({
                "url": url,
                "link_title": link_text,
                "domain_category": domain_category,
                "found_in_document": doc_node_id,
                **self._prov(rel_path, section, doc_url, m.group(0), "explicit", 1.0),
            })

            if url not in self._source_nodes_seen:
                source_node_id = f"source:{short_hash(url)}"
                self._source_nodes_seen[url] = source_node_id
                self.sources.append({
                    "source_id": source_node_id,
                    "kind": "external_link",
                    "source_url": url,
                    "source_file": rel_path,
                    "source_section": section,
                    "domain_category": domain_category,
                    "link_title": link_text,
                    "evidence": m.group(0),
                    "evidence_type": "explicit",
                    "confidence": 1.0,
                    "repository_commit": self.commit,
                    "extraction_date": self.extraction_date,
                })
            source_node_id = self._source_nodes_seen[url]
            self.add_relationship(
                "HAS_SOURCE", doc_node_id, source_node_id, "Document", "Source",
                rel_path, section, doc_url, m.group(0), "explicit", 1.0,
            )

        # --- canonical entity lexicon scan -> Entity + MENTIONS ---
        body_lower = body.lower()
        for entry in self.canonical:
            for alias in entry["aliases"]:
                idx = body_lower.find(alias)
                if idx == -1:
                    continue
                node_id = f"entity:{entry['canonical_id']}"
                section = find_section(body, idx)
                evidence = sentence_containing(body, idx, len(alias))
                if node_id not in self._entity_nodes_seen:
                    self._entity_nodes_seen.add(node_id)
                    self.entities.append({
                        "node_id": node_id, "type": entry["type"], "name": entry["canonical_name"],
                        "canonical_id": entry["canonical_id"],
                        "extraction_method": "canonical_lexicon_match",
                        "found_in_document": doc_node_id,
                        **self._prov(rel_path, section, doc_url, evidence, "explicit", 0.9),
                    })
                self.add_relationship(
                    "MENTIONS", doc_node_id, node_id, "Document", entry["type"],
                    rel_path, section, doc_url, evidence, "explicit", 0.85,
                )
                break  # one alias hit per document is enough evidence

        return doc_node_id, ra_node_id, fm, body, rel_path, doc_url

    # ---------- curated pilot facts (manual, see notes/findings.md) ----------
    def apply_curated_facts(self, doc_node_id, ra_node_id, rel_path, doc_url, body):
        facts = CURATED_FACTS.get(rel_path, [])
        for fact in facts:
            section = fact.get("section")
            from_id = fact["from_id"].format(doc=doc_node_id, ra=ra_node_id)
            to_id = fact["to_id"].format(doc=doc_node_id, ra=ra_node_id)

            # entities referenced by curated facts that are not canonical
            # (e.g. UseCase / Requirement / Constraint) are created here
            if fact.get("create_entity"):
                ce = fact["create_entity"]
                node_id = ce["node_id"].format(doc=doc_node_id, ra=ra_node_id)
                if node_id not in self._entity_nodes_seen:
                    self._entity_nodes_seen.add(node_id)
                    self.entities.append({
                        "node_id": node_id, "type": ce["type"], "name": ce["name"],
                        "canonical_id": None, "extraction_method": ce["extraction_method"],
                        "found_in_document": doc_node_id,
                        **self._prov(rel_path, section, doc_url, fact["evidence"], fact["evidence_type"], fact["confidence"]),
                    })

            self.add_relationship(
                fact["type"], from_id, to_id, fact["from_type"], fact["to_type"],
                rel_path, section, doc_url, fact["evidence"], fact["evidence_type"], fact["confidence"],
            )

    def run(self):
        for rel_path in PILOT_DOCS:
            doc_node_id, ra_node_id, fm, body, rel_path, doc_url = self.process_document(rel_path)
            self.apply_curated_facts(doc_node_id, ra_node_id, rel_path, doc_url, body)

    def write(self):
        out_dir = os.path.join(RESEARCH_ROOT, "data", "pilot")
        os.makedirs(out_dir, exist_ok=True)

        def dump(name, rows):
            path = os.path.join(out_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            print(f"Wrote {path} ({len(rows)} rows)")

        dump("documents.jsonl", self.documents)
        dump("entities.jsonl", self.entities)
        dump("sources.jsonl", self.sources)
        dump("external_links.jsonl", self.external_links)
        dump("relationships.jsonl", self.relationships)


# ---------------------------------------------------------------------------
# Curated pilot facts: manually verified against the exact document text.
# Each entry needs a from_id/to_id template ('{doc}' / '{ra}' placeholders),
# and, if the target entity isn't in the canonical registry, a create_entity
# block. evidence is copied verbatim from the source document.
# ---------------------------------------------------------------------------
CURATED_FACTS = {
    "docs/ref-arch/RA0024/3-extend-joule-with-joule-studio/readme.md": [
        {
            "type": "AUTHENTICATES_WITH",
            "from_type": "SAPService", "to_type": "IdentitySecurity",
            "from_id": "entity:joule-studio", "to_id": "entity:sap-cloud-identity-services",
            "section": "Flow",
            "evidence": "Joule Studio relies on SAP Cloud Identity Services for identity management, authentication and identity life-cycle management.",
            "evidence_type": "explicit", "confidence": 0.95,
        },
        {
            "type": "REQUIRES",
            "from_type": "SAPService", "to_type": "Requirement",
            "from_id": "entity:joule-studio", "to_id": "entity:req-joule-studio-provisioning",
            "section": "Flow",
            "evidence": "To provision Joule Studio, Joule must be set up in the target landscape along with SAP Build Process Automation as part of the SAP Build tenant with the build-default plan utilizing SAP Identity Authentication Service (IAS).",
            "evidence_type": "explicit", "confidence": 0.9,
            "create_entity": {
                "node_id": "entity:req-joule-studio-provisioning",
                "type": "Requirement",
                "name": "Joule + SAP Build Process Automation must be provisioned before Joule Studio",
                "extraction_method": "explicit_sentence",
            },
        },
        {
            "type": "HAS_USE_CASE", "from_type": "ReferenceArchitecture", "to_type": "UseCase",
            "from_id": "{ra}", "to_id": "entity:usecase-customer-support-agent",
            "section": "Examples in an SAP context",
            "evidence": "Automated Customer Support Agent Deployment: Joule Studio can be used to create AI agents that automatically handle customer inquiries, retrieve order statuses, and escalate issues when necessary.",
            "evidence_type": "explicit", "confidence": 0.9,
            "create_entity": {
                "node_id": "entity:usecase-customer-support-agent", "type": "UseCase",
                "name": "Automated Customer Support Agent Deployment",
                "extraction_method": "use_case_bullet",
            },
        },
        {
            "type": "HAS_USE_CASE", "from_type": "ReferenceArchitecture", "to_type": "UseCase",
            "from_id": "{ra}", "to_id": "entity:usecase-procurement-negotiation",
            "section": "Examples in an SAP context",
            "evidence": "Procurement Negotiation and Compliance: AI agents built in Joule Studio analyze supplier contracts, monitor compliance, and negotiate procurement deals, leveraging data from SAP ECC and external sources.",
            "evidence_type": "explicit", "confidence": 0.9,
            "create_entity": {
                "node_id": "entity:usecase-procurement-negotiation", "type": "UseCase",
                "name": "Procurement Negotiation and Compliance",
                "extraction_method": "use_case_bullet",
            },
        },
        {
            # deliberately-inferred edge: the source text lists integration
            # channels in one sentence without pairing each one explicitly
            # to Joule Studio via its own verb - kept low-confidence.
            "type": "CONNECTS_TO", "from_type": "SAPService", "to_type": "SAPProduct",
            "from_id": "entity:joule-studio", "to_id": "entity:sap-s4hana",
            "section": "Characteristics",
            "evidence": "Joule Studio integrates seamlessly with various SAP and third-party cloud solutions, including SAP ECC, SAP S/4HANA, and S/4HANA Cloud Private Edition, providing comprehensive support for hybrid landscapes.",
            "evidence_type": "inferred", "confidence": 0.5,
        },
    ],
    "docs/ref-arch/RA0029/readme.md": [
        {
            "type": "USES", "from_type": "Agent", "to_type": "SAPService",
            "from_id": "entity:joule", "to_id": "entity:generative-ai-hub",
            "section": "Architecture",
            "evidence": "Generative AI Hub: Foundation models, prompt optimization, orchestration capabilities (grounding, templating, data masking, I/O filtering) and vector search via SAP HANA Cloud.",
            "evidence_type": "inferred", "confidence": 0.55,
        },
        {
            "type": "CONSUMES_DATA_FROM", "from_type": "SAPService", "to_type": "SAPService",
            "from_id": "entity:generative-ai-hub", "to_id": "entity:sap-hana-cloud",
            "section": "Architecture",
            "evidence": "Generative AI Hub: ... vector search via SAP HANA Cloud.",
            "evidence_type": "explicit", "confidence": 0.85,
        },
        {
            "type": "AUTHENTICATES_WITH", "from_type": "Agent", "to_type": "IdentitySecurity",
            "from_id": "entity:joule", "to_id": "entity:sap-cloud-identity-services",
            "section": "Architecture",
            "evidence": "Security: SAP Cloud Identity Services manages authentication, authorization and identity federation across all connections.",
            "evidence_type": "explicit", "confidence": 0.85,
        },
    ],
    "docs/ref-arch/RA0029/1-a2a-and-mcp/readme.md": [
        {
            "type": "SUPPORTS_PROTOCOL", "from_type": "Agent", "to_type": "Protocol",
            "from_id": "entity:joule", "to_id": "entity:a2a-protocol",
            "section": None,
            "evidence": "Joule acts as an A2A client to communicate with external agents, while agents themselves use MCP to discover and consume tools from MCP servers.",
            "evidence_type": "explicit", "confidence": 0.95,
        },
        {
            "type": "SUPPORTS_PROTOCOL", "from_type": "Agent", "to_type": "Protocol",
            "from_id": "entity:joule", "to_id": "entity:mcp-protocol",
            "section": None,
            "evidence": "Joule acts as an A2A client to communicate with external agents, while agents themselves use MCP to discover and consume tools from MCP servers.",
            "evidence_type": "explicit", "confidence": 0.85,
        },
        {
            "type": "EXPOSES", "from_type": "API", "to_type": "Protocol",
            "from_id": "entity:agent-gateway", "to_id": "entity:a2a-protocol",
            "section": "Agent Gateway (Inbound)",
            "evidence": "The Agent Gateway exposes Joule Agents via the A2A protocol with an externally reachable endpoint.",
            "evidence_type": "explicit", "confidence": 0.95,
        },
        {
            "type": "AUTHENTICATES_WITH", "from_type": "API", "to_type": "IdentitySecurity",
            "from_id": "entity:agent-gateway", "to_id": "entity:sap-cloud-identity-services",
            "section": "Agent Gateway (Inbound)",
            "evidence": "Authentication: Secured through SAP Cloud Identity Services (IAS) App2App tokens with named user context.",
            "evidence_type": "explicit", "confidence": 0.95,
        },
        {
            "type": "CONSUMES_DATA_FROM", "from_type": "Protocol", "to_type": "DataSource",
            "from_id": "entity:mcp-protocol", "to_id": "entity:sap-knowledge-graph",
            "section": "Model Context Protocol (MCP)",
            "evidence": "At SAP, MCP is used to provide Joule Agents with semantically enriched access to SAP business capabilities and domain knowledge, including content from SAP Knowledge Graph.",
            "evidence_type": "explicit", "confidence": 0.9,
            "create_entity": {
                "node_id": "entity:sap-knowledge-graph", "type": "DataSource",
                "name": "SAP Knowledge Graph",
                "extraction_method": "explicit_sentence",
            },
        },
        {
            "type": "REQUIRES", "from_type": "API", "to_type": "IdentitySecurity",
            "from_id": "entity:agent-gateway", "to_id": "entity:sap-cloud-identity-services",
            "section": "Bring Your Own Agent (Outbound)",
            "evidence": "To ensure secure inbound communication and validate server updates, an Identity Authentication Service (IAS) App2App trust relationship must be established between Joule and the target agent server.",
            "evidence_type": "explicit", "confidence": 0.85,
        },
        {
            "type": "MENTIONS", "from_type": "Document", "to_type": "Constraint",
            "from_id": "{doc}", "to_id": "entity:constraint-agent-gateway-not-ga",
            "section": None,
            "evidence": "The Agent Gateway is not yet generally available (GA). As a result, the current architecture supports unidirectional (outbound) communication only.",
            "evidence_type": "explicit", "confidence": 0.95,
            "create_entity": {
                "node_id": "entity:constraint-agent-gateway-not-ga", "type": "Constraint",
                "name": "Agent Gateway not GA - unidirectional (outbound) only",
                "extraction_method": "explicit_sentence",
            },
        },
    ],
}


if __name__ == "__main__":
    ex = Extractor()
    ex.run()
    ex.write()
