# Home Assistant Macropad Bridge

[![Build](https://img.shields.io/github/actions/workflow/status/scottfridwin/homeassistant-macropad-bridge/build.yml?branch=main&label=build)](https://github.com/scottfridwin/homeassistant-macropad-bridge/actions/workflows/build.yml)
[![Release](https://img.shields.io/github/v/release/scottfridwin/homeassistant-macropad-bridge)](https://github.com/scottfridwin/homeassistant-macropad-bridge/releases/latest)
[![Image](https://img.shields.io/badge/image-ghcr.io-blue?logo=docker)](https://github.com/scottfridwin/homeassistant-macropad-bridge/pkgs/container/homeassistant-macropad-bridge)

Turn a USB macropad (or any USB keyboard) into a [Home Assistant](https://www.home-assistant.io/) remote. Every key
press is sent to Home Assistant as an event that your automations can react to, for example to change the volume or
skip a track.

> [!NOTE]
> **AI disclosure:** This project is built and maintained with substantial help from AI coding assistants (GitHub Copilot). AI is used to write and modify the code, tests, documentation and CI configuration, and to manage the repository. Dependency updates are merged and released automatically, without human review, when the automated tests pass. Review the code and test it in your own environment before relying on it.

## Features

- **Home Assistant events** — each key press fires a `macropad_key` event (name configurable) through the REST API
- **Survives unplugging** — the macropad can be unplugged and plugged back in at any time; the bridge picks it up again
- **Listens to every interface** — macropads often expose several keyboard interfaces; all of them are read
- **Multi-architecture image** for `amd64`, `arm64` and `arm/v7` (Raspberry Pi)
- **Secure by default** — runs as non-root with a read-only filesystem, no Linux capabilities and read-only access to
  input devices only; the Home Assistant token can be a Docker secret
- **Health check** — Docker marks the container unhealthy if the bridge stops working
- **Verifiable images** — signed build provenance and an SBOM for every image

## Requirements

- A Linux host with Docker and the macropad plugged in
- A Home Assistant [long-lived access token](https://www.home-assistant.io/docs/authentication/#your-account-profile)
- The macropad's USB vendor and product ID. Find them with `ls /dev/input/by-id/`: a device named
  `usb-1189_8890-event-kbd` has vendor `1189` and product `8890`.

## Quick start

1. Save the Home Assistant token to a file that only you can read:

   ```bash
   mkdir -p secrets && install -m 600 /dev/null secrets/ha_token && read -rsp "Token: " t && printf '%s' "$t" > secrets/ha_token && unset t
   ```

2. Look up the host's `input` group ID with `getent group input | cut -d: -f3` and put it in `group_add` below.

3. Create `docker-compose.yml` (also in this repository):

   ```yaml
   secrets:
     ha_token:
       file: ./secrets/ha_token

   services:
     macropad:
       image: ghcr.io/scottfridwin/homeassistant-macropad-bridge:1
       container_name: macropad-bridge
       group_add:
         - "105" # the host's input group
       read_only: true
       tmpfs:
         - /tmp
       cap_drop:
         - ALL
       security_opt:
         - no-new-privileges:true
       volumes:
         - /dev/input:/dev/input:ro
       device_cgroup_rules:
         - "c 13:* r" # read-only access to input devices only
       environment:
         - HA_URL=http://homeassistant.local:8123
         - HA_TOKEN_FILE=/run/secrets/ha_token
         - MACROPAD_VID=1189
         - MACROPAD_PID=8890
         - DEVICE_NAME=office_macropad
       secrets:
         - ha_token
       restart: unless-stopped
   ```

4. Start it with `docker compose up -d` and check `docker compose logs`. You should see one `Listening on ...` line per
   keyboard interface.

The token file must be readable by the container's user (UID `1000` unless you set `user:`).

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `HA_URL` | yes | | Home Assistant URL, for example `http://homeassistant.local:8123` |
| `HA_TOKEN_FILE` | one of | | Path to a file containing the long-lived access token (preferred) |
| `HA_TOKEN` | one of | | The token itself |
| `MACROPAD_VID` | yes | | USB vendor ID as shown in `/dev/input/by-id` |
| `MACROPAD_PID` | yes | | USB product ID as shown in `/dev/input/by-id` |
| `DEVICE_NAME` | no | `macropad` | Name sent with each event, to tell several macropads apart |
| `EVENT_TYPE` | no | `macropad_key` | Home Assistant event type |
| `RESCAN_SECONDS` | no | `5` | How often to look for the macropad after it was unplugged |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

## Home Assistant

Each key press fires an event with this data:

```json
{
  "event_type": "macropad_key",
  "event_data": {
    "key": "KEY_VOLUMEUP",
    "device": "office_macropad",
    "source": "/dev/input/by-id/usb-1189_8890-if02-event-kbd"
  }
}
```

The key details are nested under `event_data`, so templates read them as `trigger.event.data.event_data.key`. Keys
with more than one name are joined with `_` (for example `KEY_MIN_INTERESTING_KEY_MUTE`), so match with `in` rather
than `==`:

```yaml
triggers:
  - trigger: event
    event_type: macropad_key
actions:
  - choose:
      - conditions: "{{ 'KEY_VOLUMEUP' in trigger.event.data.event_data.key }}"
        sequence:
          - action: media_player.volume_up
            target:
              entity_id: media_player.living_room
```

Watch the events live in **Developer tools → Events** by listening to `macropad_key`.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| *Macropad 1189:8890 not found, waiting* | Wrong IDs, or `/dev/input` is not mounted. Compare with `ls /dev/input/by-id/` on the host. |
| *Cannot open ...: Permission denied* | The container is not in the host's `input` group (`group_add`) or the `device_cgroup_rules` entry is missing. |
| *Could not send ... to Home Assistant* | `HA_URL` is unreachable from the container, or the token is wrong (HTTP 401). |
| Key presses also reach other programs | The bridge only listens; it does not take the macropad away from the desktop. |

## Security

See [SECURITY.md](SECURITY.md) for supported versions, reporting vulnerabilities and verifying images.
