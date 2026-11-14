# Share passkey sign-in, local sessions, and blob storage

Status: Accepted by the user request on 2026-11-14.

The owner decided that Poster starts the shared passkey, session, API token,
and blob storage code in this library. Xbot and later services will use the
same code. The roadmap rule to wait for a second consumer does not apply to
these parts.

- `opendle.local_auth` has no dependency. It creates session and API token
  secrets and stores only their SHA-256 hashes through a caller-owned store.
- `opendle.passkey` needs the `passkey` extra (`webauthn`). It runs
  registration, addition, and sign-in ceremonies. Credentials are
  discoverable, user verification is required, and attestation is not used.
  A one-time setup grant opens registration. There is no open registration.
- `opendle.passkey_fastapi` needs the `passkey-fastapi` extra. It supplies
  the routes, the `__Host-` cookies, the same-origin check, and the request
  dependencies.
- `opendle.blob_store` has no dependency. It stores blobs by SHA-256 through
  a caller-supplied S3 client and keeps a bounded local cache.

The consumer keeps its database schema, migrations, user records, permission
rules, bucket names, and credentials. The library has no rate limit: the
first consumer has one user behind an access proxy. A consumer that is open to
the public must add its own limit.
