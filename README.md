# text-input-generation

This repository contains the code used for the paper **“Large Language Models for Mobile GUI Text Input Generation: An Empirical Study.”** It includes the original Android app collection utilities, the current text-input generation implementation, and the supplementary evaluation code used by the latest experiment protocol.

## Repository layout

- `app-collection/` — Google Play metadata collection, APK download, and text-input-component filtering utilities.
- `text-generation/configs/` — APK, model, installation, prompt, and supplementary experiment configurations.
- `text-generation/src/` — Android installation/navigation, context extraction, LLM interaction, test execution, experiment, and analysis code.
- `text-generation/tosem_major/text_input_eval/` — evaluation services, validators, experiment runners, audit/report scripts, supplementary protocols, and tests.

## Current supplementary protocol

The current configuration is `text-generation/configs/supplementary/e2_abcdef_v1.json`, whose protocol identifier is `E2-abcdef-v2`. It defines six feedback arms:

- **A** — stateless independent retry.
- **B** — recent candidate history.
- **C** — candidate history plus `PASS` / `FAIL` / `UNKNOWN`.
- **D** — C plus time-ordered application screenshots.
- **E** — D plus deterministic public UI changes and visible public error text.
- **F** — E plus evidence-bound cause analysis without repair advice or Oracle override.

The historical supplementary implementations remain available for traceability. Generated experiment results are deliberately excluded from this repository.

## Setup

```bash
cd text-generation
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill local environment variables as needed. Do not commit `.env` or real credentials.

The original text-generation entry point is:

```bash
python main.py
```

## Offline verification

The self-contained A–F unit and contract tests use fake gateways and local fixtures; they do not launch a formal experiment:

```bash
cd text-generation
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tosem_major \
  python -m unittest -v \
  text_input_eval.tests.test_abcdef_analyzer_gateway \
  text_input_eval.tests.test_abcdef_cli \
  text_input_eval.tests.test_abcdef_environment \
  text_input_eval.tests.test_abcdef_evidence \
  text_input_eval.tests.test_abcdef_migration \
  text_input_eval.tests.test_abcdef_mysql \
  text_input_eval.tests.test_abcdef_runner \
  text_input_eval.tests.test_abcdef_templates
```

Some Android adapter and structured-recovery integration tests intentionally verify hash-pinned files under excluded run-output or external-volume paths. Run those only with the corresponding frozen artifacts mounted. Before any device-backed or provider-backed execution, review the selected configuration and run the relevant fail-closed preflight. Commands that invoke models, Android devices, MySQL, or formal-run storage are not part of the offline verification above.

## Publication boundary

This repository contains code, static configuration, validators, and tests. It excludes API credentials, populated `.env` files, SQLite ledgers, generated run directories, screenshots and videos, APK payloads, Android emulator images/snapshots, manuscript files, and local temporary artifacts. Legacy credential fields in the publication copy use `REDACTED_USE_ENV_*` placeholders; see `text-generation/SECURITY.md`.
