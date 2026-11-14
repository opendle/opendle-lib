"""Check passkey ceremonies with a software passkey and a memory store."""

from __future__ import annotations

from typing import cast

import pytest

from opendle.local_auth import AuthError, new_secret
from opendle.passkey import Passkey, PasskeyConfig, PasskeyService
from passkey_support import (
    CONFIG,
    ORIGIN,
    FakeClock,
    MemoryStore,
    SoftPasskey,
    new_passkey,
)


class Scene:
    """Hold one service with its store, clock, and first passkey."""

    def __init__(self) -> None:
        """Create a service without users."""
        self.store = MemoryStore()
        self.clock = FakeClock()
        self.service = PasskeyService(self.store, CONFIG, clock=self.clock)
        self.device = new_passkey()

    def register(
        self,
        device: SoftPasskey | None = None,
        *,
        user_id: str | None = None,
        transports: list[object] | None = None,
    ) -> Passkey:
        """Register a passkey through a new setup grant."""
        token = self.service.create_setup_grant("Owner", user_id=user_id)
        started = self.service.begin_registration(token)
        return self.service.finish_registration(
            challenge_id=started.challenge_id,
            binding=started.binding,
            credential=(device or self.device).create(
                started.options,
                transports=transports,
            ),
            setup_token=token,
            label="  Laptop  ",
        )

    def login(self, device: SoftPasskey | None = None) -> Passkey:
        """Sign in with a passkey."""
        started = self.service.begin_login()
        return self.service.finish_login(
            challenge_id=started.challenge_id,
            binding=started.binding,
            credential=(device or self.device).get(started.options),
        )


def _reason(raised: pytest.ExceptionInfo[AuthError]) -> str:
    return raised.value.reason


def test_the_configuration_accepts_https_and_local_origins() -> None:
    """The relying party ID defaults to the host and can be a parent domain."""
    config = PasskeyConfig.from_origin(
        "https://poster.example.com",
        rp_name="Poster",
        rp_id="example.com",
    )
    local = PasskeyConfig.from_origin("http://localhost:5173", rp_name="Poster")

    assert (config.rp_id, config.secure) == ("example.com", True)
    assert (local.rp_id, local.secure) == ("localhost", False)
    assert CONFIG.rp_id == "app.example"


@pytest.mark.parametrize(
    ("origin", "rp_id"),
    [
        ("http://app.example", None),
        ("https://app.example/", None),
        ("https://", None),
        ("ftp://localhost", None),
        ("https://app.example", "other.example"),
    ],
)
def test_the_configuration_refuses_an_unsafe_origin(
    origin: str,
    rp_id: str | None,
) -> None:
    """A plain HTTP host, a path, or a foreign relying party ID is an error."""
    with pytest.raises(ValueError, match="origin"):
        PasskeyConfig.from_origin(origin, rp_name="Poster", rp_id=rp_id)


@pytest.mark.parametrize(("name", "minutes"), [(" ", 15), ("Owner", 0), ("Owner", 61)])
def test_a_setup_grant_needs_a_name_and_a_short_lifetime(
    name: str,
    minutes: int,
) -> None:
    """The operator cannot create a grant without a name or for a long time."""
    with pytest.raises(ValueError, match="must"):
        Scene().service.create_setup_grant(name, minutes=minutes)


def test_a_setup_grant_registers_a_user_and_the_passkey_signs_in() -> None:
    """Registration stores the user and passkey; sign-in updates the counter."""
    scene = Scene()
    token = scene.service.create_setup_grant("Owner")
    started = scene.service.begin_registration(token)
    user = cast("dict[str, object]", started.options["user"])
    selection = started.options["authenticatorSelection"]

    assert user["name"] == "Owner"
    assert selection == {
        "residentKey": "required",
        "requireResidentKey": True,
        "userVerification": "required",
    }
    assert started.options["attestation"] == "none"

    passkey = scene.service.finish_registration(
        challenge_id=started.challenge_id,
        binding=started.binding,
        credential=scene.device.create(
            started.options,
            transports=["internal", 7],
        ),
        setup_token=token,
        label="  Laptop  ",
    )

    assert scene.store.users == {passkey.user_id: "Owner"}
    assert scene.store.grants == {}
    assert (passkey.id, passkey.label, passkey.transports) == (
        scene.device.credential_id,
        "Laptop",
        ("internal",),
    )
    assert passkey.created_at == scene.clock.now
    assert scene.service.passkeys(passkey.user_id) == [passkey]

    scene.clock.advance(hours=1)
    login_options = scene.service.begin_login().options
    assert login_options["userVerification"] == "required"
    used = scene.login()

    assert used.user_id == passkey.user_id
    assert used.sign_count == 1
    assert used.last_used_at == scene.clock.now
    assert scene.store.passkey_rows[passkey.id] == used


def test_a_used_or_unknown_setup_grant_is_refused() -> None:
    """A grant works one time, and a bad token never starts a registration."""
    scene = Scene()
    token = scene.service.create_setup_grant("Owner")
    started = scene.service.begin_registration(token)
    scene.service.finish_registration(
        challenge_id=started.challenge_id,
        binding=started.binding,
        credential=scene.device.create(started.options),
        setup_token=token,
    )

    for bad_token in (token, new_secret(), "short"):
        with pytest.raises(AuthError) as raised:
            scene.service.begin_registration(bad_token)
        assert _reason(raised) == "setup_grant"


def test_a_setup_grant_expires() -> None:
    """A grant does not start a registration after its lifetime."""
    scene = Scene()
    token = scene.service.create_setup_grant("Owner", minutes=1)
    scene.clock.advance(minutes=1)

    with pytest.raises(AuthError) as raised:
        scene.service.begin_registration(token)

    assert _reason(raised) == "setup_grant"


def test_a_registration_must_finish_with_its_own_setup_grant() -> None:
    """A challenge from one grant does not accept the token of another."""
    scene = Scene()
    first = scene.service.create_setup_grant("Owner")
    second = scene.service.create_setup_grant("Other")
    started = scene.service.begin_registration(first)

    with pytest.raises(AuthError) as raised:
        scene.service.finish_registration(
            challenge_id=started.challenge_id,
            binding=started.binding,
            credential=scene.device.create(started.options),
            setup_token=second,
        )

    assert _reason(raised) == "setup_grant"
    assert scene.store.users == {}


def test_a_grant_that_another_request_used_stores_no_user() -> None:
    """A lost race for the grant is an error before any user record."""
    scene = Scene()
    token = scene.service.create_setup_grant("Owner")
    started = scene.service.begin_registration(token)
    scene.store.refuse_grant_use = True

    with pytest.raises(AuthError) as raised:
        scene.service.finish_registration(
            challenge_id=started.challenge_id,
            binding=started.binding,
            credential=scene.device.create(started.options),
            setup_token=token,
        )

    assert _reason(raised) == "setup_grant"
    assert scene.store.users == {}
    assert scene.store.passkey_rows == {}


def test_a_recovery_grant_adds_a_passkey_to_the_current_user() -> None:
    """A grant with a user ID registers a passkey and keeps the user."""
    scene = Scene()
    first = scene.register()
    second_device = new_passkey()

    second = scene.register(second_device, user_id=first.user_id)

    assert second.user_id == first.user_id
    assert second.transports == ()
    assert scene.store.users == {first.user_id: "Owner"}
    assert scene.login(second_device).id == second.id


def test_a_registered_credential_cannot_register_again() -> None:
    """The same credential ID is refused for a second record."""
    scene = Scene()
    scene.register()

    with pytest.raises(AuthError) as raised:
        scene.register()

    assert _reason(raised) == "passkey"


def test_a_challenge_works_one_time_with_its_binding_purpose_and_lifetime() -> None:
    """A wrong binding, a wrong purpose, or a late answer uses the challenge."""
    scene = Scene()
    scene.register()

    def attempt(*, binding: str | None, wait: float = 0, reuse: bool = False) -> str:
        started = scene.service.begin_login()
        credential = scene.device.get(started.options)
        scene.clock.advance(minutes=wait)
        if reuse:
            scene.service.finish_login(
                challenge_id=started.challenge_id,
                binding=started.binding,
                credential=credential,
            )
        with pytest.raises(AuthError) as raised:
            scene.service.finish_login(
                challenge_id=started.challenge_id,
                binding=started.binding if binding == "same" else binding,
                credential=credential,
            )
        assert started.challenge_id not in scene.store.challenges
        return _reason(raised)

    assert attempt(binding=None) == "challenge"
    assert attempt(binding=new_secret()) == "challenge"
    assert attempt(binding="same", wait=5) == "challenge"
    assert attempt(binding="same", reuse=True) == "challenge"

    token = scene.service.create_setup_grant("Owner")
    started = scene.service.begin_registration(token)
    with pytest.raises(AuthError) as raised:
        scene.service.finish_login(
            challenge_id=started.challenge_id,
            binding=started.binding,
            credential=scene.device.get(started.options),
        )
    assert _reason(raised) == "challenge"


def test_a_bad_registration_answer_is_refused() -> None:
    """An answer for a different origin does not store a passkey."""
    scene = Scene()
    token = scene.service.create_setup_grant("Owner")
    started = scene.service.begin_registration(token)

    with pytest.raises(AuthError) as raised:
        scene.service.finish_registration(
            challenge_id=started.challenge_id,
            binding=started.binding,
            credential=SoftPasskey(
                origin="https://evil.example", rp_id=CONFIG.rp_id
            ).create(
                started.options,
            ),
            setup_token=token,
        )

    assert _reason(raised) == "passkey"
    assert scene.store.passkey_rows == {}
    assert len(scene.store.grants) == 1


def test_a_bad_sign_in_answer_is_refused() -> None:
    """An unknown credential, a wrong user handle, or a bad signature fails."""
    scene = Scene()
    scene.register()
    stranger = new_passkey()

    def attempt(change: dict[str, object] | None, device: SoftPasskey) -> str:
        started = scene.service.begin_login()
        credential = device.get(started.options)
        if change is not None:
            credential.update(change)
        with pytest.raises(AuthError) as raised:
            scene.service.finish_login(
                challenge_id=started.challenge_id,
                binding=started.binding,
                credential=credential,
            )
        return _reason(raised)

    assert attempt(None, stranger) == "passkey"
    assert attempt({"id": 7}, scene.device) == "passkey"
    assert attempt({"response": "text"}, scene.device) == "passkey"
    assert attempt({"response": {"userHandle": "AAAA"}}, scene.device) == "passkey"
    stranger.use_identity_of(
        scene.device.credential_id,
        scene.store.passkey_rows[scene.device.credential_id].user_id,
    )
    assert attempt(None, stranger) == "passkey"
    assert scene.store.passkey_rows[scene.device.credential_id].sign_count == 0


def test_a_signed_in_user_adds_and_removes_passkeys() -> None:
    """A user keeps one passkey or more and removes only owned passkeys."""
    scene = Scene()
    first = scene.register()
    second_device = new_passkey()
    started = scene.service.begin_addition(first.user_id)
    excluded = cast("list[dict[str, object]]", started.options["excludeCredentials"])

    assert [item["id"] for item in excluded] == [first.id]

    second = scene.service.finish_addition(
        user_id=first.user_id,
        challenge_id=started.challenge_id,
        binding=started.binding,
        credential=second_device.create(started.options),
        label="Phone",
    )

    assert second.label == "Phone"
    assert scene.login(second_device).id == second.id
    assert scene.service.rename_passkey(first.user_id, "unknown", "Key") is None
    assert scene.service.rename_passkey("other-user", first.id, "Key") is None
    renamed = scene.service.rename_passkey(first.user_id, second.id, "  Tablet ")
    assert renamed is not None
    assert renamed.label == "Tablet"
    with pytest.raises(AuthError) as empty:
        scene.service.rename_passkey(first.user_id, second.id, "  ")
    assert _reason(empty) == "input"
    assert not scene.service.remove_passkey(first.user_id, "unknown")
    assert not scene.service.remove_passkey("other-user", first.id)
    assert scene.service.remove_passkey(first.user_id, first.id)
    with pytest.raises(AuthError) as raised:
        scene.service.remove_passkey(first.user_id, second.id)
    assert _reason(raised) == "last_passkey"
    assert [item.id for item in scene.service.passkeys(first.user_id)] == [second.id]


def test_an_addition_needs_a_known_user_and_its_own_challenge() -> None:
    """An unknown user cannot start, and another user cannot finish."""
    scene = Scene()
    first = scene.register()

    with pytest.raises(AuthError) as raised:
        scene.service.begin_addition("unknown")
    assert _reason(raised) == "input"

    started = scene.service.begin_addition(first.user_id)
    with pytest.raises(AuthError) as raised:
        scene.service.finish_addition(
            user_id="other-user",
            challenge_id=started.challenge_id,
            binding=started.binding,
            credential=new_passkey().create(started.options),
        )
    assert _reason(raised) == "challenge"
    assert CONFIG.origin == ORIGIN
