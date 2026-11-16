"""The real server.py agent endpoints, driven with a fake model (fastapi / llama.cpp are stubbed in a child process)."""
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class ServerAgentTest(unittest.TestCase):
    def test_agent_endpoint_end_to_end(self):
        d = tempfile.mkdtemp(prefix="cressy-srv-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "tests"))
        open(os.path.join(d, "calc.py"), "w").write("def add(a, b):\n    return a - b\n")
        open(os.path.join(d, "tests", "__init__.py"), "w").write("")
        open(os.path.join(d, "tests", "test_calc.py"), "w").write(
            "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        p = subprocess.run([sys.executable, "-W", "ignore", os.path.join(HERE, "server_agent_harness.py"), d], capture_output=True, text=True, timeout=180)
        line = [x for x in p.stdout.splitlines() if x.startswith("RESULT ")]
        self.assertTrue(line, p.stdout[-800:] + p.stderr[-1500:])
        r = json.loads(line[0][7:])
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["verification"]["kind"], "tests")
        self.assertEqual(r["files"], ["calc.py"])
        self.assertTrue(r["fixed"])
        self.assertTrue(r["caps"])
        self.assertEqual(r["types"][0], "start")
        self.assertEqual(r["revert"]["files"], ["calc.py"])
        self.assertTrue(r["reverted"])
        self.assertEqual(r["empty"], 400)
        self.assertEqual(r["revert_unknown"], 404)
        self.assertEqual(len(r["detect"]), 1)


if __name__ == "__main__":
    unittest.main()
