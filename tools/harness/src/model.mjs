// Cluster / attribute / command name resolution on top of matter.js' standard model.
import * as Clusters from "@matter/main/clusters";
import { Matter } from "@matter/main/model";
import { hex, parseIntLoose } from "./util.mjs";

export const GLOBAL_ATTRIBUTES = {
    0xfff8: "GeneratedCommandList",
    0xfff9: "AcceptedCommandList",
    0xfffa: "EventList",
    0xfffb: "AttributeList",
    0xfffc: "FeatureMap",
    0xfffd: "ClusterRevision",
};

// id -> cluster namespace (with `.Complete`, `.id`, `.name`)
const byId = new Map();
const byName = new Map();
for (const value of Object.values(Clusters)) {
    if (value && typeof value === "object" && value.Cluster && typeof value.id === "number" && value.Complete) {
        if (!byId.has(value.id)) {
            byId.set(value.id, value);
            byName.set(value.name.toLowerCase(), value);
        }
    }
}

const lc = s => String(s).toLowerCase();

export function clusterName(id) {
    return Matter.clusters(id)?.name ?? byId.get(id)?.name ?? `Unknown${hex(id, 4)}`;
}

/** Resolve a cluster by hex/decimal id or by (case-insensitive) name such as "OnOff". */
export function resolveCluster(ref) {
    const n = parseIntLoose(ref);
    if (n !== undefined) {
        const c = byId.get(n);
        return { id: n, name: clusterName(n), cluster: c?.Complete };
    }
    const c = byName.get(lc(ref));
    if (!c) throw new Error(`Unknown cluster "${ref}"`);
    return { id: c.id, name: c.name, cluster: c.Complete };
}

function memberOf(clusterId, kind, ref) {
    const model = Matter.clusters(clusterId);
    const n = parseIntLoose(ref);
    const members = model ? model[kind] : [];
    for (const m of members ?? []) {
        if (n !== undefined ? m.id === n : lc(m.name) === lc(ref)) return m;
    }
    return undefined;
}

export function attributeName(clusterId, attrId) {
    if (GLOBAL_ATTRIBUTES[attrId]) return GLOBAL_ATTRIBUTES[attrId];
    return memberOf(clusterId, "attributes", attrId)?.name ?? `Attr${hex(attrId, 4)}`;
}

export function commandName(clusterId, cmdId) {
    return memberOf(clusterId, "commands", cmdId)?.name ?? `Cmd${hex(cmdId, 2)}`;
}

export function eventName(clusterId, evId) {
    return memberOf(clusterId, "events", evId)?.name ?? `Event${hex(evId, 2)}`;
}

/** Resolve an attribute to `{ id, name, attribute }` where attribute is the ClusterType attribute (for writes). */
export function resolveAttribute(clusterRef, ref) {
    const c = resolveCluster(clusterRef);
    const n = parseIntLoose(ref);
    if (n !== undefined) {
        const entry = Object.values(c.cluster?.attributes ?? {}).find(a => a.id === n);
        return { cluster: c, id: n, name: attributeName(c.id, n), attribute: entry };
    }
    const g = Object.entries(GLOBAL_ATTRIBUTES).find(([, name]) => lc(name) === lc(ref));
    if (g) return { cluster: c, id: Number(g[0]), name: g[1], attribute: undefined };
    const entry = Object.values(c.cluster?.attributes ?? {}).find(a => lc(a.name) === lc(ref) || lc(a.schema?.name) === lc(ref));
    if (!entry) throw new Error(`Cluster ${c.name} has no attribute "${ref}"`);
    return { cluster: c, id: entry.id, name: entry.schema?.name ?? entry.name, attribute: entry };
}

/** Resolve a command to `{ id, name, command }` (command is the ClusterType.Command used by invoke). */
export function resolveCommand(clusterRef, ref) {
    const c = resolveCluster(clusterRef);
    const n = parseIntLoose(ref);
    const entry = Object.values(c.cluster?.commands ?? {}).find(cmd =>
        n !== undefined ? cmd.id === n : lc(cmd.name) === lc(ref) || lc(cmd.schema?.name) === lc(ref),
    );
    if (!entry) throw new Error(`Cluster ${c.name} has no command "${ref}"`);
    return { cluster: c, id: entry.id, name: entry.schema?.name ?? entry.name, command: entry };
}

export function resolveEvent(clusterRef, ref) {
    const c = resolveCluster(clusterRef);
    const n = parseIntLoose(ref);
    const entry = Object.values(c.cluster?.events ?? {}).find(ev =>
        n !== undefined ? ev.id === n : lc(ev.name) === lc(ref) || lc(ev.schema?.name) === lc(ref),
    );
    if (!entry) throw new Error(`Cluster ${c.name} has no event "${ref}"`);
    return { cluster: c, id: entry.id, name: entry.schema?.name ?? entry.name, event: entry };
}
