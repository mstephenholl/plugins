#!/usr/bin/env python3
"""Merge upstream cursor/plugins into the claude-codex port and triage the result.

  merge-upstream.py [--ref origin/main] [--repo DIR] [--no-fetch]

1. Fetches origin and merges REF, or picks up a merge already in progress.
2. Resolves each conflict where our only change to the file is the
   pstack-harness pointer line: it takes upstream's version and puts the
   pointer back under the title. Every other conflict is listed for a person.
3. Reports what needs porting: new upstream skills (with their Cursor
   terms), ported skills that disappeared, and Cursor terms that upstream
   newly added to skills already ported.

It never commits. Finish the merge yourself, then run port/install.sh and
port/smoke/smoke.py.
"""

import argparse
import os
import re
import subprocess
import sys

POINTER = re.compile(r"^Outside Cursor, first read `~/\.agents/pstack/skills/pstack-harness/SKILL\.md`.*$", re.M)
CURSOR_TERMS = re.compile(
    r"\bTask\b|AskQuestion|\.cursor/|pstack-models\.mdc|grok-|gpt-5\.6|claude-opus-5-5-max|agent-transcripts"
    r"|cursor-team-kit|create-skill|environment: \"cloud\"|cloud_base_branch|Cursor's built-in"
)


def git(repo, *args, check=True):
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{proc.stderr}")
    return proc.stdout


def show(repo, spec):
    proc = subprocess.run(["git", "-C", repo, "show", spec], capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def strip_pointer(text):
    """The text with our pointer paragraph removed, plus that paragraph."""
    match = POINTER.search(text)
    if not match:
        return text, None
    start, end = match.start(), match.end()
    if text[end:end + 1] == "\n":
        end += 1
    if text[start - 2:start] == "\n\n":
        start -= 1
    return text[:start] + text[end:], match.group(0)


def insert_pointer(text, pointer):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[: i + 1] + ["", pointer] + lines[i + 1 :])
    return None


def resolve_pointer_conflicts(repo):
    conflicted = git(repo, "diff", "--name-only", "--diff-filter=U").split()
    resolved, manual = [], []
    for path in conflicted:
        base, ours, theirs = (show(repo, f":{n}:{path}") for n in (1, 2, 3))
        if None not in (base, ours, theirs):
            without, pointer = strip_pointer(ours)
            if pointer and without == base:
                merged = insert_pointer(theirs, pointer)
                if merged is not None:
                    with open(os.path.join(repo, path), "w") as f:
                        f.write(merged)
                    git(repo, "add", path)
                    resolved.append(path)
                    continue
        manual.append(path)
    return resolved, manual


def installed_skills(repo):
    with open(os.path.join(repo, "pstack", "port", "install.sh")) as f:
        body = f.read()
    block = re.search(r"^SKILLS=\((.*?)^\)", body, re.S | re.M).group(1)
    return set(block.split())


def triage(repo, base, incoming):
    ported = installed_skills(repo)
    before = set(git(repo, "ls-tree", "--name-only", f"{base}:pstack/skills").split())
    upstream = set(git(repo, "ls-tree", "--name-only", f"{incoming}:pstack/skills").split())
    report = []

    for skill in sorted(upstream - before):
        text = show(repo, f"{incoming}:pstack/skills/{skill}/SKILL.md") or ""
        terms = sorted(set(CURSOR_TERMS.findall(text)))
        verdict = f"Cursor terms: {', '.join(terms)}" if terms else "no Cursor terms, likely installable as-is"
        report.append(f"new upstream skill {skill}: {verdict}. Port it, then add it to SKILLS in port/install.sh.")

    for skill in sorted((before - upstream) & ported):
        report.append(f"ported skill {skill} is gone upstream. Remove it from SKILLS in port/install.sh.")

    diff = git(repo, "diff", base, incoming, "--", "pstack/skills")
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and current:
            skill = current.split("/")[2] if current.count("/") >= 3 else None
            if skill in ported and CURSOR_TERMS.search(line):
                report.append(f"upstream added a Cursor term to {current}: {line[1:].strip()[:160]}")
    return report


def merge_head(repo):
    return subprocess.run(["git", "-C", repo, "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True).returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()
    repo = os.path.realpath(args.repo)

    if merge_head(repo):
        incoming = "MERGE_HEAD"
        print("picking up the merge already in progress")
    else:
        if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
            sys.exit("the working tree has uncommitted changes; commit or stash them first")
        if not args.no_fetch:
            git(repo, "fetch", "-q", "origin")
        incoming = args.ref
        if not git(repo, "rev-list", f"HEAD..{incoming}").strip():
            print(f"already up to date with {incoming}")
            return
        proc = subprocess.run(["git", "-C", repo, "merge", "--no-commit", "--no-ff", incoming], capture_output=True, text=True)
        print(proc.stdout.strip() or proc.stderr.strip())

    base = git(repo, "merge-base", "HEAD", incoming).strip()
    resolved, manual = resolve_pointer_conflicts(repo)
    for path in resolved:
        print(f"resolved {path}: took upstream's text and restored the pstack-harness pointer")
    for path in manual:
        print(f"CONFLICT needs a person: {path}")

    report = triage(repo, base, incoming)
    if report:
        print("\nto port:")
        for item in report:
            print(f"- {item}")

    print("\nnext: " + ("resolve the conflicts above, then " if manual else "") + "review with `git diff --cached`, commit the merge, run port/install.sh, and run port/smoke/smoke.py.")
    sys.exit(1 if manual else 0)


if __name__ == "__main__":
    main()
