import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

spec = importlib.util.spec_from_file_location("sora_client", Path(__file__).resolve().parents[1] / "endfield_bridge/client.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
core = Path(__file__).resolve().parents[2] / "Sora-Core"
executable = Path(os.environ.get('SORA_CORE_EXECUTABLE', core / "src/Sora.Cli/bin/Release/net10.0/Sora-Core.exe"))
database = Path(os.environ.get('SORA_TEST_DATABASE', core / "artifacts/fixtures/fixture.sredb"))


class ClientTests(unittest.TestCase):
    def test_capabilities(self):
        self.assertEqual(client.request(str(executable), "capabilities")["product"], "Sora-Core")

    def test_compatible_scene(self):
        result = client.request(str(executable), "scene", path=str(database), asset="character:sample")
        self.assertEqual(result["meshes"][0]["shapes"][0]["name"], "Smile")
        self.assertEqual(result["clips"][0]["tracks"][0]["keys"][1]["time"], 1)

    def test_missing_executable(self):
        with self.assertRaises(client.CoreError):
            client.request("missing.exe", "capabilities")

    def test_missing_database(self):
        with self.assertRaises(client.CoreError):
            client.request(str(executable), "inspect", path=str(database) + ".missing")

    def test_unknown_method(self):
        with self.assertRaises(client.CoreError):
            client.request(str(executable), "not-a-method")

    def test_process_recovers_after_malformed_input(self):
        requests = ["not-json", json.dumps({"protocol": 2, "id": "bad", "method": "capabilities", "params": {}}), json.dumps({"protocol": 1, "id": "unknown", "method": "unknown", "params": {}}), json.dumps({"protocol": 1, "id": "good", "method": "capabilities", "params": {}})]
        output = subprocess.run([str(executable), "rpc"], input="\n".join(requests) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=10, check=True)
        responses = [json.loads(line) for line in output.stdout.splitlines()]
        self.assertEqual([r["ok"] for r in responses], [False, False, False, True])
        self.assertEqual(responses[-1]["id"], "good")

    def test_oversized_input_recovers(self):
        good = json.dumps({"protocol": 1, "id": "good", "method": "capabilities", "params": {}})
        output = subprocess.run([str(executable), "rpc"], input="x" * (1024 * 1024 + 1) + "\n" + good, capture_output=True, text=True, encoding="utf-8", timeout=10, check=True)
        self.assertEqual([json.loads(line)["ok"] for line in output.stdout.splitlines()], [False, True])

    def test_invalid_utf8_recovers_in_same_process(self):
        good = json.dumps({"protocol": 1, "id": "good", "method": "capabilities", "params": {}}).encode()
        output = subprocess.run([str(executable), "rpc"], input=b'\xff\xfe\n' + good + b'\n', capture_output=True, timeout=10, check=True)
        self.assertEqual([json.loads(line)["ok"] for line in output.stdout.splitlines()], [False, True])


if __name__ == "__main__":
    unittest.main()
