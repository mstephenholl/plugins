"""github.json and github-settings.sh: the repository settings the release process depends on, and the script that applies them."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

PORT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PORT))
CI = os.path.join(ROOT, ".github", "workflows", "pstack-ci.yml")
GITHUB_JSON = os.path.join(PORT, "github.json")
GITHUB_SETTINGS = os.path.join(PORT, "github-settings.sh")
SYNC = os.path.join(PORT, "sync-upstream.sh")
ACTIONS_APP_ID = 15368


def github_config():
    with open(GITHUB_JSON) as f:
        return json.load(f)


def ruleset(name):
    return next(r for r in github_config()["rulesets"] if r["name"] == name)


def rule_types(rs):
    return [rule["type"] for rule in rs["rules"]]


def gate_name():
    with open(CI) as f:
        return re.search(r"^  gate:\n(?:    .*\n)*?    name: (.+)$", f.read(), re.M).group(1)


class RepositoryWiringTest(unittest.TestCase):
    def test_main_requires_the_ci_gate_from_github_actions(self):
        required = next(r for r in ruleset("main")["rules"] if r["type"] == "required_status_checks")
        self.assertEqual(required["parameters"]["required_status_checks"], [{"context": gate_name(), "integration_id": ACTIONS_APP_ID}])

    def test_only_merge_commits_are_allowed(self):
        settings = github_config()["settings"]
        self.assertEqual([settings["allow_merge_commit"], settings["allow_squash_merge"], settings["allow_rebase_merge"]], [True, False, False])
        pull_request = next(r for r in ruleset("main")["rules"] if r["type"] == "pull_request")
        self.assertEqual(pull_request["parameters"]["allowed_merge_methods"], ["merge"])
        for rs in github_config()["rulesets"]:
            self.assertNotIn("required_linear_history", rule_types(rs))

    def test_the_release_job_can_still_create_tags(self):
        rs = ruleset("release tags")
        self.assertEqual((rs["target"], rs["conditions"]["ref_name"]["include"]), ("tag", ["refs/tags/v*"]))
        self.assertNotIn("creation", rule_types(rs))
        self.assertTrue({"update", "deletion"} <= set(rule_types(rs)))

    def test_no_ruleset_has_a_bypass_actor(self):
        rulesets = github_config()["rulesets"]
        self.assertTrue(rulesets)
        self.assertTrue(all(not rs["bypass_actors"] for rs in rulesets))

    def test_issues_are_on_for_the_upstream_sync_to_open_one(self):
        with open(SYNC) as f:
            self.assertIn("gh issue create", f.read())
        self.assertIs(github_config()["settings"]["has_issues"], True)


@unittest.skipUnless(shutil.which("jq"), "github-settings.sh needs jq")
class GithubSettingsTest(unittest.TestCase):
    FAKE_GH = (
        "#!/bin/sh\n"
        'if [ "$2" = --paginate ]; then echo "$1 $2 $3" >> "$FAKE_GH_LOG"; echo \'[{"id":7,"name":"main"}]\'; exit 0; fi\n'
        'echo "$* $(cat)" >> "$FAKE_GH_LOG"\n'
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.log = os.path.join(self.tmp, "log")
        gh = os.path.join(self.tmp, "gh")
        with open(gh, "w") as f:
            f.write(self.FAKE_GH)
        os.chmod(gh, 0o755)
        self.env = {**os.environ, "PATH": self.tmp + os.pathsep + os.environ["PATH"], "FAKE_GH_LOG": self.log, "GH_REPO": "o/r"}
        self.env.pop("DRY_RUN", None)

    def apply(self, **env):
        proc = subprocess.run(["bash", GITHUB_SETTINGS], capture_output=True, text=True, env={**self.env, **env})
        calls = []
        if os.path.exists(self.log):
            with open(self.log) as f:
                calls = f.read().splitlines()
        return proc, calls

    def test_it_patches_the_settings_updates_the_existing_ruleset_and_creates_the_missing_one(self):
        proc, calls = self.apply()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([c.split(" ", 4)[:4] for c in calls], [
            ["api", "-X", "PATCH", "repos/o/r"],
            ["api", "--paginate", "repos/o/r/rulesets"],
            ["api", "-X", "PUT", "repos/o/r/rulesets/7"],
            ["api", "-X", "POST", "repos/o/r/rulesets"],
        ])
        self.assertEqual(json.loads(calls[0].split(" --input - ", 1)[1]), github_config()["settings"])
        self.assertEqual(json.loads(calls[2].split(" --input - ", 1)[1]), ruleset("main"))
        self.assertEqual(json.loads(calls[3].split(" --input - ", 1)[1]), ruleset("release tags"))

    def test_a_dry_run_reads_but_writes_nothing(self):
        proc, calls = self.apply(DRY_RUN="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(calls, ["api --paginate repos/o/r/rulesets"])
        self.assertEqual([line.split(" ", 3)[:3] for line in proc.stdout.splitlines()], [
            ["DRY_RUN:", "PATCH", "repos/o/r"],
            ["DRY_RUN:", "PUT", "repos/o/r/rulesets/7"],
            ["DRY_RUN:", "POST", "repos/o/r/rulesets"],
        ])

    def test_gh_repo_is_required(self):
        env = {k: v for k, v in self.env.items() if k != "GH_REPO"}
        proc = subprocess.run(["bash", GITHUB_SETTINGS], capture_output=True, text=True, env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("GH_REPO", proc.stderr)
        self.assertFalse(os.path.exists(self.log))


if __name__ == "__main__":
    unittest.main()
