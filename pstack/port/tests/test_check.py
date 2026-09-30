"""check.py passes on the repo and catches each kind of breakage it exists for."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))


def copy_repo(dest):
    ignore = shutil.ignore_patterns("node_modules", "__pycache__", ".DS_Store")
    for name in ("pstack", "cursor-team-kit"):
        shutil.copytree(os.path.join(ROOT, name), os.path.join(dest, name), ignore=ignore, symlinks=True)
    shutil.copy(os.path.join(ROOT, "README.md"), dest)


def edit(path, old, new):
    with open(path) as f:
        text = f.read()
    assert old in text, f"{old!r} not in {path}"
    with open(path, "w") as f:
        f.write(text.replace(old, new, 1))


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        copy_repo(self.tmp)
        self.skills = os.path.join(self.tmp, "pstack", "skills")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_check(self):
        proc = subprocess.run(["python3", os.path.join(self.tmp, "pstack", "port", "check.py")], capture_output=True, text=True)
        return proc.returncode, proc.stdout

    def assertProblem(self, pattern):
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, re.compile(r"^problem: .*" + pattern, re.M))

    def test_the_repo_passes(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_an_unported_skill_fails(self):
        os.makedirs(os.path.join(self.skills, "unported"))
        with open(os.path.join(self.skills, "unported", "SKILL.md"), "w") as f:
            f.write("---\nname: unported\ndescription: New upstream skill.\n---\n\n# Unported\n")
        self.assertProblem(r"pstack/skills/unported is not in SKILLS")

    def test_a_missing_pointer_fails(self):
        edit(os.path.join(self.skills, "how", "SKILL.md"), "Outside Cursor, first read", "Before anything, read")
        self.assertProblem(r"how/SKILL\.md uses Cursor terms .* no pstack-harness pointer")

    def test_a_name_that_does_not_match_its_folder_fails(self):
        edit(os.path.join(self.skills, "how", "SKILL.md"), "name: how", "name: How")
        self.assertProblem(r"name is 'How', but the folder is 'how'")

    def test_a_user_only_skill_without_a_codex_policy_fails(self):
        os.remove(os.path.join(self.skills, "arena", "agents", "openai.yaml"))
        self.assertProblem(r"arena/SKILL\.md is user-only but has no agents/openai\.yaml")

    def test_a_broken_reference_fails(self):
        edit(os.path.join(self.skills, "how", "SKILL.md"), "# How", "# How\n\nSee `references/missing.md`.")
        self.assertProblem(r"how/SKILL\.md refers to `references/missing\.md`, which does not exist")

    def test_a_skill_missing_from_the_catalog_fails(self):
        edit(os.path.join(self.tmp, "pstack", "README.md"), "| Understand | `how` |", "| Understand | `howx` |")
        self.assertProblem(r"does not list how")

    def test_a_cursor_term_the_harness_stops_mapping_fails(self):
        edit(os.path.join(self.skills, "pstack-harness", "SKILL.md"), "| `AskQuestion` |", "| `AskUser` |")
        self.assertProblem(r"pstack-harness does not map AskQuestion")


if __name__ == "__main__":
    unittest.main()
