"""Measure identifiable rule text in a synthetic Web replay's public CLI events."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from scripts.report_context_budget import summarize_reads


MARKER = re.compile(r"(?m)^<!-- (documents/rulers/[^>\n]+\.md) -->\n")
FIRST = "<!-- documents/rulers/core/HARD_CONSTRAINTS.md -->\n"
PROFILE = "documents/rulers/PROJECT_PROFILE.md"


def rule_output(output, files, profile_end):
    """Match actual output spans, excluding other results in a combined shell call."""
    reads = []
    while FIRST in output:
        start = output.index(FIRST)
        profile = output.find(f"<!-- {PROFILE} -->\n", start)
        finish = output.find(profile_end, profile) if profile >= 0 else -1
        if finish < 0:
            raise ValueError("Incomplete load output. Inspect the original event before counting.")
        end = finish + len(profile_end)
        if output[end:end + 1] == "\n":
            end += 1
        payload = output[start:end]
        markers = list(MARKER.finditer(payload))
        boundaries = [item.start() for item in markers[1:]] + [len(payload)]
        for item, stop in zip(markers, boundaries):
            if item.group(1) not in files:
                raise ValueError(f"Unknown rule in load output: {item.group(1)}")
            reads.append(("load", item.group(1), len(payload[item.start():stop].encode())))
        output = output[:start] + output[end:]

    for line in output.splitlines(keepends=True):
        if '"context_schema_version"' in line:
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and "context_schema_version" in data and "load" in data:
                reads.append(("context", "Context", len(line.encode())))
                output = output.replace(line, "", 1)
    for name, content in files.items():
        while content and content in output:
            reads.append(("direct", name, len(content.encode())))
            output = output.replace(content, "", 1)
    return reads


def verified_rule_texts(target, manifest):
    def installed_input(name):
        path = Path(name)
        return (name == "AGENTS.md" or path.is_relative_to("documents/rulers")) and (
            "__pycache__" not in path.parts and path.name != ".DS_Store"
        )

    before = manifest["before"]["files"]
    after = manifest["after"]["files"]
    required = {"AGENTS.md", PROFILE, "documents/rulers/RULERS_STATE.json", "documents/rulers/scripts/validate_rulers.py"}
    expected = required | {name for name in before.keys() | after.keys() if installed_input(name)}
    seen = set()
    files = {}
    for path in [target / "AGENTS.md", *(target / "documents/rulers").rglob("*")]:
        name = path.relative_to(target).as_posix()
        if not installed_input(name):
            continue
        if path.is_symlink():
            raise ValueError(f"Replay input is a symbolic link: {name}")
        if not path.is_file():
            continue
        source = path.read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        if before.get(name) != digest or after.get(name) != digest:
            raise ValueError(f"Replay input changed: {name}")
        seen.add(name)
        if path.suffix == ".md":
            files[name] = source.decode("utf-8")
    missing = expected - seen
    if missing:
        raise ValueError("Missing replay inputs: " + ", ".join(sorted(missing)))
    return dict(sorted(files.items(), key=lambda item: -len(item[1])))


def measure(target, evidence):
    manifest = json.loads((evidence / "manifest.json").read_text())
    if manifest.get("exit_code") != 0:
        raise ValueError("Replay did not complete. Keep its failure record.")
    files = verified_rule_texts(target, manifest)
    end_heading = "## 阻塞性未决问题"
    profile_end = end_heading + files[PROFILE].split(end_heading, 1)[1].rstrip()
    log = (evidence / "events.jsonl").read_bytes()
    trace = []
    events = []
    shell_bytes = 0
    for line in log.splitlines():
        event = json.loads(line)
        item = event.get("item", {})
        if event.get("type") != "item.completed" or item.get("type") != "command_execution":
            continue
        output = item["aggregated_output"]
        shell_bytes += len(output.encode())
        reads = rule_output(output, files, profile_end)
        if reads:
            events.append({"event": item["id"], "spans": reads})
            trace.extend({"path": name, "output_bytes": count} for _, name, count in reads)
    total = summarize_reads(trace)["output_bytes"]
    first = {}
    for entry in trace:
        first.setdefault(entry["path"], entry["output_bytes"])
    first_bytes = sum(first.values())
    return {"rule_bytes": total, "first_bytes": first_bytes, "repeat_bytes": total - first_bytes,
            "shell_bytes": shell_bytes, "seconds": round(manifest["finished_at_unix"] - manifest["started_at_unix"], 1),
            "events_sha256": hashlib.sha256(log).hexdigest(), "events": events}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(measure(args.target.resolve(), args.evidence), ensure_ascii=False, indent=2))
