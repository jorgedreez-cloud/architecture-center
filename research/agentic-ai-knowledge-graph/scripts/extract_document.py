#!/usr/bin/env python3
"""
Deep, evidence-anchored extraction for a document list read from a YAML
manifest (see config/pilots/*.yaml). Defaults to config/pilots/pilot1.yaml,
the original 3-document FASE 6 pilot (/ref-arch/ff07b1, RA0029 root,
RA0029/1-a2a-and-mcp).

For each document this script produces, in memory, rows for:
  - documents.jsonl   (Document + ReferenceArchitecture nodes)
  - entities.jsonl    (SAPProduct/SAPService/DeveloperTool/Agent/Protocol/API/
                        DataSource/IdentitySecurity/Capability/TechnologyDomain/
                        Diagram/UseCase/Requirement/Constraint nodes)
  - sources.jsonl     (Source nodes, deduplicated by URL)
  - external_links.jsonl (raw, one row per link occurrence - no dedup)
  - relationships.jsonl  (edges between the above)

Every row carries the mandatory provenance fields defined in
schemas/*.schema.json (source_id, source_url, source_file, source_section,
evidence, evidence_type, confidence, repository_commit, extraction_date).
No relationship is written without a quoted, verbatim `evidence` string -
never an ellipsis-elided paraphrase.

Extraction is split into two layers, and every record is labelled with
which one produced it:

1. Automatic / deterministic (scales to all 118 RA documents unchanged):
   front matter -> Document/ReferenceArchitecture/TechnologyDomain nodes
   (TechnologyDomain restricted to the closed vocabulary in
   config/ontology.yaml: genai, data, appdev, integration, opsec - other
   tags stay in the Document row's `tags` array only), markdown drawio/svg
   embeds -> Diagram nodes, markdown links -> Source nodes and
   external_links rows, canonical lexicon (config/ontology.yaml) scan of
   the body -> Entity nodes + MENTIONS edges (word-boundary, case-
   insensitive, and restricted to substantive body prose - see
   find_substantive_alias_match), and a conservative sentence-trigger scan
   -> atomic Requirement/Constraint nodes + HAS_REQUIREMENT/HAS_CONSTRAINT
   edges (see REQUIREMENT_TRIGGERS/CONSTRAINT_TRIGGERS).

2. Curated overrides (NOT automated, manually verified against the exact
   document text, in CURATED_FACTS / REQUIREMENT_ATOMIC_OVERRIDES /
   CONSTRAINT_ATOMIC_OVERRIDES below): this is an explicit, documented
   override layer, not the primary extraction path.
   - CURATED_FACTS holds relationships that need semantic judgement a
     deterministic rule cannot safely make on its own (AUTHENTICATES_WITH,
     CONNECTS_TO, EXPOSES, CONSUMES_DATA_FROM, SUPPORTS_PROTOCOL, REQUIRES,
     HAS_USE_CASE between two *specific* entities).
   - REQUIREMENT_ATOMIC_OVERRIDES / CONSTRAINT_ATOMIC_OVERRIDES replace the
     automatic layer's generic Document-level fact for one exact verbatim
     sentence with one or more atomic, more-specific facts, when that
     sentence asserts more than one requirement/constraint at once. Every
     atomic fact still quotes the same full verbatim sentence - only the
     target node and/or the from-entity is split out and made more
     specific/granular. Nothing is invented; see notes/findings.md.

The document list is read from a YAML manifest (see config/pilots/*.yaml,
key `documents:` - a list of repo-root-relative paths). The manifest is the
single source of truth for which documents a given pilot run covers; it is
never duplicated as a Python constant.

Usage:
    python3 scripts/extract_document.py
    python3 scripts/extract_document.py --manifest config/pilots/pilot2.yaml --output-dir data/pilot2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import yaml

from path_safety import (
    PathSecurityError,
    resolve_within,
    validate_manifest_extension,
    validate_ra_document_path,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RESEARCH_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SITE_URL = "https://architecture.learning.sap.com"
FRONT_MATTER_RE = re.compile(r"^---\n(.*?\n)---\n", re.DOTALL)
INTRODUCTION_MARKER = "__introduction__"

DEFAULT_MANIFEST = "config/pilots/pilot1.yaml"
DEFAULT_OUTPUT_DIR = "data/pilot"


def resolve_manifest_path(raw: str) -> str:
    """Validate --manifest: must resolve inside config/pilots/, must exist
    as a regular .yaml/.yml file. See audit finding F1."""
    allowed_root = os.path.join(RESEARCH_ROOT, "config", "pilots")
    real = resolve_within(
        raw, base_dir=RESEARCH_ROOT, allowed_root=allowed_root,
        must_exist=True, must_be_file=True, label="--manifest",
    )
    validate_manifest_extension(real, label="--manifest")
    return real


def resolve_document_path(rel_path: str) -> str:
    """Validate one manifest `documents:` entry: must be a relative path,
    must resolve (after collapsing ../ and symlinks) inside REPO_ROOT, must
    live under docs/ref-arch/, must end in .md, must exist as a regular
    file. See audit finding F1."""
    if os.path.isabs(rel_path):
        raise PathSecurityError("manifest document entry must be a relative path")
    real = resolve_within(
        rel_path, base_dir=REPO_ROOT, allowed_root=REPO_ROOT,
        must_exist=True, must_be_file=True, label="manifest document entry",
    )
    validate_ra_document_path(real, REPO_ROOT, label="manifest document entry")
    return real


def resolve_output_dir(raw: str, manifest_real_path: str, allow_pilot1_overwrite: bool) -> str:
    """Validate --output-dir: must resolve inside data/, must not be an
    existing file, and must not target data/pilot/ unless the manifest is
    pilot1.yaml or --allow-pilot1-overwrite was passed explicitly. See
    audit finding F2."""
    allowed_root = os.path.join(RESEARCH_ROOT, "data")
    real = resolve_within(raw, base_dir=RESEARCH_ROOT, allowed_root=allowed_root, label="--output-dir")
    if os.path.isfile(real):
        raise PathSecurityError("--output-dir must not resolve to an existing file")

    pilot1_dir_real = os.path.realpath(os.path.join(RESEARCH_ROOT, "data", "pilot"))
    manifest_is_pilot1 = os.path.basename(manifest_real_path) == "pilot1.yaml"
    if real == pilot1_dir_real and not manifest_is_pilot1 and not allow_pilot1_overwrite:
        raise PathSecurityError(
            "--output-dir data/pilot is reserved for config/pilots/pilot1.yaml; "
            "pass --allow-pilot1-overwrite to target it with a different manifest"
        )
    return real


def load_manifest(path: str) -> list[str]:
    manifest = load_yaml(path)
    docs = manifest.get("documents") or []
    if not docs:
        raise ValueError(f"manifest {path} has no `documents` entries")
    return docs


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


# Ordered: first matching rule wins. Placed before the generic "SAP.com"
# catch-all so specific SAP subdomains (help/community/discovery-center/...)
# are classified precisely rather than falling into the generic bucket.
DOMAIN_RULES = [
    ("SAP Architecture Center", ["architecture.learning.sap.com"]),
    ("SAP Help", ["help.sap.com"]),
    ("SAP Discovery Center", ["discovery-center.cloud.sap"]),
    ("SAP Community", ["community.sap.com"]),
    ("SAP Business Accelerator Hub", ["api.sap.com"]),
    ("SAP Developer", ["developer.sap.com", "developers.sap.com"]),
    ("SAP Learning", ["learning.sap.com"]),
    ("SAP.com", ["sap.com"]),
    ("GitHub", ["github.com"]),
    ("YouTube", ["youtube.com", "youtu.be"]),
]


def classify_domain(url: str) -> str:
    """Classify by hostname only - never inspects query parameters, so no
    credential/token that might appear in a query string is ever read."""
    host = urlparse(url).netloc.lower()
    if host.endswith("hana.ondemand.com"):
        return "SAP BTP Hosted App"
    for category, matches in DOMAIN_RULES:
        if any(host == d or host.endswith("." + d) for d in matches):
            return category
    return "Other"


def find_section(body: str, char_offset: int) -> str:
    """Nearest preceding markdown heading ('## X') for a body offset, or the
    INTRODUCTION_MARKER convention if the offset precedes every heading."""
    headings = list(re.finditer(r"^#{2,4}\s+(.*)$", body[:char_offset], re.MULTILINE))
    if not headings:
        return INTRODUCTION_MARKER
    return headings[-1].group(1).strip()


def sentence_containing(body: str, needle_idx: int, needle_len: int) -> str:
    """Extract the sentence (or list item) surrounding a match, as evidence."""
    start = max(body.rfind(".", 0, needle_idx), body.rfind("\n", 0, needle_idx))
    end_candidates = [e for e in (body.find(".", needle_idx + needle_len), body.find("\n", needle_idx + needle_len)) if e != -1]
    end = min(end_candidates) if end_candidates else len(body)
    snippet = body[start + 1:end + 1].strip()
    snippet = re.sub(r"\s+", " ", snippet)
    return snippet[:500]


ADMONITION_FENCE_RE = re.compile(r"^:::.*\n?", re.MULTILINE)


def split_sentences(body: str) -> list[str]:
    """Naive but sufficient sentence splitter for prose/admonition blocks.
    Docusaurus admonition fence lines (':::info Disclaimer', ':::') are
    stripped first so they don't get glued onto the following sentence -
    without this, "not yet generally available" facts would never match
    CONSTRAINT_ATOMIC_OVERRIDES because the captured text would start with
    the fence marker instead of the real sentence."""
    cleaned = ADMONITION_FENCE_RE.sub("", body)
    sentences = []
    for m in re.finditer(r"[^.!?]*[.!?]", cleaned, re.DOTALL):
        s = re.sub(r"\s+", " ", m.group(0)).strip()
        if s:
            sentences.append(s)
    return sentences


LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def link_anchor_spans(body: str) -> list[tuple[int, int]]:
    """(start, end) of the visible anchor TEXT of every markdown link - a
    brand name appearing only inside a link's anchor text is not treated as
    a substantive mention (see find_substantive_alias_match)."""
    return [(m.start(1), m.end(1)) for m in LINK_RE.finditer(body)]


def secondary_section_spans(body: str, secondary_headings_lower: set[str]) -> list[tuple[int, int]]:
    """(start, end) of every section whose heading is in the closed
    'secondary' vocabulary (config/ontology.yaml secondary_section_headings)
    - supplementary reading / example-scenario sections, excluded from
    MENTIONS scanning. 'Services and Components' is NOT in that vocabulary."""
    spans = []
    headings = list(re.finditer(r"^(#{2,4})\s+(.*)$", body, re.MULTILINE))
    for i, h in enumerate(headings):
        title = re.sub(r"[*_`]", "", h.group(2)).strip().lower()
        if title in secondary_headings_lower:
            start = h.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
            spans.append((start, end))
    return spans


def in_any_span(idx: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= idx < e for s, e in spans)


def find_substantive_alias_match(body_lower: str, alias: str, exclude_spans: list[tuple[int, int]]):
    """Word-boundary, case-insensitive search for `alias` in body_lower,
    returning the first occurrence NOT inside `exclude_spans` (link anchor
    text and/or secondary sections), or None if every occurrence is
    incidental. This is what prevents both false positives on short aliases
    (ias/a2a/mcp/rag/btp can no longer match inside a longer word) and
    MENTIONS-from-a-link-title noise (see notes/findings.md hallazgo 7)."""
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])")
    for m in pattern.finditer(body_lower):
        if in_any_span(m.start(), exclude_spans):
            continue
        return m.start(), m.end()
    return None


# Conservative, high-precision trigger phrases for the automatic
# Requirement/Constraint detector. Intentionally narrow: expanding this list
# for the full 114-document corpus requires deliberate, reviewed additions,
# not silent broadening - a missed requirement/constraint is preferable to a
# fabricated one.
REQUIREMENT_TRIGGERS = [
    re.compile(r"\bmust be set up\b", re.IGNORECASE),
    re.compile(r"\bmust be provisioned\b", re.IGNORECASE),
    re.compile(r"\bmust be established\b", re.IGNORECASE),
    re.compile(r"\bis required\b", re.IGNORECASE),
]
CONSTRAINT_TRIGGERS = [
    re.compile(r"\bnot yet generally available\b", re.IGNORECASE),
    re.compile(r"\bunidirectional\b", re.IGNORECASE),
]

# See module docstring, layer 2. Every entry is keyed by the EXACT verbatim
# sentence the automatic detector above would otherwise attach a generic
# Document-level HAS_REQUIREMENT/HAS_CONSTRAINT fact to. Presence here
# suppresses that generic fact and substitutes the atomic ones listed.
REQUIREMENT_ATOMIC_OVERRIDES: dict[str, list[dict]] = {
    "To provision Joule Studio, Joule must be set up in the target landscape along with SAP Build Process Automation as part of the SAP Build tenant with the build-default plan utilizing SAP Identity Authentication Service (IAS).": [
        {
            "node_id": "entity:req-joule-provisioned",
            "name": "Joule must be provisioned in the target landscape",
            "from_id": "entity:joule-studio", "from_type": "SAPService",
        },
        {
            "node_id": "entity:req-build-process-automation-provisioned",
            "name": "SAP Build Process Automation must be provisioned as part of the SAP Build tenant (build-default plan)",
            "from_id": "entity:joule-studio", "from_type": "SAPService",
        },
        {
            "node_id": "entity:req-ias-utilized-for-provisioning",
            "name": "Provisioning must utilize SAP Identity Authentication Service (IAS)",
            "from_id": "entity:joule-studio", "from_type": "SAPService",
        },
    ],
}
CONSTRAINT_ATOMIC_OVERRIDES: dict[str, list[dict]] = {
    "The Agent Gateway is not yet generally available (GA).": [
        {
            "node_id": "entity:constraint-agent-gateway-not-ga",
            "name": "Agent Gateway is not yet generally available (GA)",
            "from_id": "entity:agent-gateway", "from_type": "API",
        },
    ],
    "As a result, the current architecture supports unidirectional (outbound) communication only.": [
        {
            "node_id": "entity:constraint-unidirectional-outbound-only",
            "name": "Current architecture (RA0029) supports unidirectional (outbound) communication only",
            "from_id": "ra:RA0029", "from_type": "ReferenceArchitecture",
        },
    ],
}


class Extractor:
    def __init__(self):
        self.commit = repo_commit()
        self.extraction_date = datetime.now(timezone.utc).isoformat()
        self.ontology = load_yaml(os.path.join(RESEARCH_ROOT, "config", "ontology.yaml"))
        self.canonical = self.ontology["canonical_entities"]
        self.domain_allowed_tags = set(self.ontology["technology_domain_allowed_tags"])
        self.secondary_headings = {h.lower() for h in self.ontology["secondary_section_headings"]}

        self.documents: list[dict] = []
        self.entities: list[dict] = []
        self.sources: list[dict] = []
        self.external_links: list[dict] = []
        self.relationships: list[dict] = []

        self._ra_nodes_seen: set[str] = set()
        self._entity_rows_by_id: dict[str, dict] = {}
        self._source_nodes_seen: dict[str, str] = {}  # url -> node_id
        self._relationships_by_id: dict[str, dict] = {}
        self._duplicate_relationships_consolidated = 0

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

    def _is_canonical_citation_section(self, section) -> bool:
        return bool(section) and section.lower() in self.secondary_headings

    def _prefer_evidence(self, current: dict, candidate: dict) -> bool:
        """True if `candidate`'s evidence/section/evidence_type/confidence
        should replace `current`'s when the same relationship (identical
        from_id/type/to_id/source_file, i.e. identical relationship_id) is
        observed a second time in the same document with different quoted
        text - e.g. the same source URL linked once in prose and again in
        a 'Resources' list. Prefers the occurrence quoted from the
        document's canonical citation/example section (config/ontology.yaml
        secondary_section_headings, e.g. 'Resources') over an incidental
        inline mention, since that section is the deliberate citation of
        the fact rather than a passing reference. Falls back to the longer
        quoted text when neither or both occurrences are from such a
        section - a purely lexical, content-agnostic tiebreaker."""
        current_canonical = self._is_canonical_citation_section(current["source_section"])
        candidate_canonical = self._is_canonical_citation_section(candidate["source_section"])
        if candidate_canonical != current_canonical:
            return candidate_canonical
        return len(candidate["evidence"]) > len(current["evidence"])

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
        existing = self._relationships_by_id.get(rid)
        if existing is None:
            self.relationships.append(row)
            self._relationships_by_id[rid] = row
            return

        # Same relationship_id seen again in the same document: not a second
        # semantic fact (identical from/type/to/source_file), just a repeat
        # citation/mention. Consolidate into the single existing row instead
        # of appending a duplicate.
        self._duplicate_relationships_consolidated += 1
        if row["evidence"] != existing["evidence"] and self._prefer_evidence(existing, row):
            existing["evidence"] = row["evidence"]
            existing["source_section"] = row["source_section"]
            existing["evidence_type"] = row["evidence_type"]
            existing["confidence"] = row["confidence"]

    def get_or_create_entity(self, node_id, type_, name, doc_node_id, canonical_id,
                              extraction_method, rel_path, section, doc_url,
                              evidence, evidence_type, confidence):
        """Create the canonical entity row on first sighting; on every later
        sighting (in a different document), append to found_in_documents
        instead of skipping - this is what lets one entity be correctly
        attributed to every document that mentions it (see
        notes/findings.md hallazgo 13), not just the first one seen."""
        existing = self._entity_rows_by_id.get(node_id)
        if existing is None:
            row = {
                "node_id": node_id, "type": type_, "name": name,
                "canonical_id": canonical_id,
                "extraction_method": extraction_method,
                "found_in_documents": [doc_node_id],
                **self._prov(rel_path, section, doc_url, evidence, evidence_type, confidence),
            }
            self.entities.append(row)
            self._entity_rows_by_id[node_id] = row
        elif doc_node_id not in existing["found_in_documents"]:
            existing["found_in_documents"].append(doc_node_id)
        return node_id

    # ---------- automatic / deterministic extraction ----------
    def process_document(self, rel_path: str):
        real_abs_path = resolve_document_path(rel_path)
        with open(real_abs_path, encoding="utf-8") as f:
            raw = f.read()
        fm, body = parse_front_matter(raw)
        body_lower = body.lower()

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

        # PART_OF - from folder hierarchy, unchanged/generic.
        self.add_relationship(
            "PART_OF", doc_node_id, ra_node_id, "Document", "ReferenceArchitecture",
            rel_path, None, doc_url,
            f"{rel_path} is stored under docs/ref-arch/{ra_folder}/", "explicit", 1.0,
        )

        # --- TechnologyDomain nodes from tags, restricted to the closed
        # vocabulary (genai, data, appdev, integration, opsec). Tags outside
        # it (aws, gcp, azure, ibm, cap, build, ...) stay only in the
        # Document row's own `tags` array - they are not promoted to nodes.
        for tag in fm.get("tags") or []:
            if tag not in self.domain_allowed_tags:
                continue
            dom_id = f"entity:TechnologyDomain:{slugify(tag)}"
            self.get_or_create_entity(
                dom_id, "TechnologyDomain", tag, doc_node_id, None,
                "front_matter_tag", rel_path, None, doc_url,
                f"tags: [{', '.join(fm.get('tags') or [])}]", "explicit", 1.0,
            )
            self.add_relationship(
                "BELONGS_TO_DOMAIN", doc_node_id, dom_id, "Document", "TechnologyDomain",
                rel_path, None, doc_url, f"front matter tag '{tag}'", "explicit", 1.0,
            )

        # --- Diagram nodes from drawio/svg embeds ---
        for m in re.finditer(r"!\[[^\]]*\]\(\.?/?([\w\-./]+\.(?:drawio|svg))\)", body):
            asset_rel = m.group(1)
            asset_path = os.path.normpath(os.path.join(os.path.dirname(rel_path), asset_rel)).replace(os.sep, "/")
            section = find_section(body, m.start())
            diagram_id = f"diagram:{doc_node_id}:{os.path.basename(asset_rel)}"
            ext = os.path.splitext(asset_rel)[1].lstrip(".")
            self.get_or_create_entity(
                diagram_id, "Diagram", os.path.basename(asset_rel), doc_node_id, None,
                "drawio_reference" if ext == "drawio" else "svg_reference",
                rel_path, section, doc_url, m.group(0), "explicit", 1.0,
            )
            self.add_relationship(
                "HAS_DIAGRAM", doc_node_id, diagram_id, "Document", "Diagram",
                rel_path, section, doc_url, m.group(0), "explicit", 1.0,
            )
            if not os.path.isfile(os.path.join(REPO_ROOT, asset_path)):
                print(f"WARNING: referenced diagram file not found on disk: {asset_path}", file=sys.stderr)

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
        # Restricted to substantive body prose: excludes markdown link
        # anchor text and the closed "secondary section" vocabulary
        # (Resources / Examples / Examples in an SAP context).
        exclude_spans = link_anchor_spans(body) + secondary_section_spans(body, self.secondary_headings)
        for entry in self.canonical:
            match = None
            for alias in entry["aliases"]:
                match = find_substantive_alias_match(body_lower, alias, exclude_spans)
                if match:
                    break
            if not match:
                continue
            idx, end = match
            node_id = f"entity:{entry['canonical_id']}"
            section = find_section(body, idx)
            evidence = sentence_containing(body, idx, end - idx)
            self.get_or_create_entity(
                node_id, entry["type"], entry["canonical_name"], doc_node_id,
                entry["canonical_id"], "canonical_lexicon_match",
                rel_path, section, doc_url, evidence, "explicit", 0.9,
            )
            self.add_relationship(
                "MENTIONS", doc_node_id, node_id, "Document", entry["type"],
                rel_path, section, doc_url, evidence, "explicit", 0.85,
            )

        # --- sentence-trigger scan -> atomic Requirement/Constraint nodes ---
        sentences = split_sentences(body)
        for sentence, triggers, node_type, overrides, rel_type in (
            *((s, REQUIREMENT_TRIGGERS, "Requirement", REQUIREMENT_ATOMIC_OVERRIDES, "HAS_REQUIREMENT") for s in sentences),
            *((s, CONSTRAINT_TRIGGERS, "Constraint", CONSTRAINT_ATOMIC_OVERRIDES, "HAS_CONSTRAINT") for s in sentences),
        ):
            if not any(trig.search(sentence) for trig in triggers):
                continue
            idx = body.find(sentence)
            section = find_section(body, idx) if idx != -1 else INTRODUCTION_MARKER
            override_facts = overrides.get(sentence)
            if override_facts:
                for ov in override_facts:
                    self.get_or_create_entity(
                        ov["node_id"], node_type, ov["name"], doc_node_id, None,
                        f"{node_type.lower()}_trigger_sentence",
                        rel_path, section, doc_url, sentence, "explicit", 0.9,
                    )
                    self.add_relationship(
                        rel_type, ov["from_id"], ov["node_id"], ov["from_type"], node_type,
                        rel_path, section, doc_url, sentence, "explicit", 0.9,
                    )
            else:
                generic_id = f"entity:{node_type}:{short_hash(sentence)}"
                self.get_or_create_entity(
                    generic_id, node_type, sentence[:160], doc_node_id, None,
                    f"{node_type.lower()}_trigger_sentence",
                    rel_path, section, doc_url, sentence, "explicit", 0.85,
                )
                self.add_relationship(
                    rel_type, doc_node_id, generic_id, "Document", node_type,
                    rel_path, section, doc_url, sentence, "explicit", 0.85,
                )

        return doc_node_id, ra_node_id, fm, body, rel_path, doc_url

    # ---------- curated overrides (manual, see notes/findings.md) ----------
    def apply_curated_facts(self, doc_node_id, ra_node_id, rel_path, doc_url, body):
        facts = CURATED_FACTS.get(rel_path, [])
        for fact in facts:
            section = fact.get("section")
            from_id = fact["from_id"].format(doc=doc_node_id, ra=ra_node_id)
            to_id = fact["to_id"].format(doc=doc_node_id, ra=ra_node_id)

            if fact.get("create_entity"):
                ce = fact["create_entity"]
                node_id = ce["node_id"].format(doc=doc_node_id, ra=ra_node_id)
                self.get_or_create_entity(
                    node_id, ce["type"], ce["name"], doc_node_id, None,
                    ce["extraction_method"], rel_path, section, doc_url,
                    fact["evidence"], fact["evidence_type"], fact["confidence"],
                )

            self.add_relationship(
                fact["type"], from_id, to_id, fact["from_type"], fact["to_type"],
                rel_path, section, doc_url, fact["evidence"], fact["evidence_type"], fact["confidence"],
            )

    def run(self, doc_list: list[str]):
        for rel_path in doc_list:
            doc_node_id, ra_node_id, fm, body, rel_path, doc_url = self.process_document(rel_path)
            self.apply_curated_facts(doc_node_id, ra_node_id, rel_path, doc_url, body)

    def write(self, out_dir: str):
        print(f"Consolidated {self._duplicate_relationships_consolidated} duplicate relationship(s) "
              f"into their single existing row (same from_id/type/to_id/source_file).")
        os.makedirs(out_dir, exist_ok=True)
        if os.path.realpath(out_dir) != out_dir:
            raise PathSecurityError("--output-dir changed after creation (possible symlink race); aborting")

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
# Curated overrides: relationships between two *specific* entities that need
# semantic judgement (a verb like "relies on X for Y" or "exposes X via Y")
# beyond what a deterministic pattern can safely assert. This is NOT the
# primary extraction path - see module docstring. Every entry was manually
# verified against the exact document text.
#
# Removed relative to the pre-audit version (see notes/findings.md FASE 1
# corrections):
#   - REQUIRES joule-studio -> req-joule-studio-provisioning: superseded by
#     the automatic detector + REQUIREMENT_ATOMIC_OVERRIDES (now 3 atomic
#     Requirement facts instead of 1 compound one).
#   - MENTIONS doc:76ec36 -> constraint-agent-gateway-not-ga: superseded by
#     the automatic detector + CONSTRAINT_ATOMIC_OVERRIDES (now 2 atomic
#     Constraint facts, correctly attributed to BOTH doc:98efa0 and
#     doc:76ec36, which both contain the identical disclaimer text - the
#     pre-audit version only captured it from doc:76ec36).
#   - USES joule -> generative-ai-hub (inferred, 0.55): removed outright.
#     Its evidence ("Generative AI Hub: Foundation models, prompt
#     optimization, ...") never mentions Joule; an inferred relationship
#     whose evidence only substantiates one of its two endpoints is not
#     kept at low confidence, it is deleted.
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
            # Deliberately-inferred edge: the source text lists integration
            # channels in one sentence without pairing each one explicitly
            # to Joule Studio via its own verb - kept low-confidence. Both
            # endpoints (Joule Studio, SAP S/4HANA) ARE named in the quoted
            # evidence, satisfying the "inferred must substantiate both
            # sides" rule.
            "type": "CONNECTS_TO", "from_type": "SAPService", "to_type": "SAPProduct",
            "from_id": "entity:joule-studio", "to_id": "entity:sap-s4hana",
            "section": "Characteristics",
            "evidence": "Joule Studio integrates seamlessly with various SAP and third-party cloud solutions, including SAP ECC, SAP S/4HANA, and S/4HANA Cloud Private Edition, providing comprehensive support for hybrid landscapes.",
            "evidence_type": "inferred", "confidence": 0.5,
        },
    ],
    "docs/ref-arch/RA0029/readme.md": [
        {
            "type": "CONSUMES_DATA_FROM", "from_type": "SAPService", "to_type": "SAPService",
            "from_id": "entity:generative-ai-hub", "to_id": "entity:sap-hana-cloud",
            "section": "Architecture",
            # Full verbatim sentence - no ellipsis (previously elided).
            "evidence": "**Generative AI Hub:** Foundation models, prompt optimization, orchestration capabilities (grounding, templating, data masking, I/O filtering) and vector search via SAP HANA Cloud.",
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
            "section": INTRODUCTION_MARKER,
            "evidence": "Joule acts as an A2A client to communicate with external agents, while agents themselves use MCP to discover and consume tools from MCP servers.",
            "evidence_type": "explicit", "confidence": 0.95,
        },
        {
            "type": "SUPPORTS_PROTOCOL", "from_type": "Agent", "to_type": "Protocol",
            "from_id": "entity:joule", "to_id": "entity:mcp-protocol",
            "section": INTRODUCTION_MARKER,
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
            # No trailing period in the source bullet - kept verbatim.
            "evidence": "**Authentication:** Secured through SAP Cloud Identity Services (IAS) App2App tokens with named user context",
            "evidence_type": "explicit", "confidence": 0.95,
        },
        {
            # entity:sap-knowledge-graph is already created automatically by
            # the canonical lexicon scan (it's in config/ontology.yaml
            # canonical_entities) - no create_entity block needed here.
            "type": "CONSUMES_DATA_FROM", "from_type": "Protocol", "to_type": "DataSource",
            "from_id": "entity:mcp-protocol", "to_id": "entity:sap-knowledge-graph",
            "section": "Model Context Protocol (MCP)",
            "evidence": "At SAP, MCP is used to provide Joule Agents with semantically enriched access to SAP business capabilities and domain knowledge, including content from SAP Knowledge Graph.",
            "evidence_type": "explicit", "confidence": 0.9,
        },
        {
            "type": "REQUIRES", "from_type": "API", "to_type": "IdentitySecurity",
            "from_id": "entity:agent-gateway", "to_id": "entity:sap-cloud-identity-services",
            "section": "Bring Your Own Agent (Outbound)",
            "evidence": "To ensure secure inbound communication and validate server updates, an Identity Authentication Service (IAS) App2App trust relationship must be established between Joule and the target agent server.",
            "evidence_type": "explicit", "confidence": 0.85,
        },
    ],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST,
                         help=f"YAML manifest with a `documents:` list (default: {DEFAULT_MANIFEST})")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                         help=f"directory to write *.jsonl into (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--allow-pilot1-overwrite", action="store_true",
                         help="required to target --output-dir data/pilot with a manifest other than pilot1.yaml")
    args = parser.parse_args()

    try:
        manifest_real = resolve_manifest_path(args.manifest)
        doc_list = load_manifest(manifest_real)
        output_dir_real = resolve_output_dir(args.output_dir, manifest_real, args.allow_pilot1_overwrite)

        ex = Extractor()
        ex.run(doc_list)
        ex.write(output_dir_real)
    except PathSecurityError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
