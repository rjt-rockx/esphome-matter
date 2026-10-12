#!/usr/bin/env node
// A matter.js virtual device (On/Off light on endpoint 1 + temperature sensor on endpoint 2).
//
// Used by `selftest`, but also handy by itself: it gives you a commissionable Matter device without
// hardware, e.g. to try out scenarios or to see what the harness expects. Needs a working IPv6 stack
// when run standalone (matter.js requires UDP/IPv6); selftest can instead run it on matter.js'
// in-memory network simulator (see selftest.mjs).
//
//   node src/virtual-device.mjs --storage /tmp/vdev --port 5541 --passcode 20202021 --discriminator 3840
//
// stdin control lines (so a parent process can drive the device):
//   onoff 0|1       set OnOff on endpoint 1 from the device side
//   temp <centi-C>  set TemperatureMeasurement.measuredValue on endpoint 2 (e.g. "temp 2150")
//   quit
// stdout: one JSON object per line ({"event":"ready", ...}).
import { fileURLToPath } from "node:url";
import { parseArgs } from "./util.mjs";

/**
 * Create and start the virtual device. `environment` defaults to Environment.default.
 * Returns { node, light, temp, manualPairingCode, qrPairingCode, setOnOff, setTemperature, close }.
 */
export async function startVirtualDevice({
    environment,
    port = 5541,
    passcode = 20202021,
    discriminator = 3840,
    id = "harness-vdev",
    onEvent,
} = {}) {
    const { Endpoint, Environment, ServerNode, VendorId } = await import("@matter/main");
    const { OnOffLightDevice } = await import("@matter/main/devices/on-off-light");
    const { TemperatureSensorDevice } = await import("@matter/main/devices/temperature-sensor");
    const node = await ServerNode.create({
        id,
        environment: environment ?? Environment.default,
        network: { port },
        commissioning: { passcode, discriminator },
        productDescription: { name: "Harness Virtual Device", deviceType: OnOffLightDevice.deviceType },
        basicInformation: {
            vendorName: "esphome-matter-harness",
            vendorId: VendorId(0xfff1),
            nodeLabel: "Harness Virtual Device",
            productName: "Virtual Light+Temp",
            productLabel: "Virtual Light+Temp",
            productId: 0x8000,
            serialNumber: "harness-0001",
            uniqueId: "harness-0001",
        },
    });
    const light = new Endpoint(OnOffLightDevice, { id: "light" });
    const temp = new Endpoint(TemperatureSensorDevice, {
        id: "temp",
        temperatureMeasurement: { measuredValue: 2000, minMeasuredValue: -4000, maxMeasuredValue: 12500 },
    });
    await node.add(light);
    await node.add(temp);
    light.events.onOff.onOff$Changed.on(value => onEvent?.({ event: "onoff", value }));
    await node.start();
    const pairing = node.state.commissioning.pairingCodes;
    return {
        node,
        light,
        temp,
        port,
        passcode,
        discriminator,
        manualPairingCode: pairing.manualPairingCode,
        qrPairingCode: pairing.qrPairingCode,
        setOnOff: onOff => light.set({ onOff: { onOff } }),
        setTemperature: v => temp.set({ temperatureMeasurement: { measuredValue: v } }),
        close: () => node.close(),
    };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
    const args = parseArgs(process.argv.slice(2));
    // matter.js reads process.argv for environment variables; keep it clean.
    process.argv = process.argv.slice(0, 2);
    (await import("./ipv6-shim.mjs")).installIpv6ShimIfNeeded();
    await import("@matter/nodejs");
    const { Environment } = await import("@matter/main");
    Environment.default.vars.set("path.root", args.storage ?? "./.vdev-state");
    const emit = obj => process.stdout.write(JSON.stringify(obj) + "\n");
    const dev = await startVirtualDevice({
        environment: Environment.default,
        port: Number(args.port ?? 5541),
        passcode: Number(args.passcode ?? 20202021),
        discriminator: Number(args.discriminator ?? 3840),
        onEvent: emit,
    });
    process.stdin.setEncoding("utf8");
    let buf = "";
    process.stdin.on("data", async chunk => {
        buf += chunk;
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
            const [cmd, arg] = buf.slice(0, nl).trim().split(/\s+/);
            buf = buf.slice(nl + 1);
            try {
                if (cmd === "onoff") await dev.setOnOff(arg === "1" || arg === "true");
                else if (cmd === "temp") await dev.setTemperature(Number(arg));
                else if (cmd === "quit") {
                    await dev.close();
                    process.exit(0);
                }
            } catch (e) {
                emit({ event: "error", message: String(e?.message ?? e) });
            }
        }
    });
    process.on("SIGTERM", async () => {
        await dev.close().catch(() => {});
        process.exit(0);
    });
    emit({
        event: "ready",
        port: dev.port,
        passcode: dev.passcode,
        discriminator: dev.discriminator,
        manualPairingCode: dev.manualPairingCode,
        qrPairingCode: dev.qrPairingCode,
        commissioned: dev.node.state.commissioning.commissioned,
    });
}
