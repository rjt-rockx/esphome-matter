// Controller wrapper: opens a persistent matter.js CommissioningController and implements the
// operations the CLI / scenario runner / selftest need.
import { resolve } from "node:path";
import { homedir } from "node:os";
import { clusterName, attributeName, eventName, resolveAttribute, resolveCommand, resolveCluster, GLOBAL_ATTRIBUTES } from "./model.mjs";
import { installIpv6ShimIfNeeded } from "./ipv6-shim.mjs";
import { fromJson, hex, log, parseIntLoose, sleep, toJson } from "./util.mjs";

export const ipv6ShimActive = installIpv6ShimIfNeeded({ force: process.env.HARNESS_IPV6_SHIM === "1" });

const { Environment, Seconds } = await import("@matter/main");
const { ManualPairingCodeCodec, QrPairingCodeCodec, NodeId, ClusterId, EndpointNumber, AttributeId } = await import("@matter/main/types");
const { GeneralCommissioning } = await import("@matter/main/clusters");
const { CommissioningController, NodeStates, NodeStateInformation } = await import("@project-chip/matter.js").then(
    async m => ({ ...m, ...(await import("@project-chip/matter.js/device")) }),
);
const { NodeDiscoveryType } = await import("@project-chip/matter.js/cluster");
await import("@matter/nodejs");
const { Logger } = await import("@matter/main");
Logger.level = process.env.HARNESS_LOG ?? "error";

export const DEFAULT_STATE_DIR = process.env.HARNESS_STATE ?? resolve(process.cwd(), ".harness-state");

/** Decode an 11/21 digit manual pairing code or an MT: QR payload. */
export function parsePairingCode(code) {
    const c = String(code).trim();
    if (c.toUpperCase().startsWith("MT:")) {
        const [payload] = QrPairingCodeCodec.decode(c.toUpperCase());
        return { passcode: payload.passcode, longDiscriminator: payload.discriminator, vendorId: payload.vendorId, productId: payload.productId };
    }
    const digits = c.replace(/[-\s]/g, "");
    const d = ManualPairingCodeCodec.decode(digits);
    return { passcode: d.passcode, shortDiscriminator: d.shortDiscriminator, vendorId: d.vendorId, productId: d.productId };
}

/**
 * Open (and start) the controller. State lives in `stateDir` (matter.js storage) unless an explicit
 * `environment` is given (used by selftest for the in-memory network simulator).
 */
export async function openController({ stateDir = DEFAULT_STATE_DIR, environment, id = "harness", label = "esphome-matter-harness" } = {}) {
    let env = environment;
    if (!env) {
        env = Environment.default;
        env.vars.set("path.root", resolve(stateDir.replace(/^~/, homedir())));
    }
    const subscriptions = [];
    const controller = new CommissioningController({
        environment: { environment: env, id },
        autoConnect: false,
        adminFabricLabel: label,
    });
    await controller.start();
    return new Harness(controller, subscriptions);
}

export class Harness {
    constructor(controller, subscriptions) {
        this.controller = controller;
        this.subscriptions = subscriptions;
        this.nodes = new Map(); // nodeId -> PairedNode (only for subscribe)
    }

    commissionedNodeIds() {
        return this.controller.getCommissionedNodes().map(Number);
    }

    /** Pick the node id: explicit, or the only commissioned one. */
    resolveNodeId(node) {
        const ids = this.commissionedNodeIds();
        if (node === undefined || node === true) {
            if (ids.length === 1) return ids[0];
            throw new Error(`--node required (commissioned nodes: ${ids.join(", ") || "none"})`);
        }
        const n = parseIntLoose(String(node));
        if (n === undefined) throw new Error(`Bad node id "${node}"`);
        if (!ids.includes(n)) throw new Error(`Node ${n} is not commissioned (known: ${ids.join(", ") || "none"})`);
        return n;
    }

    /**
     * Commission a device that is in commissioning mode.
     * With `ip` (and `port`) the device is contacted at that address directly; otherwise it is
     * located via mDNS using the discriminator from the pairing code.
     */
    async commission({ code, ip, port = 5540, timeoutSeconds = 120, nodeId }) {
        const pc = parsePairingCode(code);
        const discovery = {
            identifierData:
                pc.longDiscriminator !== undefined
                    ? { longDiscriminator: pc.longDiscriminator }
                    : { shortDiscriminator: pc.shortDiscriminator },
            discoveryCapabilities: { onIpNetwork: true },
            timeout: Seconds(timeoutSeconds),
        };
        if (ip) discovery.knownAddress = { ip, port: Number(port), type: "udp" };
        const opts = {
            commissioning: {
                regulatoryLocation: GeneralCommissioning.RegulatoryLocationType.IndoorOutdoor,
                regulatoryCountryCode: "XX",
                ...(nodeId !== undefined ? { nodeId: NodeId(BigInt(nodeId)) } : {}),
            },
            discovery,
            passcode: pc.passcode,
        };
        const id = await this.controller.commissionNode(opts, { connectNodeAfterCommissioning: false });
        return Number(id);
    }

    async client(nodeId, discovery = NodeDiscoveryType.TimedDiscovery) {
        return this.controller.createInteractionClient(NodeId(BigInt(nodeId)), discovery, { discoveryTimeout: Seconds(30) });
    }

    /** Wildcard read of everything. Returns DecodedAttributeReportValue[] */
    async readAll(nodeId) {
        const ic = await this.client(nodeId);
        return ic.getAllAttributes({ isFabricFiltered: false });
    }

    /** Wildcard read -> stable JSON document. */
    async dump(nodeId) {
        const reports = await this.readAll(nodeId);
        const data = {};
        const clusterNames = {};
        const attrNames = {};
        for (const r of reports) {
            const { endpointId, clusterId, attributeId } = r.path;
            const ep = String(endpointId);
            const cid = hex(clusterId, 4);
            const aid = hex(attributeId, 4);
            ((data[ep] ??= {})[cid] ??= {})[aid] = toJson(r.value);
            clusterNames[cid] = clusterName(clusterId);
            (attrNames[cid] ??= {})[aid] = attributeName(clusterId, attributeId);
        }
        const sortObj = o => Object.fromEntries(Object.keys(o).sort((a, b) => (isNaN(a) || isNaN(b) ? a.localeCompare(b) : a - b)).map(k => [k, o[k]]));
        const sorted = {};
        for (const ep of Object.keys(data).sort((a, b) => a - b)) {
            sorted[ep] = {};
            for (const cid of Object.keys(data[ep]).sort()) sorted[ep][cid] = sortObj(data[ep][cid]);
        }
        const sortNames = o => Object.fromEntries(Object.keys(o).sort().map(k => [k, typeof o[k] === "object" ? sortNames(o[k]) : o[k]]));
        return { nodeId, data: sorted, names: { clusters: sortNames(clusterNames), attributes: sortNames(attrNames) } };
    }

    /** Read one attribute -> { value } (value is JSON-ified). */
    async read(nodeId, ep, clusterRef, attrRef) {
        const c = resolveCluster(clusterRef);
        const a = resolveAttribute(clusterRef, attrRef);
        const ic = await this.client(nodeId);
        const res = await ic.getMultipleAttributes({
            attributes: [{ endpointId: EndpointNumber(Number(ep)), clusterId: ClusterId(c.id), attributeId: AttributeId(a.id) }],
            isFabricFiltered: false,
        });
        if (!res.length) throw new Error(`No data for ${ep}/${c.name}/${a.name} (unsupported attribute?)`);
        return { endpoint: Number(ep), cluster: c.name, attribute: a.name, value: toJson(res[0].value) };
    }

    async write(nodeId, ep, clusterRef, attrRef, value) {
        const a = resolveAttribute(clusterRef, attrRef);
        if (!a.attribute) throw new Error(`Cannot write attribute ${a.name} of ${a.cluster.name}`);
        const ic = await this.client(nodeId);
        await ic.setAttribute({
            attributeData: {
                endpointId: EndpointNumber(Number(ep)),
                clusterId: ClusterId(a.cluster.id),
                attribute: a.attribute,
                value: fromJson(value),
            },
        });
        return { ok: true };
    }

    async invoke(nodeId, ep, clusterRef, cmdRef, args = {}) {
        const c = resolveCommand(clusterRef, cmdRef);
        const ic = await this.client(nodeId);
        const res = await ic.invoke({
            endpointId: EndpointNumber(Number(ep)),
            clusterId: ClusterId(c.cluster.id),
            command: c.command,
            request: args && Object.keys(args).length ? fromJson(args) : undefined,
        });
        return { ok: true, command: `${c.cluster.name}.${c.name}`, response: toJson(res) };
    }

    async remove(nodeId) {
        await this.controller.removeNode(NodeId(BigInt(nodeId)), true);
        return { removed: nodeId };
    }

    /**
     * Subscribe to everything on a node via a PairedNode (which also takes care of re-subscribing after
     * the device went away) and log every attribute report / event / connection state transition.
     * `onRecord(rec)` receives { t (ms since epoch), dt (ms since start), kind, ... }.
     * Returns { stop(), records }.
     */
    async subscribe(nodeId, { onRecord, minInterval = 0, maxInterval = 60 } = {}) {
        const start = Date.now();
        const records = [];
        const push = rec => {
            const now = Date.now();
            const full = { ts: new Date(now).toISOString(), dtMs: now - start, ...rec };
            records.push(full);
            onRecord?.(full);
        };
        const stateNames = {
            [NodeStateInformation.Connected]: "connected",
            [NodeStateInformation.Disconnected]: "disconnected",
            [NodeStateInformation.Reconnecting]: "reconnecting",
            [NodeStateInformation.WaitingForDeviceDiscovery]: "waitingForDeviceDiscovery",
            [NodeStateInformation.StructureChanged]: "structureChanged",
            [NodeStateInformation.Decommissioned]: "decommissioned",
        };
        const id = NodeId(BigInt(nodeId));
        const node = await this.controller.connectNode(id, {
            autoSubscribe: true,
            subscribeMinIntervalFloorSeconds: minInterval,
            subscribeMaxIntervalCeilingSeconds: maxInterval,
            attributeChangedCallback: (_n, d) =>
                push({
                    kind: "attribute",
                    endpoint: Number(d.path.endpointId),
                    cluster: clusterName(d.path.clusterId),
                    attribute: attributeName(d.path.clusterId, d.path.attributeId),
                    path: `${d.path.endpointId}/${hex(d.path.clusterId)}/${hex(d.path.attributeId)}`,
                    value: toJson(d.value),
                }),
            eventTriggeredCallback: (_n, d) =>
                push({
                    kind: "event",
                    endpoint: Number(d.path.endpointId),
                    cluster: clusterName(d.path.clusterId),
                    event: eventName(d.path.clusterId, d.path.eventId),
                    data: toJson(d.events),
                }),
            stateInformationCallback: (_n, s) => push({ kind: "state", state: stateNames[s] ?? String(s) }),
        });
        this.nodes.set(Number(nodeId), node);
        if (!node.initialized) await node.events.initialized;
        push({ kind: "state", state: "initialized" });
        return {
            records,
            node,
            stop: async () => {
                try {
                    await this.controller.disconnectNode(id, true);
                } catch {
                    /* ignore */
                }
            },
        };
    }

    async close() {
        await this.controller.close();
    }
}

export { sleep, log, Seconds, NodeStates };
