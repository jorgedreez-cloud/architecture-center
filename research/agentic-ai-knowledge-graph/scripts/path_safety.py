#!/usr/bin/env python3
"""
Shared, security-critical path validation for the agentic-ai-knowledge-graph
CLI scripts (extract_document.py, build_graph.py, validate_pilot.py).

This is the single implementation of path containment / traversal / symlink-
escape checks used by all three scripts - see the audit findings F1/F2/F3
(FASE 3, path traversal via --manifest, manifest `documents:` entries,
--output-dir, --input-dir). Scripts must never re-implement this logic
locally; that duplication is exactly what let the original gap exist in all
three places at once.

Design:
  - Every candidate path is resolved with os.path.realpath(), which both
    collapses ".."/"." components AND fully resolves symlinks (including
    chains of symlinks) to their final filesystem target.
  - The resolved path is then required to be contained within an explicit
    `allowed_root` via os.path.commonpath() - this is what catches BOTH
    "../" traversal AND a symlink whose target lives outside the root,
    since both end up as the same thing after realpath: a path outside
    allowed_root.
  - Error messages never echo the rejected path's resolved (real) location
    or any detail about what exists elsewhere on the filesystem - only the
    caller-supplied `label` and the fact that containment/shape validation
    failed. This avoids using the tool as a filesystem existence/content
    oracle for paths outside the project.
"""
from __future__ import annotations

import os


class PathSecurityError(ValueError):
    """A path failed containment, traversal, or symlink-escape validation."""


def resolve_within(
    path: str,
    *,
    base_dir: str,
    allowed_root: str,
    must_exist: bool = False,
    must_be_file: bool = False,
    must_be_dir: bool = False,
    label: str = "path",
) -> str:
    """Resolve `path` (joined against base_dir first if relative) and verify
    the fully symlink-resolved result is contained within allowed_root.

    Returns the resolved absolute path on success. Raises PathSecurityError
    on any violation - traversal, symlink escape, wrong kind, or missing
    when required. Never returns a path outside allowed_root."""
    allowed_root_real = os.path.realpath(allowed_root)
    candidate = path if os.path.isabs(path) else os.path.join(base_dir, path)
    real = os.path.realpath(candidate)

    try:
        common = os.path.commonpath([real, allowed_root_real])
    except ValueError:
        common = None  # e.g. different drives on Windows
    if common != allowed_root_real:
        raise PathSecurityError(
            f"{label} must resolve inside the allowed directory "
            f"(rejected: traversal or symlink escape)"
        )

    if must_exist and not os.path.exists(real):
        raise PathSecurityError(f"{label} does not exist")
    if must_be_file and not os.path.isfile(real):
        raise PathSecurityError(f"{label} must be an existing regular file")
    if must_be_dir and not os.path.isdir(real):
        raise PathSecurityError(f"{label} must be an existing directory")

    return real


def validate_ra_document_path(real_path: str, repo_root: str, label: str = "document") -> None:
    """Business-rule check for a manifest `documents:` entry or a
    relationship's `source_file`: must live under docs/ref-arch/ and end in
    .md. Computed from the already-resolved `real_path` (not the raw
    string) so a payload like 'docs/ref-arch/../../etc/passwd' cannot pass
    by satisfying a naive string-prefix check alone - resolve_within's
    containment check must run first."""
    repo_root_real = os.path.realpath(repo_root)
    rel = os.path.relpath(real_path, repo_root_real).replace(os.sep, "/")
    if rel.startswith("..") or not rel.startswith("docs/ref-arch/"):
        raise PathSecurityError(f"{label} must be located under docs/ref-arch/")
    if not rel.endswith(".md"):
        raise PathSecurityError(f"{label} must end in .md")


def validate_manifest_extension(real_path: str, label: str = "manifest") -> None:
    if not real_path.endswith((".yaml", ".yml")):
        raise PathSecurityError(f"{label} must end in .yaml or .yml")
