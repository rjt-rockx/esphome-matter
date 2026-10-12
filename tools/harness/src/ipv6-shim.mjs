// Workaround for hosts whose kernel has no IPv6 at all (e.g. minimal containers/VMs: no
// /proc/net/if_inet6, `socket(AF_INET6)` fails with EAFNOSUPPORT).
//
// matter.js insists on creating a UDP/IPv6 socket for mDNS and for its operational transport and
// treats failure as fatal. This shim replaces such sockets with inert stand-ins (bind succeeds, nothing is
// ever received, sends are dropped) so matter.js starts and keeps working over its IPv4 sockets. This
// only works for peers reachable via IPv4 (device advertises an A record / you pass `--ip <ipv4>`).
// It is a no-op when the host has working IPv6.
import dgram from "node:dgram";
import { EventEmitter } from "node:events";
import { existsSync, readFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";

export function hostHasIpv6() {
    try {
        return existsSync("/proc/net/if_inet6") && readFileSync("/proc/net/if_inet6", "utf8").trim().length > 0;
    } catch {
        return true;
    }
}

class InertUdp6Socket extends EventEmitter {
    #port = 0;
    #bound = false;
    bind(port, address, cb) {
        if (typeof port === "function") [cb, port] = [port, 0];
        this.#port = Number(port) || 40000 + Math.floor(Math.random() * 20000);
        this.#bound = true;
        queueMicrotask(() => {
            this.emit("listening");
            cb?.();
        });
        return this;
    }
    address() {
        if (!this.#bound) throw new Error("ERR_SOCKET_DGRAM_NOT_RUNNING");
        return { address: "::", family: "IPv6", port: this.#port };
    }
    send(...args) {
        const cb = args.find(a => typeof a === "function");
        queueMicrotask(() => cb?.(null));
    }
    close(cb) {
        this.#bound = false;
        queueMicrotask(() => {
            this.emit("close");
            cb?.();
        });
        return this;
    }
    setBroadcast() {}
    setMulticastInterface() {}
    setMulticastTTL() {}
    setMulticastLoopback() {}
    setTTL() {}
    setRecvBufferSize() {}
    setSendBufferSize() {}
    getRecvBufferSize() {
        return 0;
    }
    getSendBufferSize() {
        return 0;
    }
    addMembership() {}
    dropMembership() {}
    ref() {
        return this;
    }
    unref() {
        return this;
    }
    connect() {}
    disconnect() {}
}

let installed = false;

/** Install the shim if (and only if) the host has no IPv6. Returns true when installed. */
export function installIpv6ShimIfNeeded({ force = false } = {}) {
    if (installed) return true;
    if (!force && hostHasIpv6()) return false;
    const original = dgram.createSocket.bind(dgram);
    dgram.createSocket = (options, listener) => {
        const type = typeof options === "string" ? options : options?.type;
        if (type === "udp6") {
            const s = new InertUdp6Socket();
            if (listener) s.on("message", listener);
            return s;
        }
        return original(options, listener);
    };
    syncBuiltinESMExports();
    installed = true;
    return true;
}
