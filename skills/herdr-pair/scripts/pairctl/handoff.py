"""pairctl handoff: Handoff fences: parsing, fail-closed lint and the pre-dispatch snapshot."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from .constants import DIR_MODE, FENCE_RE, SNAPSHOT_COPY_LIMIT, TMP_PATH_RE
from .state import atomic_text, chmod_private, file_sha256


def parse_handoff_fences(text: str) -> dict[str, list[str]]:
    result = {key: [] for key in ("可以改", "只读输入", "可以新建", "不许动", "环境")}
    for match in FENCE_RE.finditer(text):
        value = match.group(2).strip().rstrip("；;")
        if match.group(1) == "环境":
            result["环境"].append(value)
        else:
            result[match.group(1)].extend(
                part.strip().rstrip("；;") for part in re.split(r"[,，]", value) if part.strip()
            )
    return result


def normalized_fence_path(value: str, cwd: str | None = None) -> str:
    value = value.strip().replace("\\", "/")
    path = Path(value).expanduser()
    if path.is_absolute() and cwd:
        try:
            value = path.resolve().relative_to(Path(cwd).resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()
    else:
        value = os.path.normpath(value).replace("\\", "/")
    return value.rstrip("/") or "/"


def paths_intersect(left: str, right: str) -> bool:
    a, b = normalized_fence_path(left), normalized_fence_path(right)
    if a == "." or b == ".":
        other = b if a == "." else a
        return not Path(other).is_absolute() and other != ".." and not other.startswith("../")
    return bool(a and b and (a == b or a.startswith(b + "/") or b.startswith(a + "/")))


def handoff_lint(cwd: str, text: str) -> list[dict[str, Any]]:
    fences = parse_handoff_fences(text)
    findings: list[dict[str, Any]] = []
    groups = ("可以改", "可以新建", "不许动")
    for index, left in enumerate(groups):
        for right in groups[index + 1:]:
            for a in fences[left]:
                for b in fences[right]:
                    normalized_a = normalized_fence_path(a, cwd)
                    normalized_b = normalized_fence_path(b, cwd)
                    if paths_intersect(normalized_a, normalized_b):
                        findings.append({"rule": "fence_overlap", "left": a, "right": b})
    if TMP_PATH_RE.search(text):
        findings.append({"rule": "tmp_path"})
    if not fences["环境"]:
        findings.append({"rule": "env_block_missing"})
    forbidden_text = " ".join(fences["不许动"]).lower()
    if "未跟踪" in forbidden_text or "untracked" in forbidden_text:
        try:
            top = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            proc = subprocess.run(
                ["git", "-C", top.stdout.strip(), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                capture_output=True, text=True, check=False, timeout=10,
            ) if top.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc and proc.returncode == 0:
            untracked = []
            repo_root = Path(top.stdout.strip()).resolve()
            cwd_path = Path(cwd).resolve()
            for line in proc.stdout.split("\0"):
                if line.startswith("?? "):
                    absolute = repo_root / line[3:]
                    try:
                        untracked.append(absolute.resolve().relative_to(cwd_path).as_posix())
                    except ValueError:
                        continue
            for allowed in fences["可以改"] + fences["可以新建"]:
                allowed = normalized_fence_path(allowed, cwd)
                matches = [path for path in untracked if paths_intersect(allowed, path)]
                if matches:
                    findings.append({
                        "rule": "untracked_conflict", "path": allowed, "untracked": matches
                    })
    return findings


def parse_report_path(text: str, cwd: str) -> str:
    match = re.search(r"^\s*(?:\[报告\]|\[report\]|report\s*:)\s*(\S.*?)\s*$", text, re.I | re.M)
    if not match:
        return ""
    value = match.group(1).strip().strip("`").rstrip("；;")
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else Path(cwd) / path)


def snapshot_round(
    pp: dict[str, Path], cwd: str, round_id: str, revision: int, text: str
) -> tuple[dict[str, Any] | None, list[str]]:
    fences = parse_handoff_fences(text)
    requested = fences["可以改"] + fences["只读输入"] + fences["可以新建"]
    warnings: list[str] = []
    if not requested:
        return None, ["handoff has no snapshot fence paths"]
    root = pp["snapshots"] / f"{round_id}-r{revision}"
    root.mkdir(parents=True, exist_ok=True)
    chmod_private(root, DIR_MODE)
    records: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    cwd_path = Path(cwd)
    for raw in requested:
        rel = normalized_fence_path(raw, cwd)
        candidate = (cwd_path / rel).resolve()
        try:
            relative = candidate.relative_to(cwd_path)
        except ValueError:
            warnings.append(f"snapshot path outside cwd skipped: {raw}")
            continue
        if candidate.is_dir():
            files = sorted(path for path in candidate.rglob("*") if path.is_file())
            if not files:
                records.setdefault(rel, {"path": rel, "sha256": "", "size": 0, "mode": 0, "exists": True, "directory": True})
        elif candidate.exists():
            files = [candidate]
        else:
            records.setdefault(rel, {"path": rel, "sha256": "", "size": 0, "mode": 0, "exists": False})
            continue
        for source in files:
            file_rel = source.relative_to(cwd_path).as_posix()
            content_hash = file_sha256(source)
            size = source.stat().st_size
            record = {
                "path": file_rel, "sha256": content_hash, "size": size,
                "mode": source.stat().st_mode & 0o7777, "exists": True,
            }
            records[file_rel] = record
            if size > SNAPSHOT_COPY_LIMIT:
                record["copied"] = False
                skipped.append({"path": file_rel, "reason": "size_exceeds_copy_limit", "size": size})
            else:
                destination = root / "files" / file_rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                record["copied"] = True
    manifest = root / "manifest.json"
    atomic_text(manifest, json.dumps(list(records.values()), indent=2, sort_keys=True) + "\n")
    return {
        "manifest": str(manifest), "files": sorted(records), "skipped": skipped,
        "roots": [normalized_fence_path(value, cwd) for value in requested],
    }, warnings
