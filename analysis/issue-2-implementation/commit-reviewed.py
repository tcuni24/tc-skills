#!/usr/bin/env python3
"""Commit the reviewed issue-2 snapshot from a terminal with Git write access."""

import hashlib
import json
from pathlib import Path
import subprocess


def main():
    evidence = Path(__file__).resolve().parent
    root = evidence.parent.parent

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    if git("branch", "--show-current") != "feat/herdr-pair-context-budget":
        raise SystemExit("Stopped: expected feat/herdr-pair-context-budget branch.")
    if git("rev-parse", "HEAD") != "f5e81267d8354bafd87fd2c6e28db9ea4ddcda26":
        raise SystemExit("Stopped: HEAD changed since review; inspect before committing.")
    if git("diff", "--cached", "--name-only"):
        raise SystemExit("Stopped: index contains staged changes; preserve and inspect them.")

    snapshot = json.loads((evidence / "snapshot.json").read_text())
    expected = "7074ed9bd2ab06e423e7c24df5c8b1f2140cac73163edb2d0251875b05e9ae98"
    digest = hashlib.sha256(json.dumps(snapshot["files"], sort_keys=True).encode()).hexdigest()
    if snapshot["snapshot_id"] != expected or digest != expected:
        raise SystemExit("Stopped: review manifest changed.")
    for name, expected_hash in snapshot["files"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected_hash:
            raise SystemExit(f"Stopped: reviewed file changed: {name}")

    git("diff", "--check")
    git("add", "--", *snapshot["files"])
    git("diff", "--cached", "--check")
    print(git("commit", "--file", str(evidence / "COMMIT_MESSAGE.txt")))
    print("Committed:", git("rev-parse", "HEAD"))


if __name__ == "__main__":
    main()
