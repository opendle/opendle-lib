"""Passkey registration and sign-in with caller-owned storage.

The module uses the optional `webauthn` package. All credentials are
discoverable, user verification is required, and attestation is not used. A
one-time setup grant opens registration; there is no open registration.
"""

from __future__ import annotations

import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol, cast
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from opendle.local_auth import AuthError, Clock, hash_secret, is_secret, new_secret
from opendle.local_auth import utc_now as _utc_now

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from opendle.contracts import JsonObject

__all__ = [
    "CeremonyPurpose",
    "Passkey",
    "PasskeyConfig",
    "PasskeyService",
    "PasskeyStore",
    "SetupGrant",
    "StartedCeremony",
    "StoredChallenge",
]

type CeremonyPurpose = Literal["register", "add", "login"]

_CHALLENGE_LIFETIME = timedelta(minutes=5)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
_MAXIMUM_LABEL_LENGTH = 100
_MAXIMUM_GRANT_MINUTES = 60


@dataclass(frozen=True, slots=True)
class PasskeyConfig:
    """Hold the relying party identity for passkey ceremonies."""

    origin: str
    rp_id: str
    rp_name: str

    @property
    def secure(self) -> bool:
        """Return whether the origin uses HTTPS."""
        return self.origin.startswith("https://")

    @classmethod
    def from_origin(
        cls,
        origin: str,
        *,
        rp_name: str,
        rp_id: str | None = None,
    ) -> PasskeyConfig:
        """Validate an origin and derive the relying party ID from its host.

        Raises:
            ValueError: If the origin is not HTTPS or a local HTTP origin, or
                if the relying party ID does not contain the origin host.

        """
        parts = urlsplit(origin)
        host = parts.hostname or ""
        local = parts.scheme == "http" and host in _LOCAL_HOSTS
        if (
            f"{parts.scheme}://{parts.netloc}" != origin
            or not host
            or not (parts.scheme == "https" or local)
        ):
            msg = "The origin must be an HTTPS origin or a local HTTP origin."
            raise ValueError(msg)
        selected_rp_id = host if rp_id is None else rp_id
        if host != selected_rp_id and not host.endswith(f".{selected_rp_id}"):
            msg = "The relying party ID does not agree with the origin."
            raise ValueError(msg)
        return cls(origin=origin, rp_id=selected_rp_id, rp_name=rp_name)


@dataclass(frozen=True, slots=True)
class Passkey:
    """Describe one stored passkey. `id` is the base64url credential ID."""

    id: str
    user_id: str
    public_key: bytes
    sign_count: int
    transports: tuple[str, ...]
    label: str
    created_at: datetime
    last_used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredChallenge:
    """Describe one pending ceremony. The store keeps it for one use."""

    id: str
    binding_hash: str
    challenge: bytes
    purpose: CeremonyPurpose
    user_id: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SetupGrant:
    """Describe the user that a valid setup grant can register."""

    user_id: str
    user_name: str


@dataclass(frozen=True, slots=True)
class StartedCeremony:
    """Carry the data for the browser at the start of a ceremony.

    `binding` goes in an HTTP-only cookie. `challenge_id` and `options` go in
    the response body. `options` is the JSON form that the browser WebAuthn
    functions `parseCreationOptionsFromJSON` and `parseRequestOptionsFromJSON`
    accept.
    """

    challenge_id: str
    binding: str
    options: JsonObject


class PasskeyStore(Protocol):
    """Store users, passkeys, challenges, and setup grants."""

    def add_challenge(self, challenge: StoredChallenge) -> None:
        """Store one pending ceremony."""
        ...

    def take_challenge(self, challenge_id: str) -> StoredChallenge | None:
        """Delete and return one pending ceremony, or return `None`."""
        ...

    def add_user(self, user_id: str, name: str) -> None:
        """Store one new user."""
        ...

    def user_name(self, user_id: str) -> str | None:
        """Return the name of a user, or `None` when the user is unknown."""
        ...

    def add_passkey(self, passkey: Passkey) -> None:
        """Store one new passkey."""
        ...

    def passkey(self, passkey_id: str) -> Passkey | None:
        """Return one passkey by its credential ID."""
        ...

    def passkeys(self, user_id: str) -> Sequence[Passkey]:
        """Return the passkeys of one user, oldest first."""
        ...

    def record_passkey_use(
        self,
        passkey_id: str,
        sign_count: int,
        used_at: datetime,
    ) -> None:
        """Store the signature counter and the time of a sign-in."""
        ...

    def remove_passkey(self, user_id: str, passkey_id: str) -> bool:
        """Delete one passkey of a user. Return whether it existed."""
        ...

    def add_setup_grant(
        self,
        grant_hash: str,
        grant: SetupGrant,
        expires_at: datetime,
    ) -> None:
        """Store one setup grant by the hash of its secret."""
        ...

    def setup_grant(self, grant_hash: str, now: datetime) -> SetupGrant | None:
        """Return a grant that is not used and expires after `now`."""
        ...

    def consume_setup_grant(self, grant_hash: str, now: datetime) -> bool:
        """Mark a valid grant as used. Return `False` when it is not valid."""
        ...


class PasskeyService:
    """Run passkey ceremonies and manage the passkeys of a user."""

    def __init__(
        self,
        store: PasskeyStore,
        config: PasskeyConfig,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        """Use a caller-owned store and one relying party identity."""
        self._store = store
        self._config = config
        self._clock = clock

    def create_setup_grant(
        self,
        user_name: str,
        *,
        user_id: str | None = None,
        minutes: int = 15,
    ) -> str:
        """Create a one-time setup grant and return its secret.

        Without `user_id`, the grant registers a new user. With the ID of a
        current user, the grant adds a passkey to that user, for recovery.
        Only an operator must call this function.

        Raises:
            ValueError: If the name or the lifetime is not valid.

        """
        clean_name = user_name.strip()
        if not clean_name or len(clean_name) > _MAXIMUM_LABEL_LENGTH:
            msg = "The user name must have from 1 to 100 characters."
            raise ValueError(msg)
        if not 1 <= minutes <= _MAXIMUM_GRANT_MINUTES:
            msg = "The setup grant lifetime must be from 1 to 60 minutes."
            raise ValueError(msg)
        secret = new_secret()
        grant = SetupGrant(
            user_id=str(uuid.uuid4()) if user_id is None else user_id,
            user_name=clean_name,
        )
        expires_at = self._clock() + timedelta(minutes=minutes)
        self._store.add_setup_grant(hash_secret(secret), grant, expires_at)
        return secret

    def begin_registration(self, setup_token: str) -> StartedCeremony:
        """Start the registration that a setup grant permits.

        Raises:
            AuthError: If the setup grant is not valid.

        """
        grant = self._valid_grant(setup_token)
        return self._begin_creation("register", grant.user_id, grant.user_name)

    def finish_registration(
        self,
        *,
        challenge_id: str,
        binding: str | None,
        credential: Mapping[str, object],
        setup_token: str,
        label: str = "",
    ) -> Passkey:
        """Verify a registration, use the grant, and store the passkey.

        The function also stores the user when the grant is for a new user.

        Raises:
            AuthError: If the challenge, the grant, or the credential is not
                valid.

        """
        challenge = self._take_challenge(challenge_id, binding, "register")
        grant = self._valid_grant(setup_token)
        if grant.user_id != challenge.user_id:
            msg = "The setup link is not valid."
            raise AuthError(msg, reason="setup_grant")
        passkey = self._verified_new_passkey(
            challenge, grant.user_id, credential, label
        )
        if not self._store.consume_setup_grant(
            hash_secret(setup_token),
            self._clock(),
        ):
            msg = "The setup link is not valid."
            raise AuthError(msg, reason="setup_grant")
        if self._store.user_name(grant.user_id) is None:
            self._store.add_user(grant.user_id, grant.user_name)
        self._store.add_passkey(passkey)
        return passkey

    def begin_addition(self, user_id: str) -> StartedCeremony:
        """Start the addition of a passkey to a signed-in user.

        Raises:
            AuthError: If the user is unknown.

        """
        user_name = self._store.user_name(user_id)
        if user_name is None:
            msg = "The user is unknown."
            raise AuthError(msg, reason="input")
        return self._begin_creation("add", user_id, user_name)

    def finish_addition(
        self,
        *,
        user_id: str,
        challenge_id: str,
        binding: str | None,
        credential: Mapping[str, object],
        label: str = "",
    ) -> Passkey:
        """Verify and store a passkey for a signed-in user.

        Raises:
            AuthError: If the challenge or the credential is not valid.

        """
        challenge = self._take_challenge(challenge_id, binding, "add")
        if challenge.user_id != user_id:
            raise _challenge_error()
        passkey = self._verified_new_passkey(challenge, user_id, credential, label)
        self._store.add_passkey(passkey)
        return passkey

    def begin_login(self) -> StartedCeremony:
        """Start a sign-in with a discoverable passkey."""
        options = generate_authentication_options(
            rp_id=self._config.rp_id,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return self._store_ceremony(
            "login",
            None,
            options.challenge,
            options_to_json(options),
        )

    def finish_login(
        self,
        *,
        challenge_id: str,
        binding: str | None,
        credential: Mapping[str, object],
    ) -> Passkey:
        """Verify a sign-in and return the passkey that signed.

        The caller starts the session for `Passkey.user_id`.

        Raises:
            AuthError: If the challenge or the credential is not valid.

        """
        challenge = self._take_challenge(challenge_id, binding, "login")
        credential_id = credential.get("id")
        stored = (
            self._store.passkey(credential_id)
            if isinstance(credential_id, str)
            else None
        )
        user_handle = _response_field(credential, "userHandle")
        if stored is None or user_handle != _user_handle_text(stored.user_id):
            raise _passkey_error()
        try:
            verified = verify_authentication_response(
                credential=json.dumps(credential),
                expected_challenge=challenge.challenge,
                expected_rp_id=self._config.rp_id,
                expected_origin=self._config.origin,
                credential_public_key=stored.public_key,
                credential_current_sign_count=stored.sign_count,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError) as error:
            raise _passkey_error() from error
        now = self._clock()
        self._store.record_passkey_use(stored.id, verified.new_sign_count, now)
        return Passkey(
            id=stored.id,
            user_id=stored.user_id,
            public_key=stored.public_key,
            sign_count=verified.new_sign_count,
            transports=stored.transports,
            label=stored.label,
            created_at=stored.created_at,
            last_used_at=now,
        )

    def passkeys(self, user_id: str) -> Sequence[Passkey]:
        """Return the passkeys of one user, oldest first."""
        return self._store.passkeys(user_id)

    def remove_passkey(self, user_id: str, passkey_id: str) -> bool:
        """Delete one passkey of a user. Return whether it existed.

        Raises:
            AuthError: If the passkey is the last passkey of the user.

        """
        current = self._store.passkeys(user_id)
        if not any(passkey.id == passkey_id for passkey in current):
            return False
        if len(current) == 1:
            msg = "The last passkey cannot be removed."
            raise AuthError(msg, reason="last_passkey")
        return self._store.remove_passkey(user_id, passkey_id)

    def _valid_grant(self, setup_token: str) -> SetupGrant:
        grant = (
            self._store.setup_grant(hash_secret(setup_token), self._clock())
            if is_secret(setup_token)
            else None
        )
        if grant is None:
            msg = "The setup link is not valid."
            raise AuthError(msg, reason="setup_grant")
        return grant

    def _begin_creation(
        self,
        purpose: CeremonyPurpose,
        user_id: str,
        user_name: str,
    ) -> StartedCeremony:
        options = generate_registration_options(
            rp_id=self._config.rp_id,
            rp_name=self._config.rp_name,
            user_name=user_name,
            user_id=user_id.encode("utf-8"),
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.id))
                for passkey in self._store.passkeys(user_id)
            ],
        )
        return self._store_ceremony(
            purpose,
            user_id,
            options.challenge,
            options_to_json(options),
        )

    def _store_ceremony(
        self,
        purpose: CeremonyPurpose,
        user_id: str | None,
        challenge: bytes,
        options_json: str,
    ) -> StartedCeremony:
        challenge_id = str(uuid.uuid4())
        binding = new_secret()
        self._store.add_challenge(
            StoredChallenge(
                id=challenge_id,
                binding_hash=hash_secret(binding),
                challenge=challenge,
                purpose=purpose,
                user_id=user_id,
                expires_at=self._clock() + _CHALLENGE_LIFETIME,
            ),
        )
        return StartedCeremony(
            challenge_id=challenge_id,
            binding=binding,
            options=cast("JsonObject", json.loads(options_json)),
        )

    def _take_challenge(
        self,
        challenge_id: str,
        binding: str | None,
        purpose: CeremonyPurpose,
    ) -> StoredChallenge:
        # The store deletes the challenge, so a failed attempt also uses it.
        challenge = self._store.take_challenge(challenge_id)
        if (
            challenge is None
            or binding is None
            or not hmac.compare_digest(challenge.binding_hash, hash_secret(binding))
            or challenge.purpose != purpose
            or challenge.expires_at <= self._clock()
        ):
            raise _challenge_error()
        return challenge

    def _verified_new_passkey(
        self,
        challenge: StoredChallenge,
        user_id: str,
        credential: Mapping[str, object],
        label: str,
    ) -> Passkey:
        try:
            verified = verify_registration_response(
                credential=json.dumps(credential),
                expected_challenge=challenge.challenge,
                expected_rp_id=self._config.rp_id,
                expected_origin=self._config.origin,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError) as error:
            raise _passkey_error() from error
        passkey_id = bytes_to_base64url(verified.credential_id)
        if self._store.passkey(passkey_id) is not None:
            raise _passkey_error()
        return Passkey(
            id=passkey_id,
            user_id=user_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=_transports(credential),
            label=label.strip()[:_MAXIMUM_LABEL_LENGTH],
            created_at=self._clock(),
        )


def _user_handle_text(user_id: str) -> str:
    return bytes_to_base64url(user_id.encode("utf-8"))


def _response_field(credential: Mapping[str, object], name: str) -> object:
    response = credential.get("response")
    if not isinstance(response, dict):
        return None
    return cast("Mapping[str, object]", response).get(name)


def _transports(credential: Mapping[str, object]) -> tuple[str, ...]:
    values = _response_field(credential, "transports")
    if not isinstance(values, list):
        return ()
    return tuple(
        value for value in cast("list[object]", values) if isinstance(value, str)
    )


def _challenge_error() -> AuthError:
    return AuthError("Start the passkey step again.", reason="challenge")


def _passkey_error() -> AuthError:
    return AuthError("This passkey was not accepted.", reason="passkey")
