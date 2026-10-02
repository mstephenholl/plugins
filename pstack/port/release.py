#!/usr/bin/env python3
"""Cut a pstack release from main: a vX.Y.Z tag and a GitHub release, created in one API call.

  release.py [--target REF] [--publish] [--repo OWNER/REPO]
  release.py --check-pr [--target BASE]

It reads main's first-parent history from the newest plain vX.Y.Z tag up to
--target (default HEAD) and plans the next version from the pull requests
that history merged. Upstream commits that a sync brings in sit on a second
parent, so they never count. Without --publish it prints the release it would
cut and stops. The version rules are in the Releases section of
pstack/port/README.md.

--publish creates the release with `gh`, which also creates the tag. Only the
release job in pstack-ci.yml passes it. It requires --repo, which defaults to
$GITHUB_REPOSITORY. It does nothing when the newest tag already covers
--target.

--check-pr reads a pull request from $PR_TITLE, $PR_BODY, and $PR_NUMBER, never
from the command line, so a workflow passes untrusted text only through env.
It reads the pull request the way the merge commit will, a subject of
"<title> (#N)" and a body of <body>, and exits 1 and lists every problem the
merge would cause: a title that is not Conventional Commits, a Release-As
value that is not a successor of the newest plain tag, or a marker that makes
GitHub skip CI, so that no release runs. When there are none, it prints the
level the pull request asks for and the release it would join if merged now.
--target is the base commit the pull request merges into, so that release also
counts what the base has not released yet. The pstack PR workflow runs it.

A v* tag that is not plain vX.Y.Z is skipped with a warning, because
`pstack update` and the bootstrap ignore it too. The decision, any warnings,
and the notes go to stdout and to $GITHUB_STEP_SUMMARY when set. Under GitHub
Actions each warning is also an annotation on the run, with %, CR, and LF
escaped as the runner requires.

It reads the local tags, so fetch them first. Exits 0 on a release, a preview,
or when there is nothing to release, and 1 on any error. Needs git, and gh to
publish.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from enum import IntEnum
from typing import NamedTuple, Sequence

VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
PR_MERGE = re.compile(r"Merge pull request #([0-9]+) from \S+")
PR_SUFFIX = re.compile(r"\s+\(#([0-9]+)\)$")
HEADER = re.compile(r"(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()]+)\))?(?P<bang>!)?: (?P<description>\S.*)")
BREAKING = re.compile(r"^BREAKING[ -]CHANGE:", re.M)
# Git trailer keys are case-insensitive. BREAKING CHANGE is not, so prose that says it in lowercase stays prose.
RELEASE_AS = re.compile(r"^Release-As:[ \t]*(\S.*?)[ \t]*$", re.M | re.I)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
PR_TYPES = ("feat", "fix", "docs", "refactor", "test", "chore", "perf", "ci", "build", "style", "revert")
SKIP_CI = re.compile(r"\[(?:skip ci|ci skip|no ci|skip actions|actions skip)\]|^skip-checks:[ \t]*true[ \t]*$", re.M | re.I)


class Level(IntEnum):
    PATCH = 0
    MINOR = 1
    MAJOR = 2


class Version(NamedTuple):
    major: int
    minor: int
    patch: int

    @staticmethod
    def parse(text: str) -> Version:
        match = VERSION.fullmatch(text)
        if not match:
            raise ValueError(f"not a plain X.Y.Z version: {text!r}")
        return Version(*map(int, match.groups()))

    @property
    def tag(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"

    def bump(self, level: Level) -> Version:
        if level == Level.MAJOR:
            return Version(self.major + 1, 0, 0)
        if level == Level.MINOR:
            return Version(self.major, self.minor + 1, 0)
        return Version(self.major, self.minor, self.patch + 1)

    def successors(self) -> tuple[Version, Version, Version]:
        return tuple(self.bump(level) for level in Level)


class Commit(NamedTuple):
    sha: str
    subject: str
    body: str


class Change(NamedTuple):
    sha: str
    pr: int | None
    type: str | None
    scope: str | None
    description: str
    breaking: bool
    release_as: tuple[str, ...]


class Release(NamedTuple):
    version: Version
    previous: Version
    target: str
    changes: tuple[Change, ...]
    warnings: tuple[str, ...]


class Tag(NamedTuple):
    version: Version
    commit: str


class Kind(NamedTuple):
    name: str
    heading: str
    level: Level


class Outcome(NamedTuple):
    decision: str
    warnings: list[str]
    notes: str | None


class PrCheck(NamedTuple):
    errors: tuple[str, ...]
    change: Change
    level: Level
    release: Release


class ReleaseError(Exception):
    pass


def outside_fences(text: str) -> str:
    kept, fence = [], None
    for line in text.splitlines():
        if fence is None:
            if match := FENCE.match(line):
                fence = match.group(1)
            else:
                kept.append(line)
        else:
            stripped = line.strip()
            if len(stripped) >= len(fence) and not stripped.strip(fence[0]):
                fence = None
    return "\n".join(kept)


def parse_change(commit: Commit) -> Change:
    title, body, pr = commit.subject, commit.body, None
    merge = PR_MERGE.fullmatch(commit.subject)
    if merge:
        pr = int(merge.group(1))
        first, _, rest = commit.body.strip().partition("\n")
        if first:
            title, body = first.strip(), rest
    elif suffix := PR_SUFFIX.search(title):
        pr, title = int(suffix.group(1)), title[:suffix.start()]

    header = HEADER.fullmatch(title)
    directives = outside_fences(body)
    return Change(
        sha=commit.sha,
        pr=pr,
        type=header["type"].lower() if header else None,
        scope=header["scope"] if header else None,
        description=header["description"] if header else title,
        breaking=bool(header and header["bang"]) or bool(BREAKING.search(directives)),
        release_as=tuple(RELEASE_AS.findall(directives)),
    )


KINDS = (
    Kind("breaking", "Breaking changes", Level.MAJOR),
    Kind("feat", "Features", Level.MINOR),
    Kind("fix", "Fixes", Level.PATCH),
    Kind("other", "Other changes", Level.PATCH),
)
PRE_1_0_CEILING = Level.MINOR


def kind_of(change: Change) -> Kind:
    name = "breaking" if change.breaking else change.type if change.type in ("feat", "fix") else "other"
    return next(kind for kind in KINDS if kind.name == name)


def title_level(change: Change, previous: Version) -> Level:
    level = kind_of(change).level
    return min(level, PRE_1_0_CEILING) if previous.major == 0 else level


def successor_tags(previous: Version) -> str:
    return ", ".join(v.tag for v in previous.successors())


def release_as_levels(change: Change, successors: tuple[Version, ...]) -> tuple[list[Level], list[str]]:
    levels, rejected = [], []
    for value in change.release_as:
        try:
            levels.append(Level(successors.index(Version.parse(value))))
        except ValueError:
            rejected.append(value)
    return levels, rejected


def change_level(change: Change, previous: Version) -> tuple[Level, list[str]]:
    asked, rejected = release_as_levels(change, previous.successors())
    return (max(asked) if asked else title_level(change, previous)), rejected


def plan_release(previous: Version, target: str, commits: Sequence[Commit]) -> Release | None:
    if not commits:
        return None
    changes = tuple(parse_change(c) for c in commits)
    levels, warnings = [], []
    for change in changes:
        level, rejected = change_level(change, previous)
        levels.append(level)
        ref = f"#{change.pr}" if change.pr is not None else change.sha[:12]
        warnings += (f"{ref}: ignored Release-As {value}, which is not one of {successor_tags(previous)}" for value in rejected)
    return Release(previous.bump(max(levels)), previous, target, changes, tuple(warnings))


def check_pr(title: str, body: str, number: int, previous: Version, base: str, unreleased: Sequence[Commit]) -> PrCheck:
    subject = f"{title} (#{number})"
    merge = Commit("0" * 40, subject, body)
    change = parse_change(merge)
    level, rejected = change_level(change, previous)
    errors = [f"Release-As {value} is not one of {successor_tags(previous)}" for value in rejected]
    if change.type not in PR_TYPES:
        errors.insert(0, f'title "{title}" is not Conventional Commits. Expected <type>[(<scope>)]: <description>, where type is one of {", ".join(PR_TYPES)}.')
    message = "\n".join(f"{subject}\n{body}".splitlines())
    markers = dict.fromkeys(match.group(0).lower() for match in SKIP_CI.finditer(message))
    errors += (f'"{marker}" in the title or body would make the merge commit skip CI, so no release would run' for marker in markers)
    release = plan_release(previous, base, [merge, *unreleased])
    assert release, "the merge commit is itself a change to release"
    return PrCheck(tuple(errors), change, level, release)


def note_line(change: Change) -> str:
    scope = f"**{change.scope}:** " if change.scope else ""
    pr = f" (#{change.pr})" if change.pr is not None else ""
    return f"- {scope}{change.description}{pr}"


def notes(release: Release, repo: str) -> str:
    by_kind = {kind: [] for kind in KINDS}
    for change in release.changes:
        by_kind[kind_of(change)].append(change)
    blocks = [f"## {kind.heading}\n\n" + "\n".join(note_line(c) for c in changes) for kind, changes in by_kind.items() if changes]
    blocks.append(f"Update with `pstack update`. Full changes: https://github.com/{repo}/compare/{release.previous.tag}...{release.version.tag}")
    return "\n\n".join(blocks)


def run(*cmd: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    except OSError as e:
        raise ReleaseError(f"{cmd[0]} could not run: {e}")


def git(*args: str) -> str:
    proc = run("git", *args)
    if proc.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def is_ancestor(ancestor: str, descendant: str) -> bool:
    proc = run("git", "merge-base", "--is-ancestor", ancestor, descendant)
    if proc.returncode > 1:
        raise ReleaseError(f"git merge-base --is-ancestor failed:\n{proc.stderr.strip()}")
    return proc.returncode == 0


def newest_tag() -> tuple[Tag, list[str]]:
    tags, warnings = [], []
    for line in git("for-each-ref", "--format=%(refname:strip=2) %(objectname) %(*objectname)", "refs/tags/v*").splitlines():
        name, tag_object, peeled = line.split(" ")
        try:
            tags.append(Tag(Version.parse(name), peeled or tag_object))
        except ValueError:
            warnings.append(f"skipped tag {name}, which is not plain vX.Y.Z. pstack update and the bootstrap ignore it too.")
    if not tags:
        raise ReleaseError("no plain vX.Y.Z tag exists to release after")
    return max(tags), warnings


def history(tag: Tag, target: str) -> list[Commit]:
    if is_ancestor(target, tag.commit):
        return []
    if not is_ancestor(tag.commit, target):
        raise ReleaseError(
            f"the newest tag, {tag.version.tag}, is at {tag.commit[:12]}, which is not in the history of {target[:12]}. "
            'If it was pushed by mistake, "Repository settings" in pstack/port/README.md says how to remove it.'
        )
    out = git("log", "--first-parent", "--format=%H%x1f%s%x1f%b%x1e", f"{tag.commit}..{target}")
    return [Commit(*record.strip("\n").split("\x1f")) for record in out.split("\x1e") if record.strip()]


def publish(repo: str, release: Release, body: str) -> None:
    payload = {
        "tag_name": release.version.tag,
        "target_commitish": release.target,
        "name": f"pstack {release.version.tag}",
        "body": body,
        "make_latest": "true",
    }
    proc = run("gh", "api", "-X", "POST", f"repos/{repo}/releases", "--input", "-", stdin=json.dumps(payload))
    if proc.returncode != 0:
        raise ReleaseError(f"gh could not create the release:\n{(proc.stderr or proc.stdout).strip()}")


def commit_of(ref: str) -> str:
    return git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def counted(changes: int) -> str:
    return f"{changes} change{'s' if changes != 1 else ''}"


def cut(args: argparse.Namespace) -> Outcome:
    if args.publish and not args.repo:
        raise ReleaseError("--repo OWNER/REPO is required with --publish")
    target = commit_of(args.target)
    tag, warnings = newest_tag()
    release = plan_release(tag.version, target, history(tag, target))
    if release is None:
        return Outcome(f"nothing to release: {target[:12]} is covered by {tag.version.tag}", warnings, None)

    body = notes(release, args.repo or "OWNER/REPO")
    if args.publish:
        publish(args.repo, release, body)
    decision = f"{'released' if args.publish else 'would release'} {release.version.tag} at {target[:12]} after {tag.version.tag} ({counted(len(release.changes))})"
    return Outcome(decision, [*warnings, *release.warnings], body)


def check_pr_from_env(base_ref: str) -> PrCheck:
    title, number = os.environ.get("PR_TITLE"), os.environ.get("PR_NUMBER", "")
    if title is None or not re.fullmatch(r"[0-9]+", number):
        raise ReleaseError("--check-pr needs PR_TITLE and a numeric PR_NUMBER in the environment, and PR_BODY when the pull request has a body")
    base = commit_of(base_ref)
    tag, _ = newest_tag()
    return check_pr(title, os.environ.get("PR_BODY", ""), int(number), tag.version, base, history(tag, base))


def check_report(check: PrCheck) -> str:
    if check.errors:
        return "".join(f"error: {e}\n" for e in check.errors)
    release = check.release
    lines = [
        f"this pull request asks for a {check.level.name.lower()} release",
        f"merged now, it would release {release.version.tag} after {release.previous.tag} ({counted(len(release.changes))})",
        f"notes section: {kind_of(check.change).heading}",
        f"notes line: {note_line(check.change)}",
    ]
    if check.change.breaking:
        lines.append("breaking: yes")
    return "\n".join(lines) + "\n"


def annotation(level: str, message: str) -> str:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{level} title=release.py::{escaped}"


def emit(report: str, annotations: Sequence[str]) -> None:
    print(report, end="")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for line in annotations:
            print(line)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(report)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="HEAD", help="the commit to release, or with --check-pr the base the pull request merges into (default: HEAD)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", help="create the release (default: print what would be released)")
    mode.add_argument("--check-pr", action="store_true", help="check the pull request in $PR_TITLE, $PR_BODY, and $PR_NUMBER, and print the release it asks for")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/REPO to release in, required with --publish (default: $GITHUB_REPOSITORY)")
    args = parser.parse_args(argv)
    try:
        if args.check_pr:
            check = check_pr_from_env(args.target)
            emit(check_report(check), [annotation("error", e) for e in check.errors])
            return 1 if check.errors else 0
        outcome = cut(args)
    except ReleaseError as e:
        print(f"release.py: {e}", file=sys.stderr)
        return 1
    report = "\n".join([outcome.decision, *(f"warning: {w}" for w in outcome.warnings)]) + "\n" + (f"\n{outcome.notes}\n" if outcome.notes else "")
    emit(report, [annotation("warning", w) for w in outcome.warnings])
    return 0


if __name__ == "__main__":
    sys.exit(main())
