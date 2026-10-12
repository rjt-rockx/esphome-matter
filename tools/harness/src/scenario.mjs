// Declarative scenario runner. See README.md for the file format.
import { readFileSync, writeFileSync } from "node:fs";
import { basename } from "node:path";
import { parseAllDocuments } from "yaml";
import { clusterName, resolveAttribute, resolveCluster, resolveEvent } from "./model.mjs";
import { deepEqual, hex, log, sleep } from "./util.mjs";

/** "2s" | "500ms" | "1m" | number(ms) -> ms */
export function parseDuration(v, dflt) {
    if (v === undefined || v === null) return dflt;
    if (typeof v === "number") return v;
    const m = /^([\d.]+)\s*(ms|s|m)?$/.exec(String(v).trim());
    if (!m) throw new Error(`Bad duration "${v}"`);
    const n = parseFloat(m[1]);
    return m[2] === "ms" ? n : m[2] === "m" ? n * 60000 : (m[2] ?? "s") === "s" ? n * 1000 : n;
}

function substitute(value, vars) {
    if (typeof value === "string") {
        const whole = /^\$\{(\w+)\}$/.exec(value);
        if (whole) {
            if (!(whole[1] in vars)) throw new Error(`Undefined variable \${${whole[1]}}`);
            return vars[whole[1]];
        }
        return value.replace(/\$\{(\w+)\}/g, (_, n) => {
            if (!(n in vars)) throw new Error(`Undefined variable \${${n}}`);
            return String(vars[n]);
        });
    }
    if (Array.isArray(value)) return value.map(v => substitute(v, vars));
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, substitute(v, vars)]));
    return value;
}

function pick(value, path) {
    if (path === undefined || path === "") return value;
    return String(path)
        .split(".")
        .reduce((v, k) => (v === null || v === undefined ? undefined : v[k]), value);
}

/** Evaluate an expectation block against a value. Returns null if satisfied, else a message. */
export function checkExpectation(exp, actualRaw) {
    const actual = pick(actualRaw, exp.path ?? exp.field);
    if ("equals" in exp && !deepEqual(actual, exp.equals)) return `expected ${JSON.stringify(exp.equals)}, got ${JSON.stringify(actual)}`;
    if ("notEquals" in exp && deepEqual(actual, exp.notEquals)) return `expected anything but ${JSON.stringify(exp.notEquals)}`;
    if ("oneOf" in exp && !exp.oneOf.some(x => deepEqual(actual, x))) return `expected one of ${JSON.stringify(exp.oneOf)}, got ${JSON.stringify(actual)}`;
    if ("gte" in exp && !(actual >= exp.gte)) return `expected >= ${exp.gte}, got ${JSON.stringify(actual)}`;
    if ("lte" in exp && !(actual <= exp.lte)) return `expected <= ${exp.lte}, got ${JSON.stringify(actual)}`;
    if ("gt" in exp && !(actual > exp.gt)) return `expected > ${exp.gt}, got ${JSON.stringify(actual)}`;
    if ("lt" in exp && !(actual < exp.lt)) return `expected < ${exp.lt}, got ${JSON.stringify(actual)}`;
    if ("contains" in exp) {
        const ok = Array.isArray(actual) ? actual.some(x => deepEqual(x, exp.contains)) : String(actual).includes(String(exp.contains));
        if (!ok) return `expected to contain ${JSON.stringify(exp.contains)}, got ${JSON.stringify(actual)}`;
    }
    if ("exists" in exp && (actual !== undefined && actual !== null) !== exp.exists) return `expected exists=${exp.exists}, got ${JSON.stringify(actual)}`;
    if ("hasBits" in exp && ((Number(actual) & exp.hasBits) !== exp.hasBits)) return `expected bits ${hex(exp.hasBits)} set in ${JSON.stringify(actual)}`;
    return null;
}

const KNOWN_STEPS = ["invoke", "read", "write", "expect", "expect_event", "sleep", "log", "set_var"];

class Runner {
    constructor(h, nodeId, sub, vars) {
        this.h = h;
        this.nodeId = nodeId;
        this.sub = sub;
        this.vars = vars;
        this.waiters = new Set();
        this.lastAttr = new Map(); // "ep/cluster/attr" -> value from reports
        this.history = []; // { at (ms), rec } of all subscription records
        this.lastActionAt = Date.now(); // start of the most recent invoke/write step
    }

    onRecord(rec) {
        this.history.push({ at: Date.now(), rec });
        if (rec.kind === "attribute") this.lastAttr.set(`${rec.endpoint}/${rec.cluster}/${rec.attribute}`, rec.value);
        for (const w of [...this.waiters]) w(rec);
    }

    async waitFor(pred, timeoutMs) {
        return new Promise(resolve => {
            const timer = setTimeout(() => {
                this.waiters.delete(w);
                resolve(undefined);
            }, timeoutMs);
            const w = rec => {
                if (pred(rec)) {
                    clearTimeout(timer);
                    this.waiters.delete(w);
                    resolve(rec);
                }
            };
            this.waiters.add(w);
        });
    }

    async step(raw) {
        const type = KNOWN_STEPS.find(k => k in raw);
        if (!type) throw new Error(`Unknown step ${JSON.stringify(raw)} (expected one of ${KNOWN_STEPS.join(", ")})`);
        const s = substitute(raw[type], this.vars);
        const ep = s?.ep ?? s?.endpoint;
        switch (type) {
            case "sleep":
                await sleep(parseDuration(s));
                return `slept ${s}`;
            case "log":
                log(`  scenario: ${s}`);
                return String(s);
            case "set_var":
                Object.assign(this.vars, s);
                return JSON.stringify(s);
            case "read": {
                const r = await this.h.read(this.nodeId, ep, s.cluster, s.attribute);
                if (s.save) this.vars[s.save] = r.value;
                const err = checkExpectation(s, r.value);
                if (err) throw new Error(`read ${ep}/${s.cluster}/${s.attribute}: ${err}`);
                return `${r.cluster}.${r.attribute} = ${JSON.stringify(r.value)}`;
            }
            case "write": {
                this.lastActionAt = Date.now();
                const expectErr = s.expectError !== undefined;
                try {
                    await this.h.write(this.nodeId, ep, s.cluster, s.attribute, s.value);
                } catch (e) {
                    if (expectErr && (s.expectError === true || String(e.message).toLowerCase().includes(String(s.expectError).toLowerCase()))) return `write failed as expected: ${e.message}`;
                    throw e;
                }
                if (expectErr) throw new Error("write unexpectedly succeeded");
                return `wrote ${s.cluster}.${s.attribute} = ${JSON.stringify(s.value)}`;
            }
            case "invoke": {
                this.lastActionAt = Date.now();
                const expectErr = s.expectError !== undefined;
                let res;
                try {
                    res = await this.h.invoke(this.nodeId, ep, s.cluster, s.command, s.args ?? {});
                } catch (e) {
                    if (expectErr && (s.expectError === true || String(e.message).toLowerCase().includes(String(s.expectError).toLowerCase()))) return `invoke failed as expected: ${e.message}`;
                    throw new Error(`invoke ${s.cluster}.${s.command} failed: ${e.message}`);
                }
                if (expectErr) throw new Error(`invoke ${s.cluster}.${s.command} unexpectedly succeeded`);
                if (s.response) {
                    const err = checkExpectation(s.response, res.response);
                    if (err) throw new Error(`invoke ${s.cluster}.${s.command} response: ${err}`);
                }
                return `${res.command} ok${res.response ? " -> " + JSON.stringify(res.response) : ""}`;
            }
            case "expect": {
                const a = resolveAttribute(s.cluster, s.attribute);
                const key = `${ep}/${clusterName(resolveCluster(s.cluster).id)}/${a.name}`;
                const within = parseDuration(s.within, 2000);
                const t0 = Date.now();
                let lastErr = "no value";
                if (s.via === "report") {
                    // Require an attribute REPORT (subscription) carrying the expected value, seen since the last invoke/write.
                    if (!this.sub) throw new Error("via: report needs the scenario subscription (do not set subscribe: false)");
                    const matches = rec =>
                        rec.kind === "attribute" && `${rec.endpoint}/${rec.cluster}/${rec.attribute}` === key && checkExpectation(s, rec.value) === null;
                    const past = this.history.find(h => h.at >= this.lastActionAt && matches(h.rec));
                    const hit = past?.rec ?? (await this.waitFor(matches, within));
                    if (!hit) throw new Error(`expect ${key}: no matching attribute report within ${within} ms`);
                    return `${key} report received ${Math.max(0, (past?.at ?? Date.now()) - this.lastActionAt)} ms after last action (value ${JSON.stringify(hit.value)})`;
                }
                // Poll with direct reads; also wake early on any matching subscription report.
                for (;;) {
                    let v;
                    try {
                        v = this.sub && s.via !== "read" && this.lastAttr.has(key) && s.fresh !== false ? this.lastAttr.get(key) : (await this.h.read(this.nodeId, ep, s.cluster, s.attribute)).value;
                    } catch (e) {
                        lastErr = e.message;
                    }
                    if (v !== undefined) {
                        const err = checkExpectation(s, v);
                        if (!err) return `${key} ok after ${Date.now() - t0} ms (value ${JSON.stringify(pick(v, s.path ?? s.field))})`;
                        lastErr = err;
                        // The cache may be stale relative to the device: verify against a real read before sleeping.
                        if (this.sub && this.lastAttr.has(key) && s.via !== "report") {
                            const rv = (await this.h.read(this.nodeId, ep, s.cluster, s.attribute)).value;
                            const err2 = checkExpectation(s, rv);
                            if (!err2) return `${key} ok after ${Date.now() - t0} ms (read; value ${JSON.stringify(pick(rv, s.path ?? s.field))})`;
                            lastErr = err2;
                        }
                    }
                    const left = within - (Date.now() - t0);
                    if (left <= 0) throw new Error(`expect ${key}: ${lastErr} (waited ${within} ms)`);
                    await this.waitFor(rec => rec.kind === "attribute" && `${rec.endpoint}/${rec.cluster}/${rec.attribute}` === key, Math.min(300, left));
                }
            }
            case "expect_event": {
                const ev = resolveEvent(s.cluster, s.event);
                const within = parseDuration(s.within, 5000);
                const cname = clusterName(resolveCluster(s.cluster).id);
                const hit = await this.waitFor(
                    rec => rec.kind === "event" && rec.cluster === cname && rec.event === ev.name && (ep === undefined || rec.endpoint === Number(ep)) && (!s.data || checkExpectation({ equals: s.data }, rec.data) === null || Object.entries(s.data).every(([k, v]) => deepEqual(rec.data?.[k], v))),
                    within,
                );
                if (!hit) throw new Error(`expect_event ${cname}.${ev.name}: not seen within ${within} ms`);
                return `${cname}.${ev.name} seen: ${JSON.stringify(hit.data)}`;
            }
        }
    }
}

/** Load a scenario file (YAML, possibly several `---` documents, or JSON). Returns a list of scenarios. */
export function loadScenarios(file) {
    const text = readFileSync(file, "utf8");
    const docs = file.endsWith(".json")
        ? [JSON.parse(text)]
        : parseAllDocuments(text).map(d => {
              if (d.errors.length) throw new Error(`${file}: ${d.errors[0].message}`);
              return d.toJS();
          });
    return docs
        .filter(d => d !== null && d !== undefined)
        .map((doc, i) => {
            if (!Array.isArray(doc.steps)) throw new Error(`${file} (document ${i + 1}): scenario needs a "steps" list`);
            return { name: doc.name ?? `${basename(file)}#${i + 1}`, ...doc };
        });
}

/** Run scenario files against a node. Returns true if everything passed (skips do not fail). */
export async function runScenarioFiles(h, nodeId, files, { junit, vars: cliVars = {} } = {}) {
    const dump = await h.dump(nodeId);
    const has = (ep, cluster) => {
        const c = resolveCluster(cluster);
        return dump.data[String(ep)]?.[hex(c.id, 4)] !== undefined;
    };
    const results = [];
    // One subscription for the whole run (PairedNode keeps the callbacks of its first connect).
    let runner;
    const sub = await h.subscribe(nodeId, { onRecord: rec => runner?.onRecord(rec) });
    for (const [file, sc] of files.flatMap(f => loadScenarios(f).map(sc => [f, sc]))) {
        const vars = { ...(sc.vars ?? {}), ...cliVars };
        const missing = substitute(sc.requires ?? [], vars).filter(r => !has(r.ep ?? r.endpoint ?? vars.ep, r.cluster));
        const res = { file, name: sc.name, steps: [], status: "pass" };
        results.push(res);
        if (missing.length) {
            res.status = "skip";
            res.reason = `device lacks ${missing.map(m => `ep${m.ep ?? m.endpoint ?? vars.ep}/${m.cluster}`).join(", ")}`;
            log(`SKIP ${sc.name}: ${res.reason}`);
            continue;
        }
        log(`RUN  ${sc.name}`);
        runner = new Runner(h, nodeId, sc.subscribe === false ? undefined : sub, vars);
        const total = (sc.setup?.length ?? 0) + sc.steps.length;
        try {
            for (const [i, raw] of [...(sc.setup ?? []), ...sc.steps].entries()) {
                const t0 = Date.now();
                try {
                    const msg = await runner.step(raw);
                    res.steps.push({ i, ok: true, ms: Date.now() - t0, msg });
                    log(`  [${i + 1}/${total}] ok   ${msg}`);
                } catch (e) {
                    res.steps.push({ i, ok: false, ms: Date.now() - t0, msg: e.message });
                    log(`  [${i + 1}/${total}] FAIL ${e.message}`);
                    res.status = "fail";
                    break;
                }
            }
        } finally {
            // cleanup steps always run
            for (const raw of sc.cleanup ?? []) {
                try {
                    await runner.step(raw);
                } catch (e) {
                    log(`  cleanup step failed: ${e.message}`);
                }
            }
        }
        log(`${res.status === "pass" ? "PASS" : "FAIL"} ${sc.name}`);
    }
    await sub.stop();
    const pass = results.filter(r => r.status === "pass").length;
    const fail = results.filter(r => r.status === "fail").length;
    const skip = results.filter(r => r.status === "skip").length;
    log(`scenarios: ${pass} passed, ${fail} failed, ${skip} skipped`);
    if (junit) writeFileSync(junit, junitXml(results));
    return fail === 0;
}

const esc = s => String(s).replace(/[<>&"]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" })[c]);
function junitXml(results) {
    const cases = results
        .map(r => {
            const t = r.steps.reduce((a, s) => a + s.ms, 0) / 1000;
            const body = r.status === "fail" ? `<failure message="${esc(r.steps.at(-1)?.msg ?? "")}"/>` : r.status === "skip" ? `<skipped message="${esc(r.reason)}"/>` : "";
            return `  <testcase classname="matter-harness" name="${esc(r.name)}" time="${t}">${body}</testcase>`;
        })
        .join("\n");
    return `<?xml version="1.0"?>\n<testsuite name="matter-harness" tests="${results.length}" failures="${results.filter(r => r.status === "fail").length}" skipped="${results.filter(r => r.status === "skip").length}">\n${cases}\n</testsuite>\n`;
}
