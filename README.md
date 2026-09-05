# nextendo-acnh-designs

![Python](https://img.shields.io/badge/Python-blue) ![Status](https://img.shields.io/badge/status-active-brightgreen) ![Private](https://img.shields.io/badge/-private-grey)

Private, self-hosted backend for the Animal Crossing: New Horizons Custom Designs
Portal (3.0.3), running via
[Ryujinx-Nextendo](https://github.com/NextendoNetwork/Ryujinx-Nextendo). Goal: stop
seeing `MO-0000-0000-0000`/`MA-0000-0000-0000` and actually be able to upload/download
designs. Personal project, not affiliated with Nintendo or the Nextendo Network.

## Current state

- ✅ The server (`services/acnh-designs`) implements the entire known API surface of
  the 3.0.3 client — auth, profile status, messages, user profile/land/icon, "resort
  planner" profile, `design_players` identity, design upload/list/download/delete.
  16 automated tests, all passing.
- ✅ Design upload and download **work** and persist (validated in-game:
  `MO-1F0V-HWR5-JTC2` and two more codes).
- ⚠️ **Creator ID (`MA-…`) still gets stuck at zero.** No longer a server-side
  problem — it's a client-side onboarding window that only exists once per save. See
  [`docs/save-flags.md`](docs/save-flags.md) for the identified cause and the exact
  next step.
- 🧩 A two-file client patch (`patches/`) fixes unstable account identity across
  restarts and DNS routing to the portal host — doesn't fix the Creator ID on its
  own.

## Structure

```
services/acnh-designs/   HTTP/MessagePack server (Python, stdlib + msgpack + cryptography)
patches/                 2-file diff for Ryujinx-Nextendo + application instructions
tools/                   inspection and reset (only the two onboarding flags) for the ACNH save
docs/
  protocol.md                    the entire reverse-engineered API, endpoint by endpoint
  save-flags.md                  why the Creator ID gets stuck at zero, and the concrete next step
  nextendo-network-integration.md  feasibility of integrating this into the Nextendo Network org
```

## Running the server

```bash
cd services/acnh-designs
python -m pip install -r requirements.txt
python -m unittest test_server -v          # 16 tests, no external dependencies

export ACNH_DESIGNS_AUTH_SECRET="<at least 32 random bytes>"
python server.py --host 0.0.0.0 --port 443 --certfile portal.pem --keyfile portal-key.pem
```

On the Ryujinx-Nextendo side, point `NEXTENDO_ACNH_DESIGNS_IP` at the service (or use
an exact entry in `sdcard/atmosphere/hosts/default.txt` pointing
`api.hac.lp1.acbaa.srv.nintendo.net` at the service's IP). Without that variable, the
host falls back to Nextendo's default route (`NEXTENDO_SERVER_IP`).

## How this came about

Rebuilt from a real debugging session — over two hours going back and forth between
capturing the in-game error, reading the service log, and fixing exactly the next
field the client rejected (code alphabet, `display_id`, `meta` format, download
envelope, token `expire_at`, etc. — each documented in
[`docs/protocol.md`](docs/protocol.md)). The code and tests came directly from the
prototype validated in that session; the documentation and the Creator ID root-cause
analysis are the new part, written after digging through the project's actual state
(the SQLite database with the request trail, git diffs against upstream, and the save
offsets already tested) instead of repeating trial and error.

## Disclaimer

Interoperates with a Nintendo online service via dynamic reverse engineering, for
personal use with owned hardware/software. Redistributes no binary or code extracted
from the game — the save tools only read/write two event flags publicly documented by
the NHSE project, and the emulator patch is a text diff to apply over one's own clone
of Ryujinx-Nextendo, not a copy of its source tree.
