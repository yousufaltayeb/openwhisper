# Clean 1.0 data boundary

OpenWhisper 1.0 creates only a versioned `config.toml`, `state.sqlite3`, cache,
and runtime directory. `OPENWHISPER_V1_HOME` exists for tests and portable
foreground use. Per-user directories are 0700 and configuration/database files
are 0600 on Unix.

Legacy INI files, history, personalization, credentials, CTranslate2 caches,
and Hugging Face model caches are never opened, parsed, migrated, altered, or
deleted. `doctor` checks only filesystem metadata and reports their paths with
this message:

> Legacy OpenWhisper data was detected and remains untouched. OpenWhisper 1.0
> uses a new config.toml and state.sqlite3.

Normal uninstall preserves 1.0 and legacy data. A future signed installer may
delete only the displayed 1.0 paths after the user explicitly supplies
`openwhisper service uninstall --purge`; legacy paths remain out of scope.
