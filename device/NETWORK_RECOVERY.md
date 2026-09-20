# Pi ↔ ASUS network recovery

The camera failed with `No route to host` after switching hotspots because its
launcher retained the ASUS's old DHCP address. Both devices were online, but
on different hotspots. ASUS's API, Elasticsearch, and ComfyUI were healthy.
The existing Tailscale policy allows SSH; direct API port access timed out.

The Pi now connects to ASUS's stable Tailscale address through a dedicated SSH
tunnel. API and library traffic use loopback listeners on the Pi:

- `NIMBUS_API=http://127.0.0.1:18000` → ASUS `127.0.0.1:8000`.
- `NIMBUS_ES_URL=http://127.0.0.1:19200` → ASUS `127.0.0.1:9200`.

`ops/nimbus-asus-tunnel.service` is installed and enabled on the Pi. Keepalives
notice dead connections; systemd retries after five seconds. Both devices need
working network paths to their tailnet, but need not share Wi-Fi or a hotspot.
No firewall or tailnet ACL was widened, and no API listener was exposed by the
new configuration.

The dedicated Pi private key remains only at `~/.ssh/nimbus_asus_tunnel`.
ASUS's authorized public key restricts forwarding to the two service endpoints;
its options are `restrict,port-forwarding,permitopen="127.0.0.1:8000",permitopen="127.0.0.1:9200"`.
The Pi uses a separate known-hosts file copied from an already trusted ASUS host
key; strict host-key checking is enabled. No credentials are stored in this repo.

`~/start_nimbus.sh` exports the local endpoints after sourcing existing
credentials and waits briefly for API readiness. The graphical boot autostart
now invokes that same launcher, rather than duplicating stale DHCP addresses.
The wait is bounded; a prolonged network outage can still cause local-library
fallback and a capture error. This is automatic transport reconnection, not
transaction replay or offline AI generation. A capture in flight during an
outage may need to be retried once connectivity recovers.

## Verified on hardware

- Pi on Adi's hotspot, ASUS on Calvin's: API and Elasticsearch both HTTP 200.
- Terminated the tunnel's main process: systemd restarted it (`NRestarts=1`),
  and API readiness recovered to HTTP 200.
- Camera restarted using Elasticsearch over the tunnel.

Capture acceptance is recorded separately with the live test results. Reboot
and a full physical Wi-Fi outage have not been tested.

## Rollback

Backups on Pi are under `~/frame-pacing-test-20260920/`:
`start-before-network.sh` and `autostart-before-network`. Restore those only if
the previous network endpoint is reachable. Disable the tunnel with
`sudo systemctl disable --now nimbus-asus-tunnel`. Remove only the dedicated
`nimbus-pi-api-tunnel` authorized-key entry on ASUS if decommissioning it.
