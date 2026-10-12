"""Runtime test fixtures: the real firmware under QEMU, a matter.js controller and an ESPHome API client.

    qemu_device(yaml)   build (content-hash cached), boot, wait for "Server ready!", tee the UART log
    matter              harness `serve` process: commission, read/write/invoke, subscription reports
    api                 aioesphomeapi client on the forwarded TCP port 6053 (ESPHome-side stimuli and readback)
    node                matter + api, commissioned, subscription live (the fixture most tests want)

See docs/dev/runtime-tests.md. Environment variables:

    RUNTIME_YAML         firmware used by the `device` fixture   (default tests/qemu/golden.yaml)
    RUNTIME_IMAGE        use this prebuilt merged flash image instead of building
    RUNTIME_CACHE        image cache directory                   (default ~/.cache/esphome-matter-runtime)
    RUNTIME_BUILD_DIR    run.sh BUILD_DIR                        (default ~/build-qemu)
    RUNTIME_WORK_NAME    run.sh WORK_NAME, shared incremental build dir (default "runtime")
    RUNTIME_ARTIFACTS    UART log, harness log, subscription log (default <repo>/test-artifacts/runtime)
    QEMU_DIR             unpacked Espressif QEMU (run.sh default ~/tools/qemu/x/qemu)
    BOOT_TIMEOUT         seconds to wait for "Server ready!"      (default 300)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_SH = REPO / "tools" / "qemu" / "run.sh"
HARNESS_CLI = REPO / "tools" / "harness" / "src" / "cli.mjs"
DEFAULT_YAML = REPO / "tests" / "qemu" / "golden.yaml"
MANUAL_CODE = "34970112332"  # passcode 20202021, discriminator 3840 (fixed in the YAML)
MATTER_PORT = int(os.environ.get("MATTER_PORT", "5540"))
API_PORT = int(os.environ.get("API_PORT", "6053"))
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

ARTIFACTS = Path(os.environ.get("RUNTIME_ARTIFACTS", REPO / "test-artifacts" / "runtime"))


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: boot, commission, toggle a light (every PR)")
    config.addinivalue_line("markers", "golden: golden behavioural tests (nightly / label `runtime`)")


def pytest_collection_modifyitems(items):
    """Run the smoke tests first: boot and commissioning are the first thing to look at when something breaks."""
    items.sort(key=lambda i: 0 if i.get_closest_marker("smoke") else 1)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # run.sh needs esphome and esptool.py: use the ones next to the interpreter running pytest.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.setdefault("CMAKE_BUILD_PARALLEL_LEVEL", "3")
    return env


def content_hash(yaml: Path) -> str:
    """Hash of everything that ends up in the firmware: the YAML, components/ and the build script."""
    h = hashlib.sha256()
    h.update(yaml.read_bytes())
    h.update(RUN_SH.read_bytes())
    h.update(os.environ.get("RUNTIME_CACHE_SALT", "").encode())
    for p in sorted((REPO / "components").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(REPO)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def ensure_image(yaml: Path) -> Path:
    """Return a merged flash image for `yaml`, building it with run.sh unless the content hash is cached."""
    prebuilt = os.environ.get("RUNTIME_IMAGE")
    if prebuilt:
        return Path(prebuilt)
    cache = Path(os.environ.get("RUNTIME_CACHE", Path.home() / ".cache" / "esphome-matter-runtime"))
    key = content_hash(yaml)
    cached = cache / f"{yaml.stem}-{key}" / "flash.bin"
    if cached.exists():
        return cached
    env = _env()
    build_dir = os.environ.get("RUNTIME_BUILD_DIR", str(Path.home() / "build-qemu"))
    work_name = os.environ.get("RUNTIME_WORK_NAME", "runtime")
    env.update(BUILD_DIR=build_dir, WORK_NAME=work_name)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with open(ARTIFACTS / "build.log", "w") as log:
        r = subprocess.run([str(RUN_SH), "build", str(yaml)], env=env, stdout=log, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        tail = (ARTIFACTS / "build.log").read_text(errors="replace")[-3000:]
        pytest.fail(f"run.sh build failed ({r.returncode}); tail of build.log:\n{tail}", pytrace=False)
    cached.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(Path(build_dir) / work_name / "flash.bin", cached)
    return cached


class QemuDevice:
    """One booted QEMU instance. The UART log is teed to ARTIFACTS/uart.log."""

    def __init__(self, yaml: Path, image: Path):
        self.yaml = yaml
        self.image = image
        self.run_dir = Path(tempfile.mkdtemp(prefix="qemu-run-"))
        self.run_image = self.run_dir / "flash.run.bin"  # QEMU writes NVS into this copy
        shutil.copy(image, self.run_image)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        self.log_path = ARTIFACTS / "uart.log"
        self.proc: subprocess.Popen | None = None
        self.boot_seconds: float | None = None

    def boot(self, timeout: float | None = None) -> None:
        timeout = timeout or float(os.environ.get("BOOT_TIMEOUT", "300"))
        env = _env()
        env["FLASH_IMAGE"] = str(self.run_image)
        out = subprocess.run([str(RUN_SH), "cmd", str(self.yaml)], env=env, capture_output=True, text=True)
        if out.returncode != 0:
            pytest.fail(f"run.sh cmd failed: {out.stderr}", pytrace=False)
        # Only the last line is the command; run.sh may print progress before it.
        lines = [line for line in out.stdout.splitlines() if line.strip()]
        cmd = shlex.split(lines[-1]) if lines else []
        self._log = open(self.log_path, "wb")
        t0 = time.monotonic()
        self.proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)
        while time.monotonic() - t0 < timeout:
            if "Server ready!" in self.log_text():
                self.boot_seconds = time.monotonic() - t0
                return
            if self.proc.poll() is not None:
                pytest.fail(f"QEMU exited with {self.proc.returncode} before 'Server ready!':\n{self.log_text()[-3000:]}", pytrace=False)
            time.sleep(1)
        pytest.fail(f"'Server ready!' not seen within {timeout:.0f} s:\n{self.log_text()[-3000:]}", pytrace=False)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if getattr(self, "_log", None):
            self._log.close()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    # -- UART log ---------------------------------------------------------------------------------------------
    def log_text(self, since: int = 0) -> str:
        """The UART log (ANSI colors stripped) from character offset `since`."""
        try:
            return ANSI.sub("", self.log_path.read_text(errors="replace"))[since:]
        except FileNotFoundError:
            return ""

    def log_mark(self) -> int:
        return len(self.log_text())

    def log_lines(self, since: int = 0, pattern: str | None = None) -> list[str]:
        lines = self.log_text(since).splitlines()
        return [ln for ln in lines if re.search(pattern, ln)] if pattern else lines

    def wait_log(self, pattern: str, since: int = 0, timeout: float = 10) -> str | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for ln in self.log_lines(since):
                if re.search(pattern, ln):
                    return ln
            time.sleep(0.25)
        return None


class MatterError(RuntimeError):
    pass


class Matter:
    """Controller side: matter.js harness. `commission` is a one-shot CLI call, everything else goes through
    one long-running `serve` process (the harness state directory is locked, so there can only be one)."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.node_id: int | None = None
        self.commission_seconds: float | None = None
        self.proc: subprocess.Popen | None = None
        self._records: list[dict] = []
        self._cond = threading.Condition()
        self._pending: dict[int, dict] = {}
        self._next_id = 1
        self._stderr = None
        ARTIFACTS.mkdir(parents=True, exist_ok=True)

    def _cli(self, *args: str) -> list[str]:
        return ["node", str(HARNESS_CLI), *args, "--state", str(self.state_dir), "--operational-ip", "127.0.0.1"]

    # -- lifecycle ---------------------------------------------------------------------------------------------
    def commission(self, timeout: float = 300) -> int:
        if self.node_id is not None:
            return self.node_id
        t0 = time.monotonic()
        with open(ARTIFACTS / "harness-commission.log", "wb") as err:
            r = subprocess.run(
                self._cli("commission", "--code", MANUAL_CODE, "--ip", "127.0.0.1", "--port", str(MATTER_PORT), "--timeout", str(int(timeout))),
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
                timeout=timeout + 60,
            )
        if r.returncode != 0:
            tail = (ARTIFACTS / "harness-commission.log").read_text(errors="replace")[-2000:]
            raise MatterError(f"commission failed ({r.returncode}): {tail}")
        self.node_id = int(json.loads(r.stdout)["nodeId"])
        self.commission_seconds = time.monotonic() - t0
        return self.node_id

    def start(self) -> None:
        """Start the serve process (after commissioning)."""
        if self.proc:
            return
        self._stderr = open(ARTIFACTS / "harness.log", "wb")
        env = dict(os.environ, HARNESS_OPERATIONAL_IP="127.0.0.1", HARNESS_OPERATIONAL_PORT=str(MATTER_PORT))
        verbose = os.environ.get("RUNTIME_HARNESS_VERBOSE", "info" if os.environ.get("CI") else "")
        self.proc = subprocess.Popen(
            self._cli("serve", "--node", str(self.node_id), *(["--verbose", verbose] if verbose else []), "--operational-port", str(MATTER_PORT)),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        with self._cond:
            if not self._cond.wait_for(lambda: self._ready, timeout=60):
                raise MatterError(f"harness serve did not become ready{self._stderr_tail()}")

    _ready = False
    _warm = False

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        sub_log = open(ARTIFACTS / "subscription.jsonl", "w")
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self._cond:
                if "record" in msg:
                    rec = msg["record"]
                    rec["_t"] = time.monotonic()
                    self._records.append(rec)
                    sub_log.write(json.dumps(rec) + "\n")
                    sub_log.flush()
                elif msg.get("ready"):
                    self._ready = True
                elif "id" in msg and msg["id"] in self._pending:
                    self._pending[msg["id"]].update(msg)
                    self._pending[msg["id"]]["done"] = True
                self._cond.notify_all()
        with self._cond:
            self._ready = False
            for p in self._pending.values():
                p.setdefault("error", "harness serve exited")
                p["done"] = True
            self._cond.notify_all()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                assert self.proc.stdin
                self.proc.stdin.close()
                self.proc.wait(15)
            except Exception:
                self.proc.kill()
        if self._stderr:
            self._stderr.close()

    def _stderr_tail(self, chars: int = 4000) -> str:
        """Tail of the serve process's stderr (harness.log), so a CI failure shows it in the job log."""
        try:
            if self._stderr:
                self._stderr.flush()
            text = ANSI.sub("", (ARTIFACTS / "harness.log").read_text(errors="replace"))
        except OSError:
            return ""
        return f"\n--- harness serve stderr (last {chars} chars) ---\n{text[-chars:]}" if text.strip() else ""

    # -- requests ----------------------------------------------------------------------------------------------
    def request(self, op: str, timeout: float = 90, **fields: Any) -> Any:
        assert self.proc and self.proc.stdin, "call start() first"
        if not self._warm and op != "ping":
            # The first operation sets up the CASE session, which can take minutes on a slow runner.
            timeout = max(timeout, float(os.environ.get("RUNTIME_FIRST_REQUEST_TIMEOUT", "300")))
            self._warm = True
        with self._cond:
            rid = self._next_id
            self._next_id += 1
            slot: dict = {}
            self._pending[rid] = slot
        self.proc.stdin.write(json.dumps({"id": rid, "op": op, **fields}) + "\n")
        self.proc.stdin.flush()
        with self._cond:
            ok = self._cond.wait_for(lambda: slot.get("done"), timeout=timeout)
            self._pending.pop(rid, None)
        if not ok:
            raise MatterError(f"{op} {fields}: no response within {timeout} s{self._stderr_tail()}")
        if "error" in slot:
            raise MatterError(f"{op} {fields}: {slot['error']}{self._stderr_tail()}")
        return slot["result"]

    def read(self, ep: int, cluster: str, attribute: str) -> Any:
        return self.request("read", ep=ep, cluster=cluster, attribute=attribute)["value"]

    def write(self, ep: int, cluster: str, attribute: str, value: Any) -> None:
        self.request("write", ep=ep, cluster=cluster, attribute=attribute, value=value)

    def invoke(self, ep: int, cluster: str, command: str, args: dict | None = None) -> Any:
        return self.request("invoke", ep=ep, cluster=cluster, command=command, args=args or {})

    def subscribe(self) -> None:
        self.request("subscribe", timeout=300, min=0, max=60)

    # -- subscription records ----------------------------------------------------------------------------------
    def mark(self) -> int:
        """Position in the record stream; pass to reports()/events()/wait_*() as `since`."""
        with self._cond:
            return len(self._records)

    def records(self, since: int = 0) -> list[dict]:
        with self._cond:
            return list(self._records[since:])

    def reports(self, ep: int, cluster: str, attribute: str, since: int = 0) -> list[Any]:
        """Values of the attribute reports seen since `since`, oldest first."""
        return [r["value"] for r in self.records(since) if r["kind"] == "attribute" and r["endpoint"] == ep and r["cluster"] == cluster and r["attribute"] == attribute]

    def events(self, ep: int, cluster: str, event: str, since: int = 0) -> list[Any]:
        return [r["data"] for r in self.records(since) if r["kind"] == "event" and r["endpoint"] == ep and r["cluster"] == cluster and r["event"] == event]

    def wait_report(self, ep: int, cluster: str, attribute: str, expect: Any | Callable[[Any], bool], since: int = 0, timeout: float = 5) -> bool:
        """Wait until a report whose value equals `expect` (or satisfies the predicate) has been seen since `since`."""
        pred = expect if callable(expect) else (lambda v: v == expect)
        with self._cond:
            return self._cond.wait_for(lambda: any(pred(v) for v in self.reports(ep, cluster, attribute, since)), timeout=timeout)

    def wait_event(self, ep: int, cluster: str, event: str, since: int = 0, timeout: float = 5, pred: Callable[[Any], bool] = lambda d: True) -> Any | None:
        found: list = []

        def check() -> bool:
            for d in self.events(ep, cluster, event, since):
                if pred(d):
                    found.append(d)
                    return True
            return False

        with self._cond:
            self._cond.wait_for(check, timeout=timeout)
        return found[0] if found else None


class Api:
    """Synchronous facade over aioesphomeapi (a private event loop in a thread)."""

    def __init__(self, host: str = "127.0.0.1", port: int = API_PORT):
        import aioesphomeapi

        self._lib = aioesphomeapi
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._host, self._port = host, port
        self._cli = self._run(self._make_client())
        self.entities: dict[str, Any] = {}  # object_id -> EntityInfo
        self.services: dict[str, Any] = {}
        self._states: dict[int, Any] = {}
        self._cond = threading.Condition()

    async def _make_client(self):
        # aioesphomeapi needs a running loop at construction time.
        return self._lib.APIClient(self._host, self._port, None, client_info="matter-runtime-tests")

    def _run(self, coro, timeout: float = 60):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def connect(self, timeout: float = 120) -> None:
        end = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < end:
            try:
                self._run(self._cli.connect(login=True), 30)
                entities, services = self._run(self._cli.list_entities_services())
                self.entities = {e.object_id: e for e in entities}
                self.services = {s.name: s for s in services}
                self._run(self._subscribe())
                return
            except Exception as e:  # noqa: BLE001 - the API server may not be up yet
                last = e
                time.sleep(3)
        raise RuntimeError(f"could not connect to the ESPHome API on port {API_PORT}: {last!r}")

    async def _subscribe(self) -> None:
        def on_state(state) -> None:
            with self._cond:
                self._states[state.key] = state
                self._cond.notify_all()

        self._cli.subscribe_states(on_state)

    def close(self) -> None:
        try:
            self._run(self._cli.disconnect(), 10)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    # -- state -------------------------------------------------------------------------------------------------
    def state(self, object_id: str) -> Any:
        """Latest state of an entity (an aioesphomeapi EntityState), or None if none was received yet."""
        return self._states.get(self.entities[object_id].key)

    def wait_state(self, object_id: str, pred: Callable[[Any], bool], timeout: float = 5) -> Any | None:
        key = self.entities[object_id].key
        with self._cond:
            self._cond.wait_for(lambda: key in self._states and pred(self._states[key]), timeout=timeout)
            st = self._states.get(key)
            return st if st is not None and pred(st) else None

    # -- commands ----------------------------------------------------------------------------------------------
    def _call(self, fn: Callable, *args: Any, **kw: Any) -> None:
        async def run() -> None:
            fn(*args, **kw)

        self._run(run())

    def light(self, object_id: str, **kw: Any) -> None:
        kw.setdefault("transition_length", 0.0)
        self._call(self._cli.light_command, self.entities[object_id].key, **kw)

    def switch(self, object_id: str, state: bool) -> None:
        self._call(self._cli.switch_command, self.entities[object_id].key, state)

    def fan(self, object_id: str, **kw: Any) -> None:
        self._call(self._cli.fan_command, self.entities[object_id].key, **kw)

    def climate(self, object_id: str, **kw: Any) -> None:
        self._call(self._cli.climate_command, self.entities[object_id].key, **kw)

    def select(self, object_id: str, option: str) -> None:
        self._call(self._cli.select_command, self.entities[object_id].key, option)

    def cover(self, object_id: str, **kw: Any) -> None:
        self._call(self._cli.cover_command, self.entities[object_id].key, **kw)

    def lock(self, object_id: str, command: Any) -> None:
        self._call(self._cli.lock_command, self.entities[object_id].key, command)

    def action(self, name: str, **data: Any) -> None:
        self._run(self._cli.execute_service(self.services[name], data), 60)

    def action_burst(self, name: str, calls: list[dict]) -> float:
        """Fire all `calls` of an API action concurrently; returns the elapsed seconds."""

        async def run() -> float:
            t0 = time.monotonic()
            await asyncio.gather(*(self._cli.execute_service(self.services[name], c) for c in calls))
            return time.monotonic() - t0

        return self._run(run(), 120)


# -- fixtures ----------------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def qemu_device():
    """Factory: `qemu_device(yaml)` builds (or takes the cached image), boots and returns a QemuDevice.

    The forwarded ports are fixed, so there is one device at a time: asking for another YAML stops the first."""
    current: dict[str, QemuDevice] = {}

    def factory(yaml: str | Path) -> QemuDevice:
        yaml = Path(yaml)
        if not yaml.is_absolute():
            yaml = REPO / yaml
        if "dev" in current and current["dev"].yaml == yaml:
            return current["dev"]
        if "dev" in current:
            current.pop("dev").stop()
        dev = QemuDevice(yaml, ensure_image(yaml))
        current["dev"] = dev
        dev.boot()
        return dev

    yield factory
    if "dev" in current:
        current["dev"].stop()


@pytest.fixture(scope="session")
def device(qemu_device) -> QemuDevice:
    return qemu_device(os.environ.get("RUNTIME_YAML", DEFAULT_YAML))


@pytest.fixture(scope="session")
def matter(device):
    """Controller, not commissioned yet: call `matter.commission()` (the smoke test does so explicitly)."""
    state = Path(tempfile.mkdtemp(prefix="harness-state-"))
    m = Matter(state)
    yield m
    m.stop()
    shutil.rmtree(state, ignore_errors=True)


@pytest.fixture(scope="session")
def api(device):
    a = Api()
    a.connect()
    yield a
    a.close()


@pytest.fixture(scope="session")
def node(matter, api, device):
    """Commissioned device with a live subscription. Priming takes about 90 s under emulation; this fixture
    only returns once a probe (an API-side sensor change) has been seen as a report."""
    matter.commission()
    matter.start()
    mark = matter.mark()
    matter.subscribe()
    if "set_temp" in api.services:
        deadline = time.monotonic() + 300
        i = 0
        while time.monotonic() < deadline:
            i += 1
            api.action("set_temp", value=10.0 + (i % 5))
            if matter.wait_report(4, "TemperatureMeasurement", "MeasuredValue", lambda v: v is not None, since=mark, timeout=15):
                break
        else:
            pytest.fail("subscription never delivered a report within 300 s", pytrace=False)
    return matter
