"""Small, resumable Graph controller. Private evidence never belongs in the repo."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parents[1]
STATUSES = {"pending", "running", "passed", "failed", "blocked", "stale", "awaiting_review"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def equation_labels(text):
    """References and equation labels are candidates, never proof of completeness."""
    pattern = r"[（(]\s*([1-9]\d?\s*[-－–]\s*\d{1,3}(?:\.\d+)*)\s*[）)]"
    return [re.sub(r"\s+", "", match.group(1)).replace("－", "-").replace("–", "-")
            for match in re.finditer(pattern, text)]


def private_root(repo=REPO):
    root = Path(os.environ.get("GAN_WIKI_PRIVATE_ROOT", str(repo.parent / "gan-hemt-private"))).resolve()
    if root == repo.resolve() or root.is_relative_to(repo.resolve()):
        raise ValueError("Private workspace must be outside the public repository")
    return root


def graph():
    return read_json(REPO / "workflow/graph.json")


def sources():
    return read_json(REPO / "sources/catalog.json")["sources"]


def definitions():
    result = {}
    source_ids = [s["id"] for s in sources()]
    for item in graph()["nodes"]:
        for sid in source_ids if item.get("per_source") else [None]:
            name = item["id"] + (":" + sid if sid else "")
            deps = []
            for dep in item["depends"]:
                if dep.endswith(":*"):
                    deps.extend(dep[:-1] + other for other in source_ids)
                else:
                    deps.append(dep.replace("{source}", sid or ""))
            result[name] = {**item, "source_id": sid, "dependencies": deps}
    return result


def initial_state(run_id):
    return {"run_id": run_id, "started_at": now(), "graph_version": graph()["version"],
            "graph_sha256": digest(REPO / "workflow/graph.json"),
            "tool_sha256": digest(REPO / "tools/wiki.py"), "repairs": 0,
            "nodes": {name: {"status": "pending", "attempts": 0, "outputs": {}, "deps_snapshot": {}}
                      for name in definitions()}}


def current():
    root = private_root()
    pointer = root / "current.json"
    if not pointer.exists():
        raise ValueError("No run. Use: python tools/wiki.py start")
    run = root / "runs" / read_json(pointer)["run_id"]
    state = read_json(run / "state.json")
    if state["graph_sha256"] != digest(REPO / "workflow/graph.json"):
        raise ValueError("Graph definition changed. Start a new run; do not reinterpret old states")
    return run, state


def event(run, state, node, action, **fields):
    item = {"at": now(), "run_id": state["run_id"], "node": node, "action": action,
            "tool_sha256": digest(REPO / "tools/wiki.py"), **fields}
    run.mkdir(parents=True, exist_ok=True)
    with (run / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def save(run, state):
    write_json(run / "state.json", state)


def descendants(node, defs):
    affected = {node}
    changed = True
    while changed:
        changed = False
        for name, definition in defs.items():
            if name not in affected and affected.intersection(definition["dependencies"]):
                affected.add(name)
                changed = True
    return affected


def invalidate(state, node, reason):
    affected = descendants(node, definitions())
    for name in affected:
        record = state["nodes"][name]
        if record["status"] != "pending" or name == node:
            record["status"] = "stale"
            record["stale_reason"] = reason
    return affected


def refresh(run, state):
    for name, record in state["nodes"].items():
        if record["status"] != "passed":
            continue
        if any(not Path(path).is_file() or digest(path) != old_hash
               for path, old_hash in record.get("outputs", {}).items()):
            affected = invalidate(state, name, "recorded_output_changed")
            event(run, state, name, "invalidated", affected=sorted(affected), reason_code="recorded_output_changed")
    for name, definition in definitions().items():
        if definition["kind"] == "human" and state["nodes"][name]["status"] == "pending":
            if all(state["nodes"][dep]["status"] == "passed" for dep in definition["dependencies"]):
                state["nodes"][name]["status"] = "awaiting_review"
                event(run, state, name, "awaiting_user_review")
    save(run, state)


def begin(run, state, node):
    defs = definitions()
    if node not in defs:
        raise ValueError("Unknown node instance")
    record = state["nodes"][node]
    if defs[node]["kind"] == "human":
        raise ValueError("Human gates use review, after an actual user response")
    if record["status"] in {"passed", "running"}:
        raise ValueError("Node already passed or running; do not duplicate an attempt")
    missing = [dep for dep in defs[node]["dependencies"] if state["nodes"][dep]["status"] != "passed"]
    if missing:
        raise ValueError("Dependencies not passed: " + ", ".join(missing))
    policy = graph()
    if record["attempts"] >= policy["max_attempts_per_instance"]:
        record["status"] = "blocked"
        save(run, state)
        raise ValueError("Attempt limit reached")
    if record["attempts"]:
        if state["repairs"] >= policy["max_automatic_repairs_per_run"]:
            record["status"] = "blocked"
            save(run, state)
            raise ValueError("Run repair limit reached")
        state["repairs"] += 1
    record["attempts"] += 1
    record["status"] = "running"
    record["deps_snapshot"] = {dep: state["nodes"][dep].get("outputs", {}) for dep in defs[node]["dependencies"]}
    event(run, state, node, "started", attempt=record["attempts"])
    save(run, state)


def allowed_artifact(path):
    path = Path(path).resolve()
    if not (path.is_relative_to(REPO) or path.is_relative_to(private_root())):
        raise ValueError("Output artifact must be inside repository or private workspace")
    if not path.is_file():
        raise ValueError("Output artifact does not exist: " + path.name)
    return path


def fail_node(run, state, node, code):
    record = state["nodes"][node]
    if record["status"] != "running":
        raise ValueError("Only a running node may fail")
    record["status"] = "failed"
    event(run, state, node, "failed", attempt=record["attempts"], reason_code=code)
    sid = definitions()[node].get("source_id") or ""
    route = graph()["failure_routes"].get(code, "inspect_and_stop").replace("{source}", sid)
    if route == "blocked":
        record["status"] = "blocked"
    elif route in definitions() and route != node:
        affected = invalidate(state, route, "failure_route:" + code)
        event(run, state, node, "routed", target=route, affected=sorted(affected), reason_code=code)
    save(run, state)
    return route


def validate_result(node, result):
    definition = definitions()[node]
    checks = result.get("checks", {})
    for key in definition["checks"]:
        if checks.get(key) != "passed":
            raise ValueError("Required check not passed: " + key)
    if not result.get("scope") or not isinstance(result.get("open_items"), list):
        raise ValueError("Declare actual review scope and open_items")
    if not result.get("outputs"):
        raise ValueError("Actual output artifacts are required")
    if definition["id"] == "G2":
        evidence = result.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("G2 requires nonempty evidence")
        page_counts = {item["id"]: item["expected_pages"] for item in sources()}
        for claim in evidence:
            sid = claim.get("source_id")
            page = claim.get("pdf_page")
            if sid not in page_counts or not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= page_counts[sid]:
                raise ValueError("Invalid source or PDF page")
            if sid != definition["source_id"]:
                raise ValueError("Paper evidence must match node source")
            if not claim.get("claim") or claim.get("basis") not in {"paper", "external", "ai", "pending"}:
                raise ValueError("Missing claim or evidence label")
            if claim.get("status") not in {"checked", "pending"}:
                raise ValueError("Evidence status must be explicit")
            if claim.get("basis") == "external" and not claim.get("external_source"):
                raise ValueError("External evidence requires its own verified source")


def complete(run, state, node, result_path):
    if state["nodes"][node]["status"] != "running":
        raise ValueError("begin the node before recording results")
    result_path = allowed_artifact(result_path)
    result = read_json(result_path)
    validate_result(node, result)
    outputs = [allowed_artifact(path) for path in result["outputs"]] + [result_path]
    state["nodes"][node].update(status="passed", outputs={str(p): digest(p) for p in outputs},
                                scope=result["scope"], open_items_count=len(result["open_items"]))
    event(run, state, node, "passed", attempt=state["nodes"][node]["attempts"],
          output_count=len(outputs), open_items_count=len(result["open_items"]))
    save(run, state)


def automatic_result(run, state, node, outputs, scope, open_items=None):
    path = run / "results" / (node.replace(":", "-") + ".json")
    result = {"checks": {key: "passed" for key in definitions()[node]["checks"]},
              "outputs": [str(p) for p in outputs], "scope": scope, "open_items": open_items or []}
    write_json(path, result)
    complete(run, state, node, path)


def run_ingest(run, state):
    from pypdf import PdfReader
    root = private_root()
    if not (root / "inputs.json").exists():
        raise ValueError("Missing private inputs.json; nothing was ingested")
    inputs = read_json(root / "inputs.json")["sources"]
    if state["nodes"]["G0"]["status"] != "passed":
        begin(run, state, "G0")
        registered = []
        for source in sources():
            sid = source["id"]
            incoming = Path(inputs[sid]).resolve()
            if not incoming.is_file():
                raise ValueError("Source is unavailable: " + sid)
            if incoming.is_relative_to(REPO):
                raise ValueError("Raw sources must not be inside public repository")
            if incoming.read_bytes()[:5] != b"%PDF-":
                raise ValueError("Invalid PDF signature: " + sid)
            original_hash = digest(incoming)
            target = root / "raw" / (sid + ".pdf")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and digest(target) != original_hash:
                raise ValueError("Immutable source differs; use a new version, do not overwrite: " + sid)
            if not target.exists():
                shutil.copy2(incoming, target)
            reader = PdfReader(target)
            if len(reader.pages) != source["expected_pages"] or digest(target) != original_hash:
                raise ValueError("Page count or copy hash mismatch: " + sid)
            registered.append({"source_id": sid, "sha256": original_hash, "pages": len(reader.pages),
                               "raw_copy": str(target), "first_page_text_chars": len(reader.pages[0].extract_text() or "")})
        try:
            remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO, capture_output=True, text=True)
            configured = remote.returncode == 0 and "Tin9AnGUgta/gan-hemt-llm-wiki" in remote.stdout
        except FileNotFoundError:
            configured = False
        # Bundled Windows Git may not be on PATH; .git/config is authoritative for this local checkout.
        if not configured:
            git_config = REPO / ".git/config"
            configured = git_config.exists() and "Tin9AnGUgta/gan-hemt-llm-wiki" in git_config.read_text(encoding="utf-8")
        if not configured:
            raise ValueError("Repository origin not configured")
        manifest = run / "source-manifest.json"
        write_json(manifest, {"at": now(), "sources": registered, "remote_configured": True,
                              "remote_access_note": "Connector access is verified separately; local configuration alone is not a network test."})
        automatic_result(run, state, "G0", [manifest] + [Path(item["raw_copy"]) for item in registered],
                         "Source readability, immutable copy hashes, page counts and local origin configuration")
    for source in sources():
        sid = source["id"]
        node = "G1:" + sid
        if state["nodes"][node]["status"] == "passed":
            continue
        begin(run, state, node)
        reader = PdfReader(root / "raw" / (sid + ".pdf"))
        pages, candidates, flags = [], [], []
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            page_flags = ["blank_or_image_only"] if len(text.strip()) < 20 else []
            if "\ufffd" in text:
                page_flags.append("replacement_character")
            pages.append({"pdf_page": index + 1, "printed_page": None, "text": text,
                          "extraction_status": "extracted" if text.strip() else "no_text",
                          "visual_review": "pending", "flags": page_flags})
            flags.extend({"pdf_page": index + 1, "code": flag} for flag in page_flags)
            labels = equation_labels(text)
            for label in labels:
                candidates.append({"pdf_page": index + 1, "candidate_label": label, "status": "unverified"})
            if labels:
                flags.append({"pdf_page": index + 1, "code": "formula_label_requires_visual_pairing"})
        folder = root / "extracted" / sid / state["run_id"]
        page_file, equation_file = folder / "pages.json", folder / "equation-candidates.json"
        write_json(page_file, {"source_id": sid, "pages": pages})
        write_json(equation_file, {"source_id": sid, "note": "Candidates include false positives; not a complete or verified formula inventory", "candidates": candidates})
        summary = folder / "summary.json"
        write_json(summary, {"source_id": sid, "pages_registered": len(pages), "flags": flags,
                             "formula_visual_review_pages": sorted({c["pdf_page"] for c in candidates}),
                             "formula_candidate_count": len(candidates), "academic_review": "not_completed"})
        automatic_result(run, state, node, [page_file, equation_file, summary],
                         "All PDF pages registered and machine text extracted; equations and figures not visually certified", flags)
    print("G0 and five G1 instances completed. G2-G4 require actual Codex reading and evidence artifacts.")


def markdown_links(text):
    text = re.sub(r"```.*?```|`[^`\n]*`|\$\$.*?\$\$|\$(?:\\.|[^$\\])*\$", "", text, flags=re.S)
    return re.findall(r"!?\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)", text)


def public_errors(repo=REPO):
    errors = []
    manifest_path = repo / "publication/public-files.json"
    if not manifest_path.exists():
        return ["Missing publication/public-files.json"]
    manifest = read_json(manifest_path)
    listed = manifest["files"]
    actual = {p.relative_to(repo).as_posix(): p for p in repo.rglob("*")
              if p.is_file() and not any(part in {".git", "__pycache__", ".venv"} for part in p.relative_to(repo).parts)}
    allowed = set(listed) | {"publication/public-files.json"}
    for name in sorted(set(actual) - allowed):
        errors.append("Unlisted public file: " + name)
    for name, expected in listed.items():
        if name not in actual:
            errors.append("Missing listed file: " + name)
        elif digest(actual[name]) != expected["sha256"]:
            errors.append("Changed file requires manifest refresh/review: " + name)
    for name, path in actual.items():
        if path.suffix.lower() in {".pdf", ".pem", ".key", ".zip"}:
            errors.append("Forbidden public artifact: " + name)
            continue
        if path.suffix.lower() not in {".md", ".json", ".jsonl", ".py", ".txt", ".yml", ".yaml", ".html", ".css", ".js", ".svg"} and name not in {".gitignore", ".gitattributes"}:
            errors.append("Unreviewed file type: " + name)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            errors.append("Not UTF-8 text: " + name)
            continue
        if b"\r\n" in path.read_bytes():
            errors.append("Public text must use LF line endings: " + name)
        # Split the Unix prefix so the scanner does not mistake its own regex for a personal path.
        sensitive_pattern = r"[A-Za-z]:[\\/]Users[\\/]|" + "/" + "home" + "/" + r"[^/]+/|-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}"
        if re.search(sensitive_pattern, text):
            errors.append("Potential private path or secret: " + name)
        if path.suffix == ".md":
            # Ignore code examples: template placeholders are not real links.
            prose = re.sub(r"```.*?```", "", text, flags=re.S)
            for first, second in markdown_links(prose):
                target = unquote(first or second)
                if urlsplit(target).scheme or target.startswith("#"):
                    continue
                relative = target.split("#", 1)[0].split("?", 1)[0]
                resolved = (path.parent / relative).resolve()
                if not resolved.is_relative_to(repo.resolve()) or not resolved.exists():
                    errors.append("Broken or escaping link: " + name + " -> " + target)
    return errors


def update_manifest():
    existing_path = REPO / "publication/public-files.json"
    registrations = read_json(REPO / "publication/registrations.json")["files"]
    catalog = {s["id"]: s for s in sources()}
    items = {}
    for path in sorted(REPO.rglob("*")):
        rel = path.relative_to(REPO)
        name = rel.as_posix()
        if not path.is_file() or name == "publication/public-files.json" or any(p in {".git", "__pycache__", ".venv"} for p in rel.parts):
            continue
        if path.suffix.lower() in {".pdf", ".pem", ".key", ".zip"}:
            raise ValueError("Cannot allow forbidden artifact: " + name)
        registration = registrations.get(name)
        if not registration or registration.get("release") not in {"eligible_project_authored", "approved"}:
            raise ValueError("Register origin and release basis before allowing: " + name)
        if not registration.get("basis"):
            raise ValueError("Release basis is required: " + name)
        for sid in registration.get("source_ids", []):
            if catalog.get(sid, {}).get("public_content_release") != "approved":
                raise ValueError("Source content not released: " + sid)
        if path.suffix == ".md" and rel.parts[0] == "wiki" and path.name != "README.md":
            text = path.read_text(encoding="utf-8")
            if "public_release: pending" in text:
                raise ValueError("Research draft cannot enter public allowlist: " + name)
            for sid in re.findall(r"source_id:\s*(P\d{2})", text):
                if catalog.get(sid, {}).get("public_content_release") != "approved":
                    raise ValueError("Source content not released: " + sid)
        items[name] = {"sha256": digest(path), "origin": registration["origin"]}
    write_json(existing_path, {"schema_version": 1, "note": "Allowlist plus hashes; not a legal determination. Research/source permissions need separate review.", "files": items})
    print("Public allowlist contains", len(items), "files")


def export_run(run, state):
    target = REPO / "records" / state["run_id"]
    summary = {"run_id": state["run_id"], "started_at": state["started_at"], "graph_version": state["graph_version"],
               "repairs": state["repairs"], "nodes": {name: {key: record[key] for key in ("status", "attempts")}
                                                     for name, record in state["nodes"].items()}}
    write_json(target / "summary.json", summary)
    safe_events = []
    for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        safe = {key: item[key] for key in ("at", "node", "action", "attempt", "output_count", "open_items_count") if key in item}
        if "reason_code" in item:
            known = set(graph()["failure_routes"]) | {"ingest_error", "recorded_output_changed", "upstream_changed", "unspecified"}
            safe["reason_code"] = item["reason_code"] if item["reason_code"] in known else "other"
        safe_events.append(safe)
    target.mkdir(parents=True, exist_ok=True)
    (target / "events.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in safe_events), encoding="utf-8", newline="\n")
    print("Exported status-only public run summary. Refresh manifest and run check before publishing.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "ingest", "status", "begin", "record", "fail", "invalidate", "review", "check", "manifest", "export"])
    parser.add_argument("node", nargs="?")
    parser.add_argument("--result")
    parser.add_argument("--code", default="unspecified")
    parser.add_argument("--reason", default="upstream_changed")
    parser.add_argument("--decision", choices=["approve", "reject"])
    parser.add_argument("--statement")
    parser.add_argument("--reference")
    args = parser.parse_args()
    if args.command == "check":
        errors = public_errors()
        print(json.dumps({"check": "public_files_and_links", "status": "failed" if errors else "passed", "errors": errors}, ensure_ascii=False, indent=2))
        return 1 if errors else 0
    if args.command == "manifest":
        update_manifest()
        return 0
    if args.command == "start":
        root = private_root()
        run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        state = initial_state(run_id)
        run = root / "runs" / run_id
        previous = read_json(root / "current.json")["run_id"] if (root / "current.json").exists() else None
        save(run, state)
        write_json(run / "graph-snapshot.json", graph())
        write_json(root / "current.json", {"run_id": run_id})
        event(run, state, "run", "created", graph_version=state["graph_version"], predecessor_run_id=previous)
        print("Created private run", run_id)
        return 0
    run, state = current()
    refresh(run, state)
    if args.command == "ingest":
        try:
            run_ingest(run, state)
        except Exception:
            for name, record in state["nodes"].items():
                if record["status"] == "running":
                    record["status"] = "failed"
                    event(run, state, name, "failed", attempt=record["attempts"], reason_code="ingest_error")
            save(run, state)
            raise
    elif args.command == "status":
        defs = definitions()
        for name, record in state["nodes"].items():
            ready = all(state["nodes"][d]["status"] == "passed" for d in defs[name]["dependencies"])
            suffix = " ready" if ready and record["status"] in {"pending", "stale", "failed"} else ""
            print(name, record["status"], "attempts=" + str(record["attempts"]) + suffix)
    elif args.command == "begin":
        begin(run, state, args.node)
    elif args.command == "record":
        complete(run, state, args.node, args.result)
    elif args.command == "invalidate":
        affected = invalidate(state, args.node, args.reason)
        event(run, state, args.node, "invalidated", affected=sorted(affected), reason_code=args.reason)
        save(run, state)
    elif args.command == "fail":
        print("Routed to:", fail_node(run, state, args.node, args.code))
    elif args.command == "review":
        definition = definitions()[args.node]
        if definition["kind"] != "human" or not args.statement or not args.reference or not args.decision:
            raise ValueError("Human review needs a real user statement, reference and decision")
        if any(state["nodes"][dep]["status"] != "passed" for dep in definition["dependencies"]):
            raise ValueError("Review inputs are not ready")
        approval = run / "reviews" / (args.node + "-" + str(len(list((run / "reviews").glob("*.json"))) + 1) + ".json")
        write_json(approval, {"at": now(), "gate": args.node, "decision": args.decision,
                              "user_statement": args.statement, "reference": args.reference,
                              "reviewed_inputs": {dep: state["nodes"][dep]["outputs"] for dep in definition["dependencies"]}})
        state["nodes"][args.node].update(status="passed" if args.decision == "approve" else "awaiting_review",
                                         outputs={str(approval): digest(approval)})
        review_document = private_root() / "review" / (args.node + ".md")
        if review_document.exists():
            state["nodes"][args.node]["outputs"][str(review_document)] = digest(review_document)
        event(run, state, args.node, "user_approved" if args.decision == "approve" else "user_rejected")
        save(run, state)
    elif args.command == "export":
        export_run(run, state)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError) as error:
        print("STOP:", str(error), file=sys.stderr)
        raise SystemExit(1)
