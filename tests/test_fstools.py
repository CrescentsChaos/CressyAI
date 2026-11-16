"""fstools.py: the file operations behind the model's filesystem.* tools. No model, no server. Run: python -m unittest tests.test_fstools -v"""
import os
import tempfile
import unittest
from pathlib import Path

import fstools
from fstools import FsError, Workspace, glob_regex


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "src" / "deep").mkdir(parents=True)
        (self.root / "node_modules" / "pkg").mkdir(parents=True)
        (self.root / ".git").mkdir()
        (self.root / "a.py").write_text("import os\nprint('hello')\nTODO fix\n", "utf-8")
        (self.root / "src" / "b.js").write_text("const x = 1;\n// todo later\n", "utf-8")
        (self.root / "src" / "deep" / "c.py").write_text("def f():\n    return 42\n", "utf-8")
        (self.root / "node_modules" / "pkg" / "skip.js").write_text("todo in a skipped folder\n", "utf-8")
        (self.root / "pic.png").write_bytes(b"\x89PNG\0\0 todo")
        (self.root / "empty.txt").write_text("", "utf-8")
        self.ws = Workspace(self.root)

    # ---------------------------------------------------------------- confinement
    def test_paths_cannot_escape(self):
        for bad in ("../x.txt", "src/../../x.txt", "/etc/passwd"):
            with self.assertRaises(FsError, msg=bad):
                self.ws.read(bad)
            with self.assertRaises(FsError, msg=bad):
                self.ws.write(bad, "x")

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_out_of_root_is_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (Path(outside.name) / "secret.txt").write_text("secret todo", "utf-8")
        (self.root / "link.txt").symlink_to(Path(outside.name) / "secret.txt")
        with self.assertRaises(FsError):
            self.ws.read("link.txt")
        with self.assertRaises(FsError):
            self.ws.write("link.txt", "x")
        self.assertNotIn("link.txt", self.ws.glob("*.txt")["files"])
        self.assertEqual(self.ws.grep("secret")["hits"], [])

    def test_git_folder_is_never_written(self):
        with self.assertRaises(FsError):
            self.ws.write(".git/config", "x")
        with self.assertRaises(FsError):
            self.ws.edit(".git/config", "a", "b")

    # ---------------------------------------------------------------- read
    def test_read_numbers_lines_and_slices(self):
        d = self.ws.read("a.py")
        self.assertEqual((d["start"], d["end"], d["total"], d["truncated"]), (1, 3, 3, False))
        self.assertIn("    1 | import os", d["text"])
        d = self.ws.read("a.py", offset=2, limit=1)
        self.assertEqual((d["start"], d["end"], d["truncated"]), (2, 2, True))
        self.assertNotIn("import os", d["text"])

    def test_read_errors(self):
        with self.assertRaises(FsError):
            self.ws.read("nope.py")
        with self.assertRaises(FsError):
            self.ws.read("src")                       # a folder
        with self.assertRaises(FsError):
            self.ws.read("pic.png")                   # binary
        with self.assertRaises(FsError):
            self.ws.read("a.py", offset=99)
        self.assertEqual(self.ws.read("empty.txt")["total"], 0)

    def test_read_stops_at_the_character_budget(self):
        (self.root / "big.txt").write_text("\n".join("line %d %s" % (i, "x" * 80) for i in range(500)), "utf-8")
        d = self.ws.read("big.txt", limit=500, max_chars=2000)
        self.assertTrue(d["truncated"] and d["end"] < 500 and len(d["text"]) <= 2100)

    # ---------------------------------------------------------------- glob
    def test_glob_patterns(self):
        g = lambda p, **k: self.ws.glob(p, **k)["files"]
        self.assertEqual(g("*.py"), ["a.py", "src/deep/c.py"])                 # no slash: any depth
        self.assertEqual(g("src/**/*.py"), ["src/deep/c.py"])
        self.assertEqual(g("src/*.py"), [])                                    # * does not cross folders
        self.assertEqual(g("*.{js,py}"), ["a.py", "src/b.js", "src/deep/c.py"])
        self.assertEqual(g("*.py", path="src"), ["src/deep/c.py"])
        self.assertEqual(g("**/c.py"), ["src/deep/c.py"])
        self.assertEqual(g("?.py"), ["a.py", "src/deep/c.py"])                 # one character, still any depth
        self.assertEqual(g("/?.py"), ["a.py"])                                 # a leading slash anchors it to the folder searched
        self.assertEqual(g("*.js"), ["src/b.js"])                              # node_modules is skipped
        self.assertEqual(g("config"), [])                                       # .git is skipped, no such file

    def test_glob_limit_and_bad_patterns(self):
        d = self.ws.glob("*.py", limit=1)
        self.assertEqual((len(d["files"]), d["truncated"]), (1, True))
        for bad in ("", "../*.py"):
            with self.assertRaises(FsError):
                glob_regex(bad)

    # ---------------------------------------------------------------- grep
    def test_grep_regex_literal_and_case(self):
        d = self.ws.grep("todo")
        self.assertEqual([(h["file"], h["line"]) for h in d["hits"]], [("a.py", 3), ("src/b.js", 2)])   # skipped folder and binary file excluded
        self.assertEqual([h["file"] for h in self.ws.grep("todo", ignore_case=False)["hits"]], ["src/b.js"])   # only the lowercase one
        self.assertEqual([h["file"] for h in self.ws.grep("TODO", ignore_case=False)["hits"]], ["a.py"])
        self.assertEqual(len(self.ws.grep(r"def \w+\(\)")["hits"]), 1)
        self.assertEqual(len(self.ws.grep("print('hello')", regex=False)["hits"]), 1)
        self.assertEqual(len(self.ws.grep("todo", glob="*.js")["hits"]), 1)
        self.assertEqual(len(self.ws.grep("todo", path="a.py")["hits"]), 1)

    def test_grep_context_limit_and_errors(self):
        h = self.ws.grep("return 42", context=1)["hits"][0]
        self.assertEqual((h["before"], h["after"]), (["def f():"], []))
        self.assertTrue(self.ws.grep("e", limit=2)["truncated"])
        with self.assertRaises(FsError):
            self.ws.grep("(unclosed")
        with self.assertRaises(FsError):
            self.ws.grep("")
        with self.assertRaises(FsError):
            self.ws.grep("x", path="missing")

    # ---------------------------------------------------------------- write
    def test_write_creates_overwrites_and_dry_run_touches_nothing(self):
        plan = self.ws.write("new/dir/f.txt", "one\ntwo\n", dry_run=True)
        self.assertTrue(plan["created"] and plan["lines"] == 2)
        self.assertFalse((self.root / "new").exists())
        d = self.ws.write("new/dir/f.txt", "one\ntwo\n")
        self.assertEqual((self.root / "new/dir/f.txt").read_text("utf-8"), "one\ntwo\n")
        self.assertTrue(d["created"])
        self.assertEqual(list(self.root.rglob("*.cressy-tmp")), [])
        plan = self.ws.write("new/dir/f.txt", "one\nTWO\n", dry_run=True)
        self.assertFalse(plan["created"])
        self.assertIn("-two", plan["diff"])
        self.assertIn("+TWO", plan["diff"])
        self.assertTrue(self.ws.write("new/dir/f.txt", "one\ntwo\n", dry_run=True)["same"])

    def test_write_keeps_crlf_and_refuses_folders_binaries_and_huge_files(self):
        (self.root / "w.txt").write_bytes(b"a\r\nb\r\n")
        self.ws.write("w.txt", "x\ny\n")
        self.assertEqual((self.root / "w.txt").read_bytes(), b"x\r\ny\r\n")
        for p in ("src", "pic.png", ""):
            with self.assertRaises(FsError, msg=p):
                self.ws.write(p, "x")
        small = Workspace(self.root, max_write=10)
        with self.assertRaises(FsError):
            small.write("big.txt", "x" * 11)
        self.assertEqual((self.root / "pic.png").read_bytes(), b"\x89PNG\0\0 todo")

    # ---------------------------------------------------------------- edit
    def test_edit_single_all_and_dry_run(self):
        (self.root / "e.txt").write_text("a b a\nc\n", "utf-8")
        with self.assertRaises(FsError) as cm:
            self.ws.edit("e.txt", "a", "z")                          # twice: ambiguous
        self.assertIn("2 times", str(cm.exception))
        plan = self.ws.edit("e.txt", "c", "d", dry_run=True)
        self.assertEqual((self.root / "e.txt").read_text("utf-8"), "a b a\nc\n")
        self.assertIn("+d", plan["diff"])
        self.assertEqual(self.ws.edit("e.txt", "c", "d")["replacements"], 1)
        self.assertEqual(self.ws.edit("e.txt", "a", "z", replace_all=True)["replacements"], 2)
        self.assertEqual((self.root / "e.txt").read_text("utf-8"), "z b z\nd\n")
        self.ws.edit("e.txt", "d\n", "")                             # '' deletes
        self.assertEqual((self.root / "e.txt").read_text("utf-8"), "z b z\n")

    def test_edit_errors(self):
        for args in (("missing.txt", "a", "b"), ("a.py", "not there", "x"), ("a.py", "", "x"), ("a.py", "import os", "import os"), ("pic.png", "a", "b"), ("src", "a", "b")):
            with self.assertRaises(FsError, msg=str(args)):
                self.ws.edit(*args)

    def test_edit_keeps_crlf_and_accepts_copied_line_numbers(self):
        (self.root / "c.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
        self.ws.edit("c.txt", "one\ntwo", "ONE\nTWO")
        self.assertEqual((self.root / "c.txt").read_bytes(), b"ONE\r\nTWO\r\nthree\r\n")
        # the model copied filesystem.read's numbered output into 'old' and 'new'
        self.ws.edit("a.py", "    2 | print('hello')", "    2 | print('bye')")
        self.assertEqual((self.root / "a.py").read_text("utf-8").splitlines()[1], "print('bye')")


if __name__ == "__main__":
    unittest.main()
