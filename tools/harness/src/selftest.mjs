// End-to-end proof that the harness works: virtual device <-> harness controller.
//
// Transports:
//   sim   (default when the host has no IPv6): device and controller each run in-process on matter.js'
//         NetworkSimulator (an in-memory L2/L3 network with mDNS), so no OS networking is needed. This
//         exercises the full Matter stack (mDNS discovery, PASE, CASE, commissioning, IM read/invoke/
//         subscribe, decommissioning), just not the kernel UDP sockets.
//   real  (default when IPv6 is available): the virtual device runs as a child process on the real
//         network, the controller connects via `--ip 127.0.0.1`-style known address or mDNS.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { networkInterfaces, tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { log, sleep } from "./util.mjs";

const SUB_MAX_INTERVAL = Number(process.env.SELFTEST_SUB_MAX_INTERVAL ?? 5);
const here = dirname(fileURLToPath(import.meta.url));

export const hasIpv6 = () => existsSync("/proc/net/if_inet6") && readFileSync("/proc/net/if_inet6", "utf8").trim().length > 0;

function check(cond, msg) {
    if (!cond) throw new Error(`selftest assertion failed: ${msg}`);
    log(`  ok: ${msg}`);
}

async function waitFor(fn, timeoutMs, what) {
    const start = Date.now();
    for (;;) {
        const v = await fn();
        if (v) return v;
        if (Date.now() - start > timeoutMs) throw new Error(`timeout waiting for ${what}`);
        await sleep(50);
    }
}

async function setupSim() {
    const { Environment, NetworkSimulator, Network, MockStorageService, MemoryStorageDriver, StandardCrypto, Crypto, Entropy } = await import("@matter/general");
    const sim = new NetworkSimulator();
    const mk = (name, index) => {
        const env = new Environment(name);
        env.set(Network, sim.addHost(index));
        const crypto = new StandardCrypto();
        env.set(Entropy, crypto);
        env.set(Crypto, crypto);
        const store = {};
        new MockStorageService(env, () => new MemoryStorageDriver(store));
        return env;
    };
    const { startVirtualDevice } = await import("./virtual-device.mjs");
    const deviceEnv = mk("vdev", 1);
    const controllerEnv = mk("ctl", 2);
    const start = () => startVirtualDevice({ environment: deviceEnv, port: 5540 });
    let cur = await start();
    return {
        transport: "sim",
        dev: {
            manualPairingCode: cur.manualPairingCode,
            setOnOff: v => cur.setOnOff(v),
            setTemperature: v => cur.setTemperature(v),
        },
        controllerEnv,
        commissionArgs: { code: cur.manualPairingCode },
        /** Take the device down and bring it back with the same persisted state. */
        restart: async () => {
            await cur.close();
            await sleep(500);
            cur = await start();
        },
        stop: async () => {
            await cur.close();
        },
    };
}

async function setupReal() {
    const dir = mkdtempSync(join(tmpdir(), "harness-vdev-"));
    const spawnChild = () => {
        const child = spawn(process.execPath, [join(here, "virtual-device.mjs"), "--storage", dir, "--port", "5541"], {
            stdio: ["pipe", "pipe", "inherit"],
        });
        const ready = new Promise((res, rej) => {
            let buf = "";
            child.stdout.on("data", d => {
                buf += d;
                let nl;
                while ((nl = buf.indexOf("\n")) >= 0) {
                    const line = buf.slice(0, nl);
                    buf = buf.slice(nl + 1);
                    try {
                        const o = JSON.parse(line);
                        if (o.event === "ready") res(o);
                    } catch {
                        /* matter.js logs go to stdout too; ignore non-JSON */
                    }
                }
            });
            child.once("exit", code => rej(new Error(`virtual device exited early (${code})`)));
        });
        return { child, ready };
    };
    let cur = spawnChild();
    const info = await cur.ready;
    const killCur = async () => {
        cur.child.removeAllListeners("exit");
        const exited = new Promise(r => cur.child.once("exit", r));
        cur.child.kill("SIGTERM");
        await Promise.race([exited, sleep(5000)]);
    };
    return {
        transport: "real",
        dev: {
            manualPairingCode: info.manualPairingCode,
            setOnOff: async v => cur.child.stdin.write(`onoff ${v ? 1 : 0}\n`),
            setTemperature: async v => cur.child.stdin.write(`temp ${v}\n`),
        },
        controllerEnv: undefined,
        commissionArgs: { code: info.manualPairingCode, ...(process.env.SELFTEST_MDNS ? {} : { ip: process.env.SELFTEST_IP ?? defaultLocalIp(), port: 5541 }) },
        restart: async () => {
            await killCur();
            cur = spawnChild();
            await cur.ready;
        },
        stop: async () => {
            await killCur();
            rmSync(dir, { recursive: true, force: true });
        },
    };
}

export async function selftest({ transport, restart = false } = {}) {
    transport ??= hasIpv6() ? "real" : "sim";
    log(`selftest: transport=${transport} (IPv6 ${hasIpv6() ? "available" : "NOT available in this kernel"})`);
    const { openController } = await import("./controller.mjs");
    const setup = transport === "sim" ? await setupSim() : await setupReal();
    let h;
    const stateDir = mkdtempSync(join(tmpdir(), "harness-state-"));
    try {
        log("1/8 open controller + commission device");
        h = await openController(setup.controllerEnv ? { environment: setup.controllerEnv } : { stateDir });
        const t0 = Date.now();
        const nodeId = await h.commission({ ...setup.commissionArgs, timeoutSeconds: 60 });
        log(`  commissioned as node ${nodeId} in ${Date.now() - t0} ms`);
        check(h.commissionedNodeIds().includes(nodeId), "node listed as commissioned");

        log("2/8 dump");
        const dump = await h.dump(nodeId);
        const eps = Object.keys(dump.data);
        log(`  endpoints: ${eps.join(", ")}`);
        check(eps.includes("0") && eps.includes("1") && eps.includes("2"), "dump has endpoints 0,1,2");
        check(dump.data["1"]["0x0006"] !== undefined, "endpoint 1 has OnOff cluster (0x0006)");
        const g = dump.data["1"]["0x0006"];
        for (const a of ["0xFFFC", "0xFFFB", "0xFFF9", "0xFFF8", "0xFFFD"]) check(a in g, `OnOff global attribute ${a} (${dump.names.attributes["0x0006"][a]}) present`);
        log("  info: BasicInformation EventList (0xFFFA) " + ("0xFFFA" in dump.data["0"]["0x0028"] ? "present" : "not served by matter.js 0.17.9 (a real esp-matter device does serve it)"));
        check(dump.data["2"]["0x0402"]["0x0000"] === 2000, "temperature sensor MeasuredValue == 2000");

        log("3/8 read");
        const r = await h.read(nodeId, 1, "OnOff", "OnOff");
        check(r.value === false, "OnOff initially false");

        log("4/8 subscribe + invoke Toggle -> expect attribute report");
        const sub = await h.subscribe(nodeId, { maxInterval: SUB_MAX_INTERVAL, onRecord: rec => log("  sub:", JSON.stringify(rec)) });
        const toggledAt = Date.now();
        const inv = await h.invoke(nodeId, 1, "OnOff", "Toggle");
        check(inv.ok, "invoke OnOff.Toggle succeeded");
        const rep = await waitFor(
            () => sub.records.find(x => x.kind === "attribute" && x.cluster === "OnOff" && x.attribute === "OnOff" && x.value === true),
            5000,
            "OnOff attribute report",
        );
        log(`  attribute report arrived ${Date.now() - toggledAt} ms after invoke (dt=${rep.dtMs} ms)`);
        check(rep.value === true, "attribute report OnOff==true received");

        log("5/8 device-side change -> report");
        await setup.dev.setTemperature(2150);
        await waitFor(() => sub.records.find(x => x.kind === "attribute" && x.cluster === "TemperatureMeasurement" && x.value === 2150), 5000, "temperature report");
        check(true, "device-side temperature change reported (2150)");

        if (restart) {
            log("5b/8 restart the device; the subscription must resume on its own");
            const before = sub.records.length;
            const tRestart = Date.now();
            await setup.restart();
            const tBack = Date.now();
            log(`  device back up after ${tBack - tRestart} ms`);
            // The device lost its subscriptions and sessions with the restart. matter.js (SustainedSubscription) has to
            // notice, re-discover the device, re-establish session + subscription and re-sync. Keep flipping OnOff on
            // the device and time the first attribute report that reaches the controller after the restart.
            let flip = true;
            let resumedAt;
            const flipper = setInterval(() => void setup.dev.setOnOff((flip = !flip)), 1000);
            try {
                const rep2 = await waitFor(
                    () => sub.records.slice(before).find(x => x.kind === "attribute" && x.cluster === "OnOff" && x.attribute === "OnOff" && Date.parse(x.ts) >= tBack),
                    120000,
                    "attribute report after device restart (subscription resumption)",
                );
                resumedAt = Date.parse(rep2.ts);
            } finally {
                clearInterval(flipper);
            }
            log(`  RESUMPTION TIMING: first attribute report ${resumedAt - tBack} ms after the device was back up (${resumedAt - tRestart} ms after the restart began; subscription max interval ${SUB_MAX_INTERVAL}s)`);
            check(true, "attribute reports flow again after the device restart");
            await setup.dev.setOnOff(true);
            await sleep(500);
        }
        await sub.stop();

        log("6/8 write (BasicInformation.NodeLabel) + read back");
        await h.write(nodeId, 0, "BasicInformation", "NodeLabel", "harness-label");
        check((await h.read(nodeId, 0, "BasicInformation", "NodeLabel")).value === "harness-label", "written NodeLabel read back");

        log("7/8 reopen controller from persisted state and read again");
        await h.close();
        h = await openController(setup.controllerEnv ? { environment: setup.controllerEnv } : { stateDir });
        check(h.commissionedNodeIds().includes(nodeId), "commissioned node persisted across controller restart");
        const r2 = await h.read(nodeId, 1, "OnOff", "OnOff");
        check(r2.value === true, "OnOff == true (last device-side value) read after controller restart");

        log("8/8 decommission");
        await h.remove(nodeId);
        check(!h.commissionedNodeIds().includes(nodeId), "node removed");
        log("SELFTEST PASSED");
        return true;
    } finally {
        await h?.close().catch(() => {});
        await setup.stop().catch(() => {});
        rmSync(stateDir, { recursive: true, force: true });
    }
}

/** Address the real-transport selftest commissions at: ::1 with IPv6, otherwise the first non-loopback IPv4. */
function defaultLocalIp() {
    if (hasIpv6()) return "::1";
    for (const list of Object.values(networkInterfaces())) {
        for (const a of list ?? []) if (a.family === "IPv4" && !a.internal) return a.address;
    }
    return "127.0.0.1";
}
