# Development

## Run the tests

The tests use fake input devices and a fake Home Assistant, so no macropad or Home Assistant is needed. They do need
`evdev` installed, which compiles against the Linux kernel input headers:

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Lint and format with [ruff](https://docs.astral.sh/ruff/) (configured in `pyproject.toml`, including its security
rules):

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
```

Or run the tests inside the image exactly as CI does:

```bash
docker buildx build --target test .
```

## Build the image

```bash
docker build -t homeassistant-macropad-bridge:dev .
```

`Dockerfile` compiles `evdev` into a wheel in a build stage, so the runtime image only contains Python and the
application; pip is removed after installing. The `test` stage runs the unit tests on top of the runtime image.
Multi-platform builds (`linux/amd64,linux/arm64,linux/arm/v7`) need a Buildx builder with the `docker-container` driver
and QEMU.

## Code layout

| Path | Contents |
| --- | --- |
| `macropad_bridge.py` | Configuration, device discovery, the key-event reader and the Home Assistant client |
| `healthcheck.py` | Docker `HEALTHCHECK`: healthy while the heartbeat file is newer than 30 seconds |
| `tests/` | Unit tests |

## Continuous integration and releases

All changes reach `main` through a pull request; the **Lint** and **Test** checks (ruff, and unit tests on every
platform) must pass and merges are squashed.

- **Every build of `main`** is published as `ghcr.io/scottfridwin/homeassistant-macropad-bridge:main` and
  `:sha-<commit>`, with an SBOM, BuildKit provenance and a GitHub build provenance attestation.
- **Vulnerability scanning** — Trivy scans each new `:main` image and the released `:latest` image weekly. Fixable HIGH
  and CRITICAL findings appear under **Security → Code scanning**; they do not block builds.
- **Dependencies** — the Python base image (pinned by digest), the Python packages and the GitHub Actions (pinned to
  commit SHAs) are updated by [Renovate](https://docs.renovatebot.com/). Updates wait 3 days after publication, then
  merge automatically once **Test** passes.
- **Releases** are created automatically when a merge to `main` changes `Dockerfile`, `requirements.txt`,
  `macropad_bridge.py` or `healthcheck.py`. The version comes from the commit subject: `feat:` bumps the minor version,
  `<type>!:` or a `BREAKING CHANGE` footer bumps the major version, anything else (including Renovate updates) bumps the
  patch version. A release tags the image that was just published as `X.Y.Z`, `X.Y`, `X` and `latest`. To release
  manually, run the **Build, Test & Publish** workflow with a `release_tag` such as `v1.2.0`.
- If an unattended run on `main` fails, an issue titled **CI failed on main** is opened.
