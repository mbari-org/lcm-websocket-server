# Test LCM types

Python LCM types used by the test suite, copied unmodified from the lcm-gen output of
[MolaRS](https://github.com/CoMPASLab/molars) (`stdlcm.header_t`, `senlcm.image_t`,
`senlcm.image_metadata_t`), so the tests run without MolaRS installed.

pytest puts this directory first on `sys.path` (see `pythonpath` in `pyproject.toml`).
Regenerate from MolaRS if those message definitions change.
