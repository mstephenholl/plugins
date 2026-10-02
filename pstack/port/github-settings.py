#!/usr/bin/env python3
"""Apply port/github.json to a repository, or check that the repository still matches it.

  github-settings.py [--repo OWNER/REPO] [--config PATH] [--dry-run | --check]

--repo defaults to $GITHUB_REPOSITORY and is required, so the script never
guesses which repository to change. --config defaults to the github.json next
to this script.

Without --check it applies the file in this order: the repository settings
(PATCH repos/OWNER/REPO), each endpoint with its own method and body, each
feature (PUT to turn it on, DELETE to turn it off), then each ruleset. A
ruleset is found by name and updated in place, or created when no ruleset has
that name, so running it again changes nothing. --dry-run prints each write
instead of making it. Applying needs gh authenticated as an admin of the
repository.

Before it writes anything, it refuses to require pinned actions while the
default branch still has a workflow with a `uses:` line that is not a full
commit SHA. A local `./` path and a `docker://` image are exempt. It exits 2,
names each file and line, and writes nothing, because GitHub would then reject
every run of that workflow. --dry-run applies the same guard.

--check reads every resource, compares it with the file, and writes nothing.
Every GET is judged by one rule. A 2xx is read. A 429 or a 5xx says nothing
about the repository, so the check stops and exits 2 instead of reporting
drift. A 403 or 404 is unreadable for a token that is not admin, because GitHub
hides some fields and endpoints from it, as it does from the Actions token in
the daily workflow. For an admin it is drift, with GitHub's reason, which
catches a misspelled path. Any other status is drift. A key that a 2xx response
leaves out splits the same way, unreadable or drift with "have missing". Unreadable
items are reported and never count as drift. Run it as an admin to see everything.

  Settings and endpoints  Every key in the file must equal the live value. A list
                          of plain values compares as a set, and a list of
                          objects must be equal.
  Features                A 204 means on. A 2xx with "enabled" means that value.
                          A 404 means off for an admin.
  Rulesets                A missing ruleset is drift, and so is a live ruleset
                          the file does not name. So are a changed target,
                          enforcement, conditions, or bypass_actors, and a set of
                          rule types that differs, which catches a rule added in
                          the web UI. Each rule's parameters must be a subset of
                          the live ones, because GitHub adds defaults.

The check prints one "drift:" line per difference and one summary line that
names the unreadable items, and appends both to $GITHUB_STEP_SUMMARY when it is
set. Under GitHub Actions it also emits an ::error:: annotation per drift and a
::notice:: for the unreadable items. Exits 1 on any drift, and 2 on a usage
error, a refused apply, or when gh fails. Needs gh and Python 3.9.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from enum import Enum
from typing import Any, NamedTuple, Sequence
from urllib.parse import quote

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.realpath(__file__)), "github.json")
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/(?!\.+$)[A-Za-z0-9_.-]+")
RULESET_FIELDS = ("target", "enforcement", "conditions", "bypass_actors")
RAW = "Accept: application/vnd.github.raw"
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*['\"]?([^\s'\"]+)")
PINNED = re.compile(r"[^@\s]+@[0-9a-f]{40}")


class SettingsError(Exception):
    pass


class Endpoint(NamedTuple):
    path: str
    method: str
    body: dict

    @property
    def label(self) -> str:
        return self.path or "settings"


class Config(NamedTuple):
    endpoints: tuple[Endpoint, ...]
    features: dict
    rulesets: tuple[dict, ...]


class Reply(NamedTuple):
    status: int
    body: Any
    request: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def fields(self) -> dict:
        return self.body if isinstance(self.body, dict) else {}

    @property
    def reason(self) -> str:
        detail = self.fields["errors"] if isinstance(self.fields.get("errors"), str) else self.fields.get("message")
        return f"HTTP {self.status}" + (f", {detail}" if detail else "")

    def failure(self) -> SettingsError:
        return SettingsError(f"{self.request} failed with {self.reason}")


class Read(Enum):
    OK = "ok"
    UNREADABLE = "unreadable"
    DRIFT = "drift"


def classify(reply: Reply, admin: bool) -> Read:
    if reply.ok:
        return Read.OK
    if reply.status == 429 or reply.status >= 500:
        raise reply.failure()
    if reply.status in (403, 404) and not admin:
        return Read.UNREADABLE
    return Read.DRIFT


def same(want: Any, have: Any) -> bool:
    if isinstance(want, list) and isinstance(have, list) and not any(isinstance(v, (dict, list)) for v in want + have):
        return set(want) == set(have)
    return want == have


class Findings:
    def __init__(self, admin: bool) -> None:
        self.admin = admin
        self.drift: list[str] = []
        self.unreadable: list[str] = []

    def triage(self, resource: str, reply: Reply) -> bool:
        verdict = classify(reply, self.admin)
        if verdict is Read.UNREADABLE:
            self.unreadable.append(resource)
        elif verdict is Read.DRIFT:
            self.drift.append(f"{resource}: {reply.reason}")
        return verdict is Read.OK

    def absent(self, resource: str, key: str, wanted: Any) -> None:
        if self.admin:
            self.drift.append(f"{resource} {key}: want {json.dumps(wanted)}, have missing")
        else:
            self.unreadable.append(f"{resource} {key}")

    def exists(self, resource: str, want: bool) -> None:
        self.drift.append(f"{resource} exists: want {json.dumps(want)}, have {json.dumps(not want)}")

    def compare(self, resource: str, want: dict, have: dict) -> None:
        for key, wanted in want.items():
            if key not in have:
                self.absent(resource, key, wanted)
            elif not same(wanted, have[key]):
                self.drift.append(f"{resource} {key}: want {json.dumps(wanted)}, have {json.dumps(have[key])}")


def load_config(path: str) -> Config:
    try:
        with open(path) as f:
            raw = json.load(f)
        endpoints = (Endpoint("", "PATCH", raw["settings"]), *(Endpoint(e["path"], e["method"], e["body"]) for e in raw["endpoints"]))
        return Config(endpoints, raw["features"], tuple(raw["rulesets"]))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SettingsError(f"{path} is not a usable config ({type(e).__name__}: {e})")


def repo_path(repo: str, path: str = "") -> str:
    return f"repos/{repo}/{path}" if path else f"repos/{repo}"


def api(method: str, path: str, body: Any = None, raw: bool = False) -> Reply:
    cmd = ["gh", "api", "-i", "-X", method, path]
    if raw:
        cmd += ["-H", RAW]
    if body is None:
        stdin: dict[str, Any] = {"stdin": subprocess.DEVNULL}
    else:
        cmd += ["--input", "-"]
        stdin = {"input": json.dumps(body)}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, **stdin)
    except OSError as e:
        raise SettingsError(f"gh could not run: {e}")
    head, _, text = proc.stdout.replace("\r\n", "\n").partition("\n\n")
    status = re.match(r"HTTP/\S+ ([0-9]{3})", head)
    if not status:
        raise SettingsError(f"gh api {method} {path} gave no HTTP response:\n{proc.stderr.strip()}")
    code = int(status.group(1))
    try:
        parsed = text if raw and 200 <= code < 300 else json.loads(text) if text.strip() else None
    except ValueError:
        parsed = None
    return Reply(code, parsed, f"{method} {path}")


def write(method: str, path: str, body: Any, dry_run: bool) -> None:
    if dry_run:
        print(f"dry run: {method} {path}" + ("" if body is None else f" {json.dumps(body)}"))
        return
    reply = api(method, path, body)
    if not reply.ok:
        raise reply.failure()
    print(f"{method} {path}")


def ruleset_ids(repo: str) -> tuple[Reply, dict[str, int]]:
    reply = api("GET", repo_path(repo, "rulesets?per_page=100"))
    return reply, ({rs["name"]: rs["id"] for rs in reply.body} if reply.ok else {})


def unpinned_lines(text: str) -> list[int]:
    uses = ((number, USES.match(line)) for number, line in enumerate(text.splitlines(), 1))
    return [number for number, match in uses if match and not match.group(1).startswith(("./", "docker://")) and not PINNED.fullmatch(match.group(1))]


def refuse_unpinned(repo: str, config: Config) -> None:
    if not any(e.body.get("sha_pinning_required") is True for e in config.endpoints):
        return
    live = api("GET", repo_path(repo))
    if not live.ok:
        raise live.failure()
    branch = live.fields.get("default_branch")
    if not branch:
        raise SettingsError(f"{live.request} did not name a default branch")
    ref = f"?ref={quote(branch, safe='')}"
    listing = api("GET", repo_path(repo, f"contents/.github/workflows{ref}"))
    if listing.status == 404:
        return
    if not listing.ok:
        raise listing.failure()
    unpinned = []
    for entry in listing.body:
        if entry["name"].endswith((".yml", ".yaml")):
            workflow = api("GET", repo_path(repo, f"contents/{entry['path']}{ref}"), raw=True)
            if not workflow.ok:
                raise workflow.failure()
            unpinned += [f"{entry['path']}:{number}" for number in unpinned_lines(workflow.body)]
    if unpinned:
        raise SettingsError(f"refusing to require pinned actions: {branch} still has unpinned uses in {', '.join(unpinned)}; merge the pins first")


def apply(repo: str, config: Config, dry_run: bool) -> None:
    refuse_unpinned(repo, config)
    for e in config.endpoints:
        write(e.method, repo_path(repo, e.path), e.body, dry_run)
    for name, on in config.features.items():
        write("PUT" if on else "DELETE", repo_path(repo, name), None, dry_run)
    listing, ids = ruleset_ids(repo)
    if not listing.ok:
        raise listing.failure()
    for rs in config.rulesets:
        if rs["name"] in ids:
            write("PUT", repo_path(repo, f"rulesets/{ids[rs['name']]}"), rs, dry_run)
        else:
            write("POST", repo_path(repo, "rulesets"), rs, dry_run)


def compare_ruleset(found: Findings, want: dict, have: dict) -> None:
    resource = f"ruleset {want['name']}"
    found.compare(resource, {k: want[k] for k in RULESET_FIELDS if k in want}, have)
    wanted_types = sorted({rule["type"] for rule in want["rules"]})
    if "rules" not in have:
        found.absent(resource, "rules", wanted_types)
        return
    live = {rule["type"]: rule for rule in have["rules"]}
    if wanted_types != sorted(live):
        found.drift.append(f"{resource} rules: want {json.dumps(wanted_types)}, have {json.dumps(sorted(live))}")
    for rule in want["rules"]:
        if rule["type"] in live:
            found.compare(f"{resource} {rule['type']}", rule.get("parameters", {}), live[rule["type"]].get("parameters", {}))


def check_endpoint(found: Findings, endpoint: Endpoint, reply: Reply) -> None:
    if found.triage(endpoint.label, reply):
        found.compare(endpoint.label, endpoint.body, reply.fields)


def check_feature(found: Findings, repo: str, name: str, want: bool) -> None:
    reply = api("GET", repo_path(repo, name))
    if reply.status == 204:
        have = {"enabled": True}
    elif reply.status == 404 and found.admin:
        have = {"enabled": False}
    elif found.triage(name, reply):
        have = reply.fields
    else:
        return
    found.compare(name, {"enabled": want}, have)


def check_rulesets(found: Findings, repo: str, wanted: Sequence[dict]) -> None:
    listing, ids = ruleset_ids(repo)
    if not found.triage("rulesets", listing):
        return
    for want in wanted:
        resource = f"ruleset {want['name']}"
        if want["name"] not in ids:
            found.exists(resource, True)
            continue
        reply = api("GET", repo_path(repo, f"rulesets/{ids[want['name']]}"))
        if found.triage(resource, reply):
            compare_ruleset(found, want, reply.fields)
    named = {want["name"] for want in wanted}
    for name in ids:
        if name not in named:
            found.exists(f"ruleset {name}", False)


def check(repo: str, config: Config) -> Findings:
    live = api("GET", repo_path(repo))
    if not live.ok:
        raise live.failure()
    found = Findings(admin=bool((live.fields.get("permissions") or {}).get("admin")))
    for endpoint in config.endpoints:
        check_endpoint(found, endpoint, api("GET", repo_path(repo, endpoint.path)) if endpoint.path else live)
    for name, want in config.features.items():
        check_feature(found, repo, name, want)
    check_rulesets(found, repo, config.rulesets)
    return found


def annotation(level: str, message: str) -> str:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{level}::{escaped}"


def report(repo: str, found: Findings) -> int:
    lines = [f"drift: {d}" for d in found.drift]
    unreadable = ", ".join(found.unreadable)
    summary = f"checked {repo}: {len(found.drift)} drift, {len(found.unreadable)} unreadable" + (f": {unreadable}" if unreadable else "")
    text = "\n".join([*lines, summary]) + "\n"
    print(text, end="")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for line in lines:
            print(annotation("error", line))
        if unreadable:
            print(annotation("notice", f"unreadable with this token, so not compared: {unreadable}"))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text)
    return 1 if found.drift else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/REPO to apply to or check (default: $GITHUB_REPOSITORY)")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="the settings file (default: github.json next to this script)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print each write instead of making it")
    mode.add_argument("--check", action="store_true", help="compare the repository with the file and write nothing")
    args = parser.parse_args(argv)
    if not args.repo or not REPO.fullmatch(args.repo):
        parser.error(f"--repo OWNER/REPO is required, or set GITHUB_REPOSITORY (got {args.repo!r})")
    try:
        config = load_config(args.config)
        if args.check:
            return report(args.repo, check(args.repo, config))
        apply(args.repo, config, args.dry_run)
    except SettingsError as e:
        print(f"github-settings.py: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
