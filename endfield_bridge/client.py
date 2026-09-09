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

class CoreTask:
    """One cancellable NDJSON request. Worker threads never access Blender."""
    def __init__(self, executable, method, **parameters):
        import queue
        import threading
        import uuid
        path = Path(executable).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise CoreError('Select an existing Sora-Core executable using its absolute path')
        self.events = queue.Queue()
        self.identity = uuid.uuid4().hex
        self.process = subprocess.Popen([str(path), 'rpc-task'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='strict',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self._stderr = []
        def drain():
            for line in self.process.stderr:
                if sum(map(len, self._stderr)) < 8192:
                    self._stderr.append(line)
        threading.Thread(target=drain, daemon=True).start()
        self.process.stdin.write(json.dumps({'protocol': 1, 'id': self.identity,
            'method': method, 'params': parameters}) + '\n')
        self.process.stdin.flush()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        final = False
        try:
            for line in self.process.stdout:
                event = json.loads(line)
                if event.get('protocol') != 1 or event.get('id') != self.identity:
                    raise CoreError('Task response identity or protocol mismatch')
                if event.get('event') == 'progress':
                    self.events.put(event)
                elif 'ok' in event:
                    final = True
                    self.events.put(event)
                else:
                    raise CoreError('Invalid task event')
            code = self.process.wait()
            if not final:
                raise CoreError('Sora-Core task ended without a result: ' + ''.join(self._stderr))
        except Exception as error:
            self.events.put({'ok': False, 'error': {'message': str(error)}})
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def cancel(self):
        try:
            self.process.stdin.write(json.dumps({'method': 'cancel'}) + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError):
            pass

    def terminate(self):
        if self.process.poll() is None:
            self.process.terminate()

_sessions = {}


class TaskSession:
    """Sequential queries retain Core's bounded last-database cache."""
    def __init__(self, executable):
        import threading
        self.pending = {}
        self.lock = threading.Lock()
        self.process = subprocess.Popen([executable, 'rpc-task-session'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding='utf-8', errors='strict',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        error = 'Sora-Core query session ended'
        try:
            for line in self.process.stdout:
                event = json.loads(line)
                with self.lock:
                    destination = self.pending.get(event.get('id'))
                    if event.get('protocol') != 1 or destination is None:
                        raise CoreError('Query session response identity or protocol mismatch')
                    destination.put(event)
                    if 'ok' in event:
                        del self.pending[event['id']]
        except Exception as failure:
            error = str(failure)
        finally:
            with self.lock:
                for destination in self.pending.values():
                    destination.put({'ok':False, 'error':{'message':error}})
                self.pending.clear()
            if self.process.poll() is None:
                self.process.terminate()
            self.process.stdout.close()

    def send(self, payload, destination):
        with self.lock:
            if self.pending:
                raise CoreError('Query session is still processing a request')
            self.pending[payload['id']] = destination
            try:
                self.process.stdin.write(json.dumps(payload) + '\n')
                self.process.stdin.flush()
            except Exception:
                del self.pending[payload['id']]
                raise


class SessionTask:
    def __init__(self, executable, method, **parameters):
        import queue
        import uuid
        path = Path(executable).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise CoreError('Select an existing Sora-Core executable using its absolute path')
        key = str(path.resolve())
        session = _sessions.get(key)
        if session is None or session.process.poll() is not None:
            session = _sessions[key] = TaskSession(key)
        self.session = session
        self.process = session.process
        self.events = queue.Queue()
        self.identity = uuid.uuid4().hex
        session.send({'protocol':1,'id':self.identity,'method':method,'params':parameters}, self.events)

    def cancel(self):
        with self.session.lock:
            try:
                self.process.stdin.write(json.dumps({'method':'cancel'}) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass

    def terminate(self):
        if self.process.poll() is None:
            self.process.terminate()


def request_task(executable, method, **parameters):
    task_type = SessionTask if method in {'search', 'inspect'} else CoreTask
    return task_type(executable, method, **parameters)


def close_sessions():
    for session in list(_sessions.values()):
        try:
            if session.process.poll() is None:
                session.process.stdin.close()
        except (OSError, ValueError) as error:
            print('[Endfield-Bridge session cleanup] ' + str(error))
            if session.process.poll() is None:
                session.process.terminate()
    _sessions.clear()
