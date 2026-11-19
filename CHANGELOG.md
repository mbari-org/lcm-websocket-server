# CHANGELOG

<!-- version list -->

## v0.9.0-rc.2 (2026-11-19)

### Bug Fixes

- **benchmarks**: Import LCM types from compas_lcmtypes
  ([`25f4130`](https://github.com/mbari-org/lcm-websocket-server/commit/25f41308bbfd88e9b31aa625c3e56567820c1cf5))

### Build System

- **docker**: One generic image with bundled LCM types, bake definition
  ([`f7f95ed`](https://github.com/mbari-org/lcm-websocket-server/commit/f7f95ed87ffb750d3e4ad232c0c8cf6b5cd42a3b))

### Continuous Integration

- Upload release assets, smoke-test and publish Docker images
  ([`6e5766a`](https://github.com/mbari-org/lcm-websocket-server/commit/6e5766a0eb2dbc2084fbd66dfda9059956ee604d))

### Documentation

- Document the generic Docker image, lcmtypes extra and releases
  ([`485bd06`](https://github.com/mbari-org/lcm-websocket-server/commit/485bd069ccd1b9f0980fd1a4a4cad458d5b92870))

### Features

- Decouple the Dial proxy from MOLARS types; add lcmtypes extra
  ([`5be0f84`](https://github.com/mbari-org/lcm-websocket-server/commit/5be0f84cb6b3f25d770470d07033a7d9336847c1))


## v0.9.0-rc.1 (2026-11-19)

### Bug Fixes

- Stop work on closed connections, clean daemon shutdown, unify logging
  ([`3ef31d5`](https://github.com/mbari-org/lcm-websocket-server/commit/3ef31d55b6c0ce88988853cec44ede441a97d0da))

### Build System

- Require Python 3.11+, track uv.lock, add orjson/uvloop extras
  ([`c61e213`](https://github.com/mbari-org/lcm-websocket-server/commit/c61e213d5514ba62429940818db31c97ce779675))

- **docker**: Install with uv, add extras, ARGS and HEALTHCHECK
  ([`b2355f7`](https://github.com/mbari-org/lcm-websocket-server/commit/b2355f714a2a6137ed038723a8bfdf1250a2ae9f))

### Code Style

- Add project ruff config and clear existing lint findings
  ([`8094e21`](https://github.com/mbari-org/lcm-websocket-server/commit/8094e21e6488a5d5f4127e1a86d89b4cfaad0202))

### Continuous Integration

- Add GitHub Actions lint/test workflow and pre-commit hooks
  ([`db26af7`](https://github.com/mbari-org/lcm-websocket-server/commit/db26af73392b2e99bce03d76efb01ee936a8f244))

- Automate versioning and changelog with python-semantic-release
  ([`b01e5e1`](https://github.com/mbari-org/lcm-websocket-server/commit/b01e5e123c8d98474f93e06924a4a25db8bbc169))

### Documentation

- Deployment, monitoring and development guidance
  ([`fd2d96c`](https://github.com/mbari-org/lcm-websocket-server/commit/fd2d96cb1d1739dc18b3d5120c8f923c0b449b0b))

- Document conflation, new flags, extras and spy semantics
  ([`06ffd0f`](https://github.com/mbari-org/lcm-websocket-server/commit/06ffd0f19cb7be1be1e54a896e90c64daa7729d9))

### Features

- /healthz and /metrics endpoints, configurable keepalive
  ([`1d73c4c`](https://github.com/mbari-org/lcm-websocket-server/commit/1d73c4c69a7510c6fc404cd25f8a23faca047252))

- Shared CLI with --compression, --uvloop and --processes
  ([`5b40b2a`](https://github.com/mbari-org/lcm-websocket-server/commit/5b40b2a702a04da8cc6eec8dbd7b289a50616f39))

### Performance Improvements

- Encode each message once across clients, split worker pools
  ([`313df9f`](https://github.com/mbari-org/lcm-websocket-server/commit/313df9f3abf83c2a4f68ef5623452bf72a3a374e))

- Event-driven fan-out, per-channel conflation, cached routing
  ([`22269da`](https://github.com/mbari-org/lcm-websocket-server/commit/22269dadf1124db55926bdbd213be7fe17632bc9))

- Faster LCM-to-JSON encoding, send text payloads pre-encoded
  ([`2b39a57`](https://github.com/mbari-org/lcm-websocket-server/commit/2b39a574189712ec101bea4727cc99f53270cab0))

- Shared spy accumulator with per-connection views
  ([`ad98b51`](https://github.com/mbari-org/lcm-websocket-server/commit/ad98b51f7b8d576c12e1bcf50f2e8ce43ea534b1))

- Skip no-op image resize, add per-channel --max-fps for Dial
  ([`ef31678`](https://github.com/mbari-org/lcm-websocket-server/commit/ef31678ae9468e4944fc5deba44374807c35c8fa))

### Testing

- Add load and encoding benchmarks with before/after results
  ([`8f3c120`](https://github.com/mbari-org/lcm-websocket-server/commit/8f3c1207d055cab3b010e1cdf22abdd034c5f44a))

- Lint fixes for new tests
  ([`ae19299`](https://github.com/mbari-org/lcm-websocket-server/commit/ae19299f4fa35f1eddabdd4a3c51a7aec3e9712a))

- Vendor the LCM types used by tests so the suite is self-contained
  ([`7fe11cb`](https://github.com/mbari-org/lcm-websocket-server/commit/7fe11cb99268218e8ee9e483c5fc41d3b422cfc7))


## v0.8.0 (2026-04-20)

### Documentation

- Fix build instructions, document tuning options
  ([`be46808`](https://github.com/mbari-org/lcm-websocket-server/commit/be46808c58e4d92f5cc3abcb89a9939efd10084e))

### Features

- Add static LCM Dial Stream Viewer HTML page
  ([`129ed3d`](https://github.com/mbari-org/lcm-websocket-server/commit/129ed3d0c5d40461364ae22b3ffe770f92a0cec9))

- Rework LWS LCM spy to be per-connection
  ([`8153aa5`](https://github.com/mbari-org/lcm-websocket-server/commit/8153aa5b327ec4fd6155ce75b0cc083123317165))

### Performance Improvements

- Bounded queues, encode caching, off-loop handling, websockets 16.0
  ([`08be781`](https://github.com/mbari-org/lcm-websocket-server/commit/08be78111e809fda6fb71b5b14a3dd0815e5e48c))

### Testing

- Add comprehensive unit and integration test suite
  ([`25dd0e6`](https://github.com/mbari-org/lcm-websocket-server/commit/25dd0e62fb8f92fd899b1395277cc58719f4b448))


## v0.7.2 (2026-03-02)

### Bug Fixes

- #15 regression with image streams
  ([#16](https://github.com/mbari-org/lcm-websocket-server/pull/16),
  [`c5db8bd`](https://github.com/mbari-org/lcm-websocket-server/commit/c5db8bdbc4355e2270c5ebe2cd165c9751c15323))

### Chores

- Add simple dial proxy viewer webapp
  ([`6979d35`](https://github.com/mbari-org/lcm-websocket-server/commit/6979d3513f28ea0a940c54b4ea432d7ebc40f035))


## v0.7.1 (2025-11-21)

### Bug Fixes

- Revert to 9999 instead of inf for JSON validity
  ([`87ea59d`](https://github.com/mbari-org/lcm-websocket-server/commit/87ea59deb988292e527572ce04c84227c3efed36))

### Documentation

- Rework README, add docs on update interval
  ([`c744c23`](https://github.com/mbari-org/lcm-websocket-server/commit/c744c23064b81ae5609fd3cd6fa4525892f1e910))


## v0.7.0 (2025-11-21)

### Features

- Implement optional update_interval_ms query params to support decimation
  ([`a12602c`](https://github.com/mbari-org/lcm-websocket-server/commit/a12602cc33250ea1c3982437226003384bb02e0d))

- Make handlers async
  ([`1414bc0`](https://github.com/mbari-org/lcm-websocket-server/commit/1414bc058edac989eeeb6a1569a07c7169e9bdf3))


## v0.6.0 (2025-11-20)

### Chores

- Update Dockerfile/build scripts, remove compas_lcmtypes target (for v0.5.0)
  ([`b4d50d8`](https://github.com/mbari-org/lcm-websocket-server/commit/b4d50d8c5a54d24e74ec26ce76863f1d7e9d421e))

### Documentation

- Add docs on LCM spy channel
  ([`99e24ac`](https://github.com/mbari-org/lcm-websocket-server/commit/99e24ac8c48f28f570ca6138d563caf73b793799))

### Features

- Add LCM spy functionality
  ([`576aa42`](https://github.com/mbari-org/lcm-websocket-server/commit/576aa42531add9207a4c1e3875cfed365a4e1e41))


## v0.5.0 (2025-04-11)

### Chores

- Add uv lockfile to ignore files
  ([`21a765e`](https://github.com/mbari-org/lcm-websocket-server/commit/21a765e392e22b60a810d09a48ef45a0f727d752))

### Features

- Remove jpeg_proxy, rely on molars_lcmtypes for Dial proxy
  ([`7e5ef90`](https://github.com/mbari-org/lcm-websocket-server/commit/7e5ef90ce5b2f30e0025089c9c197476f935c096))


## v0.4.1 (2025-03-13)

### Bug Fixes

- Resolve issues in different MolaRS/CoMPAS LCM type usage
  ([`d243e4e`](https://github.com/mbari-org/lcm-websocket-server/commit/d243e4ec03c2ae890902fcda6992846d84ff80c4))

### Refactoring

- **lcm_utils**: Enhance type hints and improve LCM event handling
  ([`d867e2f`](https://github.com/mbari-org/lcm-websocket-server/commit/d867e2f0f4532e65c0a0e1eadd4c8b620030bffe))


## v0.4.0 (2024-10-31)

### Features

- Support nested LCM type decoding
  ([`2ebc9b6`](https://github.com/mbari-org/lcm-websocket-server/commit/2ebc9b626d874a57b313bbb297b5557bc1ed8d3f))

### Refactoring

- Clean up lcm util types module
  ([`3886e65`](https://github.com/mbari-org/lcm-websocket-server/commit/3886e6519266c5cc2f92b7e2e9104a00f6e4f612))

- **docker**: Tweak dockerfiles to use pip-installed LCM
  ([`78fb468`](https://github.com/mbari-org/lcm-websocket-server/commit/78fb468b64d5c4c75123e1be685b84a21dd4cca5))


## v0.3.0 (2024-10-21)

### Chores

- Add scripts to build and push all docker images
  ([`8c064ca`](https://github.com/mbari-org/lcm-websocket-server/commit/8c064ca897d704dd80e9f9b47864c3a4b210fe97))


## v0.2.2 (2024-10-02)

### Bug Fixes

- Handle encoding of nested dictionaries and handle NaN values
  ([`c85b1f5`](https://github.com/mbari-org/lcm-websocket-server/commit/c85b1f578ae2a7b5188a5a568b6f811c16c665c3))


## v0.2.1 (2024-06-06)

### Bug Fixes

- Fix swapped BGR2GRAY and GRAY2BGR in grayscale encode/decode
  ([`7847239`](https://github.com/mbari-org/lcm-websocket-server/commit/7847239b0f23cbb3fa3b1b1290d8096d57bf6156))


## v0.2.0 (2024-03-01)


## v0.1.1 (2023-11-08)


## v0.1.0 (2023-04-26)

- Initial Release
