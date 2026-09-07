import json
from pathlib import Path
import subprocess


class CoreError(RuntimeError):
    pass


def request(executable, method, **parameters):
    path = Path(executable).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise CoreError("Select an existing Sora-Core executable using its absolute path")
    payload = {"protocol": 1, "id": "bridge-1", "method": method, "params": parameters}
    try:
        process = subprocess.run(
            [str(path), "rpc"], input=json.dumps(payload) + "\n",
            capture_output=True, encoding="utf-8", errors="strict", timeout=60,
            check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise CoreError(f"Sora-Core could not complete the request: {error}") from error
    if process.returncode:
        raise CoreError(f"Sora-Core exited with code {process.returncode}: {process.stderr[:2000]}")
    try:
        result = json.loads(process.stdout)
        if result["protocol"] != 1 or result["id"] != "bridge-1":
            raise ValueError("response identity or protocol mismatch")
        if result["ok"] is not True:
            raise CoreError(result["error"]["message"])
        return result["result"]
    except (ValueError, KeyError, TypeError) as error:
        raise CoreError(f"Invalid Sora-Core response: {error}") from error
