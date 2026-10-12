// Small dependency-free helpers shared by the harness modules.

/** Parse `--key value`, `--key=value` and bare `--flag` arguments. Positional args land in `_`. */
export function parseArgs(argv) {
    const out = { _: [] };
    for (let i = 0; i < argv.length; i++) {
        const a = argv[i];
        if (a.startsWith("--")) {
            const eq = a.indexOf("=");
            if (eq > 0) {
                out[a.slice(2, eq)] = a.slice(eq + 1);
            } else {
                const key = a.slice(2);
                const next = argv[i + 1];
                if (next !== undefined && !(next.startsWith("--") && next.length > 2)) {
                    out[key] = next;
                    i++;
                } else {
                    out[key] = true;
                }
            }
        } else {
            out._.push(a);
        }
    }
    return out;
}

/** Parse "0x1f", "31" or a bare number into an integer, otherwise return undefined. */
export function parseIntLoose(v) {
    if (typeof v === "number") return v;
    if (typeof v !== "string") return undefined;
    const s = v.trim();
    if (/^0x[0-9a-f]+$/i.test(s)) return parseInt(s, 16);
    if (/^\d+$/.test(s)) return parseInt(s, 10);
    return undefined;
}

export const hex = (n, width = 4) => "0x" + Number(n).toString(16).toUpperCase().padStart(width, "0");

export const sleep = ms => new Promise(r => setTimeout(r, ms));

/** Timestamped log line on stderr (stdout is reserved for machine readable output). */
export function log(...args) {
    console.error(`[${new Date().toISOString()}]`, ...args);
}

/**
 * Convert a decoded Matter value into plain JSON: bigint -> number (or decimal string if unsafe),
 * Uint8Array -> "hex:<..>", objects get sorted keys so dumps are stable and diffable.
 */
export function toJson(value) {
    if (value === undefined || value === null) return null;
    if (typeof value === "bigint") {
        return value >= BigInt(Number.MIN_SAFE_INTEGER) && value <= BigInt(Number.MAX_SAFE_INTEGER)
            ? Number(value)
            : value.toString();
    }
    if (value instanceof Uint8Array) return "hex:" + Buffer.from(value).toString("hex");
    if (ArrayBuffer.isView(value)) return toJson(new Uint8Array(value.buffer, value.byteOffset, value.byteLength));
    if (Array.isArray(value)) return value.map(toJson);
    if (typeof value === "object") {
        const out = {};
        for (const k of Object.keys(value).sort()) out[k] = toJson(value[k]);
        return out;
    }
    if (typeof value === "number" && !Number.isFinite(value)) return String(value);
    return value;
}

/** Convert user supplied JSON: "hex:ab12" -> Uint8Array, {"$big":"123"} -> bigint. */
export function fromJson(value) {
    if (typeof value === "string" && value.startsWith("hex:")) {
        return new Uint8Array(Buffer.from(value.slice(4), "hex"));
    }
    if (Array.isArray(value)) return value.map(fromJson);
    if (value && typeof value === "object") {
        if (typeof value.$big === "string") return BigInt(value.$big);
        const out = {};
        for (const [k, v] of Object.entries(value)) out[k] = fromJson(v);
        return out;
    }
    return value;
}

/** Deep equality for JSON-ish values (used by scenario expectations). */
export function deepEqual(a, b) {
    if (a === b) return true;
    if (typeof a === "bigint" || typeof b === "bigint") return String(a) === String(b);
    if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    return ka.every(k => deepEqual(a[k], b[k]));
}
