"""Open any folder at any time (/api/browse, /api/root) and the change fingerprint the UI polls (/api/fs/stamp).
Run: python -m unittest tests.test_server_root -v"""
import os
import sys
import tempfile
import time
import types
import unittest

HDR = {"X-Cressy": "1"}


def setUpModule():
    global CLIENT, server, A, B
    m = types.ModuleType("llama_cpp")
    m.Llama = object
    sys.modules.setdefault("llama_cpp", m)
    A, B = tempfile.mkdtemp(), tempfile.mkdtemp()
    os.makedirs(os.path.join(B, "sub"))
    open(os.path.join(B, "hello.txt"), "w").write("hi")
    sys.argv = ["server.py", "--no-vision", "--no-image", "--no-reasoner", "--no-kb", "--data-dir", A, "--models-dir", A, "--root", A]
    import server as server_mod
    server = server_mod
    from fastapi.testclient import TestClient
    CLIENT = TestClient(server.app, base_url="http://127.0.0.1")


class RootTests(unittest.TestCase):
    def test_browse_lists_subfolders_of_any_folder(self):
        d = CLIENT.get("/api/browse", params={"path": B}).json()
        self.assertEqual(d["dirs"], ["sub"])
        self.assertTrue(d["parent"])
        self.assertTrue(any(p["name"] == "Home" for p in d["places"]))
        self.assertEqual(CLIENT.get("/api/browse", params={"path": os.path.join(B, "nope")}).status_code, 404)

    def test_open_folder_switches_everything_and_needs_the_header(self):
        self.assertEqual(CLIENT.post("/api/root", json={"path": B}).status_code, 403)
        self.assertEqual(CLIENT.post("/api/root", json={"path": os.path.join(B, "nope")}, headers=HDR).status_code, 404)
        r = CLIENT.post("/api/root", json={"path": B}, headers=HDR)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(str(server.ROOT), os.path.realpath(B))
        fs = CLIENT.get("/api/fs").json()
        self.assertEqual({i["name"] for i in fs["items"]}, {"sub", "hello.txt"})
        self.assertEqual(CLIENT.get("/api/caps").json()["root"], os.path.realpath(B))
        self.assertEqual(server.FS.root, server.ROOT)
        self.assertEqual(server.MEM.project, str(server.ROOT))
        CLIENT.post("/api/root", json={"path": A}, headers=HDR)
        self.assertEqual(str(server.ROOT), os.path.realpath(A))

    def test_stamp_changes_when_files_change(self):
        CLIENT.post("/api/root", json={"path": B}, headers=HDR)
        s1 = CLIENT.get("/api/fs/stamp").json()
        self.assertEqual(s1["sig"], CLIENT.get("/api/fs").json()["sig"])
        time.sleep(0.02)
        open(os.path.join(B, "new.html"), "w").write("<p>x</p>")
        s2 = CLIENT.get("/api/fs/stamp").json()
        self.assertNotEqual(s1["sig"], s2["sig"])
        self.assertTrue(CLIENT.get("/api/fs/stamp", params={"path": "gone"}).json()["missing"])
        CLIENT.post("/api/root", json={"path": A}, headers=HDR)


if __name__ == "__main__":
    unittest.main()
