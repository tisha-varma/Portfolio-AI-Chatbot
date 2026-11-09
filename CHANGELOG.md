# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project is not yet released, so versions are added when the first deploy is
tagged.

## [Unreleased]

### Added

- `scripts/check_secrets.sh`, a pre-commit hook that blocks staged credentials
  without printing the matched text.
- `docs/DECISIONS.md`, the architecture decision log.
- README instructions for installing the secret-scan hook.

### Changed

- `.gitignore` now also covers `*.env`, `*.log`, `node_modules/`, and generated
  evaluation reports (while keeping `.gitkeep` and `LATEST.md`).
