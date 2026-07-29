# OpenWhisper release signing public key

The armored release key is committed as `openwhisper-public.asc` in this directory.
Never commit a secret key, key backup, passphrase, generated `GNUPGHOME`, or
GitHub Actions credential. The release workflow expects these repository
secrets instead:

- `OPENWHISPER_GPG_PRIVATE_KEY` — base64-encoded private key export.
- `OPENWHISPER_GPG_PASSPHRASE` — passphrase for that export/signing key.
- `OPENWHISPER_GPG_FINGERPRINT` — full fingerprint used for signing.

Generate and encrypt the offline backup outside this repository. Before the
first release, verify the committed public key fingerprint out-of-band.

Fingerprint: `9DFE F9AB 055B 9CC8 A4D1 6DBB B6BF 3FE6 2C7E 797D`

The signing-only Ed25519 key expires on 2029-07-28. Rotate the Actions secrets,
offline backup, committed public key, and repository metadata together before
that date.
