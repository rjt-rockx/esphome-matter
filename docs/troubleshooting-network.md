# Network troubleshooting: slow reconnect after restart

After a reboot a Matter device has to be found again and its subscriptions
restored before controllers (Home Assistant, Apple Home, ...) show it as
available. This page explains where the time goes, what the component does about
it, and how to measure it. None of this was verified on hardware yet; the
measurement logs below exist so that it can be.

## Measuring

```yaml
matter:
  debug: true
```

This logs the milestones below at INFO level under the tag `matter.timing`
(or use `logger: logs: matter.timing: VERBOSE` without `debug`, for the marks
only; `debug` also enables the subscription and SRP polling):

```
[t=8123 ms, +0 ms since IP up] IP up: IPv6 assigned
[t=8390 ms, +267 ms since IP up] DNS-SD publish: 3F5A...-0000000000000001
[t=9100 ms, +977 ms since IP up] SRP registered: all Matter services registered with the SRP server   (Thread only)
[t=9450 ms, +1327 ms since IP up] Controller resolved: A1B2...-000000000000000F
[t=9800 ms, +1677 ms since IP up] CASE session established: node=0x... fabric=1
[t=9950 ms, +1827 ms since IP up] Subscription active: 1 active subscription(s)
Reconnect summary (ms after IP up): dnssd=267 srp=yes controller_resolved=yes case=1677 subscription=1827
```

Also look for the CHIP line `Resuming N subscriptions in X seconds`. If the
total is dominated by the gap between "Matter server ready" and "CASE session
established", read the next section.

## Where the time goes

1. **Resumption delay (controller controlled).** CHIP resumes persisted
   subscriptions once, after the largest `MinInterval` stored for any of them
   (`InteractionModelEngine::ResumeSubscriptions`). A controller that
   subscribes with `MinInterval` = 60 s makes the device wait about 60 s after
   boot, regardless of how fast the network is. This value is not configurable
   on the device. The debug log prints it on server ready.
2. **Retry after a failed first attempt (device configurable).** If the first
   resumption fails (controller not resolvable yet, Thread still attaching),
   CHIP retries after `300 s + 300 s * Fibonacci(n)` by default, i.e. at least
   five minutes. The component now ships `matter_chip_config.h` (wired in
   through `CONFIG_CHIP_PROJECT_CONFIG`) which uses
   `5 s + 10 s * Fibonacci(n)` capped at 600 s: retries after 5, 15, 15, 25, 35,
   55, 85, 135, 215, 345, 555 s. After the last retry CHIP deletes the persisted
   subscription and the controller re-subscribes by itself when it notices the
   device. Persisting subscriptions (`CONFIG_ENABLE_PERSIST_SUBSCRIPTIONS`) is
   kept enabled; it is what lets controllers skip a full re-subscribe.
3. **DNS-SD advertisement (device).** Controllers find the device through its
   `_matter._tcp` operational record. See below.
4. **Controller side.** Controllers cache the old address and back off after
   failed attempts; this cannot be seen or fixed from the device.

## DNS-SD on Thread

Records are published through the OpenThread SRP client owned by ESPHome's
`openthread` component. Changes:

* A failed publish (OpenThread lock busy, `otSrpClientAddService` error) is no
  longer dropped: the advertisement is retried with exponential backoff
  (1 s doubling up to 30 s) by re-running CHIP's `DnssdServer::StartServer()`.
* Thread connectivity established, Thread role change and address change
  schedule a debounced (500 ms) re-advertisement, skipped while the role is
  detached/disabled. This replaces esp-matter's own Thread re-advertise, which
  stays disabled (`CONFIG_ESP_MATTER_ENABLE_OPENTHREAD=n`) because it would also
  start a second OpenThread stack next to ESPHome's.
* The old clear-then-add on every publish is gone. A publish whose record is
  identical to the one already registered keeps the registration untouched; a
  changed record is cleared and added, and if the add fails the previous
  registration is restored. Previously every `StartServer()` produced a second
  SRP update, and a failed re-add silently dropped a working registration.
* "Published" in the CHIP log only means the record was handed to the SRP
  client. With `debug: true` the SRP state is polled and `SRP registered` is
  logged when the server accepted all records.

Known limitation: stale records are removed with `otSrpClientClearService`, which
does not send an SRP remove; the server drops them when the lease expires.

## DNS-SD on Wi-Fi / Ethernet and the ESPHome hostname

CHIP's `EspDnssdPublishService` calls `mdns_hostname_set()` with Matter's own
host name, replacing the name ESPHome's `mdns` component set (last writer wins).
That can break `<name>.local` for API clients. When a hostname is already set,
the component now publishes with an empty host name so CHIP skips the call; the
SRV records of the Matter services then point to `<name>.local`, which resolves
to the device. If no hostname exists (no ESPHome `mdns`), the old behaviour
applies. Verify with `avahi-browse -rt _matter._tcp` and
`ping <name>.local`.

Operational advertisement on IP assignment already works on Wi-Fi/Ethernet:
esp-matter calls `StartServer()` on `kInterfaceIpAddressChanged`. The component
does not add a second forced call because every `EspDnssdPublishService` removes
and re-adds the service, which restarts mDNS probing.

## Things to check when the device stays unavailable

* `Publish failed` / `OpenThread busy` in the log: the retry path is active; if
  it never succeeds, check that the `openthread` component finished attaching.
* `Resuming N subscriptions in X seconds` with large X: controller-chosen
  `MinInterval`.
* `Failed to establish CASE for subscription-resumption`: the device could not
  resolve or reach the controller; look at `matter.dnssd` "Resolve ... failed".
* Nothing resolves on Thread: the border router must forward mDNS/SRP (see the
  Thread docs of your controller).
