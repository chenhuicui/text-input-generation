# Credential handling

This synchronized publication copy was scanned before publication. Hard-coded API keys and database passwords found in legacy source files were replaced in the publication copy with `REDACTED_USE_ENV_*` placeholders. The original local experiment working tree was not modified.

Use environment variables listed in `.env.example` for local execution. Never commit a populated `.env` file. If an older script still reads a legacy YAML field directly, provide a local ignored configuration file or update that script to read the corresponding environment variable before running it.

The removed values are not included in this synchronized commit. Credentials should nevertheless be rotated if they are still active.
