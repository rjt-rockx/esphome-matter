#!/usr/bin/env node
// esphome-matter test harness CLI. See README.md.
import { readFileSync, writeFileSync } from "node:fs";
import { parseArgs, parseIntLoose, log } from "./util.mjs";

const USAGE = `matter-harness <command> [options]

Global: --state <dir>  controller state dir (default $HARNESS_STATE or ./.harness-state)

  commission --code <manual code|MT:...> [--ip <addr> [--port 5540]] [--node-id N] [--timeout 120]
  list                                     show commissioned nodes
  dump --node <id> [--out file.json]       wildcard read of every attribute -> JSON
  read --node <id> --ep <n> --cluster <name|id> --attribute <name|id>
  write --node <id> --ep <n> --cluster <c> --attribute <a> --value <json>
  invoke --node <id> --ep <n> --cluster <c> --command <name|id> [--args '<json>']
  subscribe --node <id> [--duration <sec>] [--out file.jsonl] [--min 0] [--max 60]
  remove --node <id>
  selftest [--transport sim|real] [--restart]   (--restart also reboots the device and times subscription resumption, ~1 min)

Add --verbose [debug|info|notice|warn] for matter.js logs on stderr (default: error).
  scenario <file.yaml|json>... [--node <id>] [--ip/--port/--code to commission first] [--keep] [--var k=v,...] [--junit out.xml]
`;

const args = parseArgs(process.argv.slice(2));
const cmd = args._[0];
// matter.js reads process.argv for its environment variables; hide ours from it.
process.argv = process.argv.slice(0, 2);
// stdout is reserved for machine readable output: matter.js logs through console.*, send them to stderr.
console.log = console.info = console.debug = console.error;
if (args.verbose) process.env.HARNESS_LOG = args.verbose === true ? "info" : args.verbose;

if (!cmd || args.help || cmd === "help") {
    console.log(USAGE);
    process.exit(cmd ? 0 : 1);
}

const need = (name, alt) => {
    const v = args[name] ?? (alt && args[alt]);
    if (v === undefined || v === true) throw new Error(`Missing --${name}`);
    return v;
};
const parseJson = (s, what) => {
    try {
        return JSON.parse(s);
    } catch (e) {
        throw new Error(`--${what} is not valid JSON: ${e.message}`);
    }
};
/** --var ep=2,speed=3  ->  { ep: 2, speed: 3 } (numbers/booleans/JSON are parsed) */
const parseVars = spec =>
    Object.fromEntries(
        String(spec === undefined || spec === true ? "" : spec)
            .split(",")
            .filter(Boolean)
            .map(kv => {
                const i = kv.indexOf("=");
                const raw = kv.slice(i + 1);
                let v = raw;
                try {
                    v = JSON.parse(raw);
                } catch {
                    /* keep string */
                }
                return [kv.slice(0, i), v];
            }),
    );
const out = obj => process.stdout.write(JSON.stringify(obj, null, 2) + "\n");

async function main() {
    if (cmd === "selftest") {
        const { selftest } = await import("./selftest.mjs");
        await selftest({ transport: args.transport, restart: !!args.restart });
        return;
    }
    const { openController } = await import("./controller.mjs");
    const h = await openController({ stateDir: args.state });
    let exitCode = 0;
    try {
        switch (cmd) {
            case "commission": {
                const t0 = Date.now();
                const nodeId = await h.commission({
                    code: need("code"),
                    ip: args.ip,
                    port: args.port ?? 5540,
                    timeoutSeconds: Number(args.timeout ?? 120),
                    nodeId: args["node-id"],
                });
                out({ nodeId, elapsedMs: Date.now() - t0 });
                break;
            }
            case "list":
                out({ nodes: h.commissionedNodeIds(), details: h.controller.getCommissionedNodesDetails().map(d => ({ nodeId: Number(d.nodeId), ...JSON.parse(JSON.stringify(d.deviceData ?? {}, (_, v) => (typeof v === "bigint" ? v.toString() : v))) })) });
                break;
            case "dump": {
                const dump = await h.dump(h.resolveNodeId(args.node));
                const text = JSON.stringify(dump, null, 2) + "\n";
                if (args.out) {
                    writeFileSync(args.out, text);
                    log(`wrote ${args.out}`);
                } else process.stdout.write(text);
                break;
            }
            case "read":
                out(await h.read(h.resolveNodeId(args.node), need("ep"), need("cluster"), need("attribute")));
                break;
            case "write":
                out(await h.write(h.resolveNodeId(args.node), need("ep"), need("cluster"), need("attribute"), parseJson(need("value"), "value")));
                break;
            case "invoke":
                out(await h.invoke(h.resolveNodeId(args.node), need("ep"), need("cluster"), need("command"), args.args ? parseJson(args.args, "args") : {}));
                break;
            case "remove":
                out(await h.remove(h.resolveNodeId(args.node)));
                break;
            case "subscribe": {
                const nodeId = h.resolveNodeId(args.node);
                const duration = Number(args.duration ?? 30);
                const lines = [];
                const sub = await h.subscribe(nodeId, {
                    minInterval: Number(args.min ?? 0),
                    maxInterval: Number(args.max ?? 60),
                    onRecord: rec => {
                        const line = JSON.stringify(rec);
                        process.stdout.write(line + "\n");
                        lines.push(line);
                    },
                });
                log(`subscribed to node ${nodeId} for ${duration}s`);
                await new Promise(r => setTimeout(r, duration * 1000));
                await sub.stop();
                if (args.out) writeFileSync(args.out, lines.join("\n") + "\n");
                break;
            }
            case "scenario": {
                const { runScenarioFiles } = await import("./scenario.mjs");
                const files = args._.slice(1);
                if (!files.length) throw new Error("scenario: give one or more files");
                let nodeId = args.node !== undefined ? h.resolveNodeId(args.node) : undefined;
                let commissioned = false;
                if (nodeId === undefined && args.code) {
                    nodeId = await h.commission({ code: args.code, ip: args.ip, port: args.port ?? 5540 });
                    commissioned = true;
                }
                nodeId ??= h.resolveNodeId(undefined);
                const ok = await runScenarioFiles(h, nodeId, files, { junit: args.junit, vars: parseVars(args.var) });
                if (commissioned && !args.keep) await h.remove(nodeId);
                if (!ok) exitCode = 1;
                break;
            }
            default:
                console.log(USAGE);
                exitCode = 1;
        }
    } finally {
        await h.close().catch(() => {});
    }
    return exitCode;
}

main().then(
    code => process.exit(code ?? 0),
    err => {
        console.error(`ERROR: ${err?.stack ?? err}`);
        process.exit(1);
    },
);
void parseIntLoose;
void readFileSync;
