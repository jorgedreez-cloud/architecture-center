#!/usr/bin/env python3
"""
Scan docs/ref-arch (read-only) and build a full metadata inventory of every
readme.md: front matter, draft/unlisted status, RA grouping, drawio/image
assets and the computed public URL. Writes data/raw/ra_inventory.json.

This does NOT read document bodies for entity extraction (that is
extract_document.py's job) - it only parses front matter, so it is cheap
enough to run over the whole site, which is what lets us justify the
114 vs 120 vs 31 reconciliation in notes/findings.md.

Usage:
    python3 scripts/extract_inventory.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RESEARCH_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REF_ARCH_DIR = os.path.join(REPO_ROOT, "docs", "ref-arch")
SITE_URL = "https://architecture.learning.sap.com"
FRONT_MATTER_RE = re.compile(r"^---\n(.*?\n)---\n", re.DOTALL)


def repo_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def parse_front_matter(path: str) -> dict | None:
    with open(path, encoding="utf-8") as f:
        content = f.read()
    m = FRONT_MATTER_RE.match(content)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        print(f"WARNING: could not parse front matter YAML in {path}: {e}", file=sys.stderr)
        return None
    return data


def relpath(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def find_assets(doc_dir: str) -> dict:
    drawio = sorted(
        relpath(p) for p in glob.glob(os.path.join(doc_dir, "drawio", "*.drawio"))
    )
    images = sorted(
        relpath(p)
        for p in glob.glob(os.path.join(doc_dir, "images", "*"))
        if os.path.isfile(p)
    )
    return {"drawio_files": drawio, "image_files": images}


def public_url(slug: str | None) -> str | None:
    if not slug:
        return None
    return f"{SITE_URL}/docs{slug}"


def load_topics() -> list[dict]:
    topics_path = os.path.join(RESEARCH_ROOT, "config", "topics.yaml")
    with open(topics_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["topics"]


def matched_topics(fm: dict, topics: list[dict]) -> list[str]:
    haystack = " ".join(
        str(fm.get(k, "")) for k in ("title", "description")
    ).lower()
    haystack += " " + " ".join(fm.get("keywords") or []).lower()
    haystack += " " + " ".join(fm.get("tags") or []).lower()
    hits = []
    for topic in topics:
        for kw in topic["keywords"]:
            if kw.strip().lower() in haystack:
                hits.append(topic["id"])
                break
    return hits


def main() -> None:
    if not os.path.isdir(REF_ARCH_DIR):
        print(f"ERROR: {REF_ARCH_DIR} not found", file=sys.stderr)
        sys.exit(1)

    commit = repo_commit()
    extraction_date = datetime.now(timezone.utc).isoformat()
    topics = load_topics()

    all_readmes = sorted(glob.glob(os.path.join(REF_ARCH_DIR, "**", "readme.md"), recursive=True))

    entries = []
    ra_folders = set()
    for path in all_readmes:
        rel = relpath(path)
        fm = parse_front_matter(path)
        parts = rel.split("/")  # docs/ref-arch/[RA0029/[subfolder/]]readme.md
        is_landing_page = rel == "docs/ref-arch/readme.md"
        ra_folder = None
        subfolder = None
        if not is_landing_page:
            ra_folder = parts[2]  # RA0029
            ra_folders.add(ra_folder)
            if len(parts) > 4:
                subfolder = parts[3]

        doc_dir = os.path.dirname(path)
        assets = find_assets(doc_dir)

        entry = {
            "source_file": rel,
            "is_landing_page": is_landing_page,
            "ra_folder": ra_folder,
            "subfolder": subfolder,
            "is_ra_root": (not is_landing_page) and subfolder is None,
            "is_demo_template": ra_folder == "RA0000",
            "front_matter_id": (fm or {}).get("id"),
            "slug": (fm or {}).get("slug"),
            "public_url": public_url((fm or {}).get("slug")),
            "title": (fm or {}).get("title"),
            "tags": (fm or {}).get("tags") or [],
            "keywords": (fm or {}).get("keywords") or [],
            "draft": bool((fm or {}).get("draft", False)),
            "unlisted": bool((fm or {}).get("unlisted", False)),
            "sidebar_position": (fm or {}).get("sidebar_position"),
            "last_update": (fm or {}).get("last_update"),
            "contributors": (fm or {}).get("contributors") or [],
            **assets,
            "matched_topics": matched_topics(fm or {}, topics) if fm else [],
        }
        entries.append(entry)

    published_listed = [e for e in entries if not e["draft"] and not e["unlisted"]]
    draft_docs = [e for e in entries if e["draft"]]
    unlisted_docs = [e for e in entries if e["unlisted"] and not e["draft"]]
    real_ra_docs = [e for e in entries if e["ra_folder"] and not e["is_demo_template"]]

    summary = {
        "total_readme_files": len(entries),
        "landing_page_count": sum(1 for e in entries if e["is_landing_page"]),
        "ra_folder_count_total": len(ra_folders),
        "ra_folder_count_real": len(ra_folders - {"RA0000"}),
        "ra_docs_total": len(real_ra_docs) + sum(1 for e in entries if e["is_demo_template"]),
        "real_ra_docs": len(real_ra_docs),
        "real_ra_root_docs": sum(1 for e in real_ra_docs if e["is_ra_root"]),
        "real_ra_subpage_docs": sum(1 for e in real_ra_docs if not e["is_ra_root"]),
        "draft_count": len(draft_docs),
        "unlisted_count": sum(1 for e in entries if e["unlisted"]),
        "published_and_listed_count": len(published_listed),
        "topic_matched_count": sum(1 for e in entries if e["matched_topics"]),
    }

    output = {
        "generated_at": extraction_date,
        "repository_commit": commit,
        "site_url": SITE_URL,
        "summary": summary,
        "documents": entries,
    }

    out_dir = os.path.join(RESEARCH_ROOT, "data", "raw")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "ra_inventory.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    print(f"Wrote {out_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
