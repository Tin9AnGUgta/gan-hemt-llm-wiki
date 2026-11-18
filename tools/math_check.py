"""Conservative Markdown math audit. Rendering is a separate, required check."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re


def audit(text):
    # Preserve offsets while ignoring fenced and inline code examples.
    clean = re.sub(r"(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$", lambda m: " " * len(m[0]), text)
    clean = re.sub(r"(`+)([^`]*?)\1", lambda m: " " * len(m[0]), clean)
    issues, segments = [], []
    def issue(code, offset):
        issues.append({"code": code, "line": text.count("\n", 0, offset) + 1})
    if re.search(r"\\[([]", clean):
        issue("legacy_delimiter_review", re.search(r"\\[([]", clean).start())
    tokens = []
    # A dollar is escaped only when preceded by an odd run of backslashes.
    for m in re.finditer(r"\$\$|\$", clean):
        prefix = clean[:m.start()]
        run = len(prefix) - len(prefix.rstrip("\\"))
        if run % 2 == 0:
            tokens.append(m)
    pos = 0
    while pos < len(tokens):
        start = tokens[pos]
        if pos + 1 >= len(tokens) or tokens[pos + 1][0] != start[0]:
            issue("unpaired_delimiter", start.start())
            pos += 1
            continue
        end = tokens[pos + 1]
        tex = clean[start.end():end.start()]
        kind = "display" if start[0] == "$$" else "inline"
        line = text.count("\n", 0, start.start()) + 1
        segments.append({"kind": kind, "tex": tex.strip(), "start": start.start(), "end": end.end(), "line": line})
        if kind == "inline" and "\n" in tex:
            issue("multiline_inline_math", start.start())
        if kind == "display":
            for token in (start, end):
                ls, le = clean.rfind("\n", 0, token.start()) + 1, clean.find("\n", token.end())
                if clean[ls:le if le >= 0 else len(clean)].strip() != "$$":
                    issue("display_delimiter_not_own_line", token.start())
        # Two slashes before a command letter are suspicious; legitimate row breaks
        # followed by whitespace are left to the renderer.
        if re.search(r"\\\\[A-Za-z]", tex):
            issue("double_escaped_command_review", start.start())
        depth = 0
        for brace in re.finditer(r"(?<!\\)[{}]", tex):
            depth += 1 if brace[0] == "{" else -1
            if depth < 0:
                break
        if depth != 0:
            issue("unbalanced_braces", start.start())
        ls, le = clean.rfind("\n", 0, start.start()) + 1, clean.find("\n", end.end())
        line_text = clean[ls:le if le >= 0 else len(clean)]
        if line_text.lstrip().startswith("|"):
            issue("math_in_table_review", start.start())
        if re.search(r"\[[^\]]+\]\([^)]*\)", tex):
            issue("markdown_link_collision", start.start())
        pos += 2
    return {"inline": sum(x["kind"] == "inline" for x in segments),
            "display": sum(x["kind"] == "display" for x in segments),
            "issues": issues, "segments": segments,
            "rendering": "not_tested_by_static_audit"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = []
    for path in args.paths:
        data = path.read_bytes()
        reports.append({"file": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(), **audit(data.decode("utf-8"))})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(reports, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps([{k: v for k, v in r.items() if k not in {"segments", "file"}} for r in reports], ensure_ascii=False))


if __name__ == "__main__":
    main()
