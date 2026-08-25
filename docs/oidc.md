# OpenID Connect client

`opendle.oidc` supplies a dependency-free confidential OpenID Connect client.
It does not define host authorization or browser-session policy.

## Client flow

Create one `OidcClient` with an exact issuer, client ID, client secret, and
redirect URI. Select `OidcClientAuthenticationMethod.CLIENT_SECRET_BASIC` or
`CLIENT_SECRET_POST` to match the provider registration.

1. Call `discover()` and keep the returned `OidcMetadata` for the flow.
2. A host that must check key availability before it sends a one-use code can
   call `fetch_signing_keys()` and keep its immutable `OidcSigningKeys` result.
3. Call `authorization_url()` with caller-generated state, nonce, and PKCE
   verifier values.
4. The host must validate and consume its state before it exchanges the code.
5. Call `exchange_code()` with the code and the same PKCE verifier.
6. Call `verify_id_token()` with the returned token, expected nonce, and the
   preflight key snapshot. A host that does not supply a snapshot gets a new
   bounded key read.
7. Apply host authorization to the verified issuer and subject.

The verified result contains the issuer, subject, audiences, issue time,
expiry time, and a read-only claims mapping. The client validates RS256, the
exact signing key, issuer, audience, authorized party, expiry, issue time,
optional not-before time, and nonce.

## Transport and bounds

The default transport uses the Python standard library. It disables proxy
environment variables, does not follow redirects, and reads no more than one
byte above the selected document limit. It validates response status, header
count, header bytes, critical duplicate headers, content length, media type,
UTF-8 JSON, duplicate JSON keys, non-finite numbers, and Unicode text.

A host can inject an `OidcTransport`. The transport must not follow redirects
or use an untrusted proxy. It must bound response headers and bytes before it
returns. It must not log request headers, request bodies, provider response
bodies, or URL query values. The client applies the same response and document
checks to an injected transport. Client-created errors do not contain the
client secret, provider response content, token content, or transport error
text.

## Host-owned policy

The host must keep these controls outside the shared client:

- administrator or user allowlists;
- grants, roles, and permissions;
- OIDC state storage and replay prevention;
- local sessions and revocation;
- cookies, CSRF, and allowed origins;
- return-path rules and route behavior;
- application error mapping and audit records;
- client-secret storage and deployment configuration.
