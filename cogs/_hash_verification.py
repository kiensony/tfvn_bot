"""Issue and validate signed provenance proofs for bot-generated content."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Protocol

from pymongo.errors import DuplicateKeyError, PyMongoError


VERIFICATION_COLLECTION = "hash_verifications"
VERIFICATION_VERSION = 1
VERIFICATION_TOKEN_PREFIX = "tfv1"
VERIFICATION_REFERENCE_PREFIX = "tfp1_"
VERIFICATION_ISSUER = "tfvn_bot"
FEMBOY_CARD_KIND = "femboy_card"
QUOTE_KIND = "quote"
SUPPORTED_VERIFICATION_KINDS = frozenset({FEMBOY_CARD_KIND, QUOTE_KIND})

_TOKEN_DOMAIN = b"tfvn-content-proof-token-v1\0"
_SNAPSHOT_DOMAIN = b"tfvn-content-proof-snapshot-v1\0"
_RECORD_DOMAIN = b"tfvn-content-proof-record-v1\0"
_INSERT_ATTEMPTS = 3
_MAX_TOKEN_LENGTH = 1_024
_MAX_CLAIMS_BYTES = 2_048
_MAX_KEYRING_JSON_BYTES = 8_192
_MIN_KEY_BYTES = 32
_MAX_KEY_BYTES = 128
_MAX_SNAPSHOT_CONTENT = 20_000

_KID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")
_B64URL_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_LOWER_HEX_32_PATTERN = re.compile(r"[0-9a-f]{32}")
_LOWER_HEX_64_PATTERN = re.compile(r"[0-9a-f]{64}")
_DECIMAL_ID_PATTERN = re.compile(r"[1-9][0-9]{0,19}")
_REFERENCE_PATTERN = re.compile(r"tfp1_[a-z2-7]{26}")
_MESSAGE_URL_PATTERN = re.compile(
    r"https?://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<message_id>\d+)/?"
)

_COMMON_CLAIMS = frozenset(
    {
        "v",
        "iss",
        "jti",
        "kind",
        "guild_id",
        "issued_by_id",
        "iat",
        "snapshot_sha256",
        "snapshot_salt",
    }
)
_CARD_CLAIMS = _COMMON_CLAIMS | {"member_id", "role_id"}
_QUOTE_CLAIMS = _COMMON_CLAIMS | {
    "channel_id",
    "message_id",
    "author_id",
    "source_iat",
}
_CARD_PAYLOAD_KEYS = frozenset(
    {
        "guild_id",
        "member_id",
        "member_name",
        "role_id",
        "role_name",
        "issued_by_id",
        "issued_at",
    }
)
_QUOTE_PAYLOAD_KEYS = frozenset(
    {
        "guild_id",
        "channel_id",
        "channel_name",
        "message_id",
        "message_url",
        "author_id",
        "author_name",
        "content",
        "source_created_at",
        "issued_by_id",
        "issued_by_name",
        "issued_at",
    }
)
_LEGACY_DOCUMENT_KEYS = frozenset(
    {
        "_id",
        "version",
        "kid",
        "token_id",
        "kind",
        "claims",
        "snapshot_salt",
        "snapshot_sha256",
        "payload",
        "created_at",
    }
)
_DOCUMENT_KEYS = _LEGACY_DOCUMENT_KEYS | {"token"}


class _InsertCollection(Protocol):
    def insert_one(self, document: dict[str, Any]) -> Any: ...


class VerificationConfigurationError(RuntimeError):
    """Signing keys are missing or unsafe."""


class VerificationStoreError(RuntimeError):
    """A verification record could not be saved reliably."""


class VerificationTokenError(ValueError):
    """A proof token is malformed or has an invalid signature."""


class VerificationReferenceError(ValueError):
    """A short proof reference is malformed."""


@dataclass(frozen=True)
class VerificationKeyring:
    """Versioned HMAC keys, with one key selected for new proofs."""

    active_kid: str
    keys: Mapping[str, bytes]

    @property
    def active_key(self) -> bytes:
        return self.keys[self.active_kid]


@dataclass(frozen=True)
class VerifiedClaims:
    """Claims recovered only after a proof signature has been authenticated."""

    token: str
    kid: str
    raw: Mapping[str, Any]

    @property
    def token_id(self) -> str:
        return self.raw["jti"]

    @property
    def kind(self) -> str:
        return self.raw["kind"]

    @property
    def guild_id(self) -> int:
        return int(self.raw["guild_id"])

    @property
    def issued_by_id(self) -> int:
        return int(self.raw["issued_by_id"])

    @property
    def issued_at(self) -> int:
        return self.raw["iat"]

    @property
    def snapshot_sha256(self) -> str:
        return self.raw["snapshot_sha256"]


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_object(raw: str | bytes) -> dict[str, Any]:
    parsed = json.loads(
        raw,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object.")
    return parsed


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str, *, max_bytes: int) -> bytes:
    if not value or _B64URL_PATTERN.fullmatch(value) is None:
        raise ValueError("Invalid base64url value.")
    if len(value) > ((max_bytes + 2) // 3) * 4:
        raise ValueError("Base64url value is too large.")
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            padded,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64url value.") from exc
    if len(decoded) > max_bytes or _b64url_encode(decoded) != value:
        raise ValueError("Non-canonical base64url value.")
    return decoded


def load_verification_keyring(
    keys_json: str | None,
    active_kid: str | None,
) -> VerificationKeyring:
    """Load strict base64url HMAC keys from environment-style values."""
    if not isinstance(keys_json, str) or not keys_json.strip():
        raise VerificationConfigurationError(
            "CONTENT_VERIFICATION_KEYS_JSON is required."
        )
    if len(keys_json.encode("utf-8")) > _MAX_KEYRING_JSON_BYTES:
        raise VerificationConfigurationError(
            "CONTENT_VERIFICATION_KEYS_JSON is too large."
        )
    if not isinstance(active_kid, str) or not active_kid.strip():
        raise VerificationConfigurationError(
            "CONTENT_VERIFICATION_ACTIVE_KEY_ID is required."
        )

    selected_kid = active_kid.strip()
    if _KID_PATTERN.fullmatch(selected_kid) is None:
        raise VerificationConfigurationError(
            "The active content-verification key ID is invalid."
        )

    try:
        configured = _load_json_object(keys_json)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise VerificationConfigurationError(
            "CONTENT_VERIFICATION_KEYS_JSON must be a JSON object."
        ) from exc
    if not configured or len(configured) > 16:
        raise VerificationConfigurationError(
            "Configure between 1 and 16 content-verification keys."
        )

    keys: dict[str, bytes] = {}
    for kid, encoded_key in configured.items():
        if not isinstance(kid, str) or _KID_PATTERN.fullmatch(kid) is None:
            raise VerificationConfigurationError(
                "A content-verification key ID is invalid."
            )
        if not isinstance(encoded_key, str):
            raise VerificationConfigurationError(
                "Content-verification keys must be base64url strings."
            )
        try:
            key = _b64url_decode(encoded_key, max_bytes=_MAX_KEY_BYTES)
        except ValueError as exc:
            raise VerificationConfigurationError(
                "A content-verification key is not canonical base64url."
            ) from exc
        if len(key) < _MIN_KEY_BYTES:
            raise VerificationConfigurationError(
                "Content-verification keys must contain at least 32 bytes."
            )
        keys[kid] = key

    if selected_kid not in keys:
        raise VerificationConfigurationError(
            "The active content-verification key ID is not in the keyring."
        )
    return VerificationKeyring(
        active_kid=selected_kid,
        keys=MappingProxyType(keys),
    )


def verification_keyring_from_bot(bot: object) -> VerificationKeyring:
    """Resolve an injected keyring, or parse the bot's environment values."""
    injected = getattr(bot, "verification_keyring", None)
    if isinstance(injected, VerificationKeyring):
        return injected
    return load_verification_keyring(
        getattr(bot, "CONTENT_VERIFICATION_KEYS_JSON", None),
        getattr(bot, "CONTENT_VERIFICATION_ACTIVE_KEY_ID", None),
    )


def normalize_verification_token(value: str) -> str:
    """Normalize copy formatting without changing case-sensitive proof bytes."""
    if not isinstance(value, str):
        raise VerificationTokenError("Proof xác thực không hợp lệ.")
    candidate = value.strip()
    if len(candidate) >= 2 and candidate.startswith("`") and candidate.endswith("`"):
        candidate = candidate[1:-1].strip()
    if not candidate or len(candidate) > _MAX_TOKEN_LENGTH:
        raise VerificationTokenError("Proof xác thực không hợp lệ.")

    parts = candidate.split(".")
    if len(parts) != 4 or parts[0] != VERIFICATION_TOKEN_PREFIX:
        raise VerificationTokenError(
            "Proof phải có định dạng tfv1.<key>.<payload>.<signature>."
        )
    _, kid, payload_segment, signature_segment = parts
    if (
        _KID_PATTERN.fullmatch(kid) is None
        or _B64URL_PATTERN.fullmatch(payload_segment) is None
        or _B64URL_PATTERN.fullmatch(signature_segment) is None
        or len(payload_segment) > ((_MAX_CLAIMS_BYTES + 2) // 3) * 4
        or len(signature_segment) != 43
    ):
        raise VerificationTokenError("Proof xác thực không hợp lệ.")
    return candidate


def verification_reference_from_token_id(token_id: str) -> str:
    """Encode a signed 128-bit token ID as a short user-facing reference."""
    if (
        not isinstance(token_id, str)
        or _LOWER_HEX_32_PATTERN.fullmatch(token_id) is None
    ):
        raise ValueError("Invalid verification token ID.")
    encoded = base64.b32encode(bytes.fromhex(token_id)).decode("ascii")
    return VERIFICATION_REFERENCE_PREFIX + encoded.rstrip("=").lower()


def normalize_verification_reference(value: str) -> str:
    """Normalize a copied short proof code and reject all other forms."""
    if not isinstance(value, str):
        raise VerificationReferenceError("Mã proof không hợp lệ.")
    candidate = value.strip()
    if len(candidate) >= 2 and candidate.startswith("`") and candidate.endswith("`"):
        candidate = candidate[1:-1].strip()
    candidate = candidate.casefold()
    if _REFERENCE_PATTERN.fullmatch(candidate) is None:
        raise VerificationReferenceError(
            "Mã proof phải có dạng tfp1_<26 ký tự>."
        )
    return candidate


def verification_token_id_from_reference(value: str) -> str:
    """Decode a strict short proof reference back to its signed token ID."""
    reference = normalize_verification_reference(value)
    encoded = reference.removeprefix(VERIFICATION_REFERENCE_PREFIX).upper()
    padded = encoded + "=" * (-len(encoded) % 8)
    try:
        token_id = base64.b32decode(padded, casefold=False).hex()
    except (binascii.Error, ValueError) as exc:
        raise VerificationReferenceError("Mã proof không hợp lệ.") from exc
    if verification_reference_from_token_id(token_id) != reference:
        raise VerificationReferenceError("Mã proof không hợp lệ.")
    return token_id


def verification_reference_from_token(
    token: str,
    keyring: VerificationKeyring,
) -> str:
    """Return the short reference for an already signed proof token."""
    claims = verify_verification_token(token, keyring)
    return verification_reference_from_token_id(claims.token_id)


def _decimal_id(value: object) -> bool:
    if not isinstance(value, str) or _DECIMAL_ID_PATTERN.fullmatch(value) is None:
        return False
    parsed = int(value)
    return parsed <= (2**64 - 1) and str(parsed) == value


def _timestamp(value: object) -> bool:
    return type(value) is int and 0 < value <= 253_402_300_799


def _claims_are_valid(claims: Mapping[str, Any]) -> bool:
    if (
        claims.get("v") != VERIFICATION_VERSION
        or type(claims.get("v")) is not int
        or claims.get("iss") != VERIFICATION_ISSUER
        or claims.get("kind") not in SUPPORTED_VERIFICATION_KINDS
        or not isinstance(claims.get("jti"), str)
        or _LOWER_HEX_32_PATTERN.fullmatch(claims["jti"]) is None
        or not isinstance(claims.get("snapshot_sha256"), str)
        or _LOWER_HEX_64_PATTERN.fullmatch(claims["snapshot_sha256"]) is None
        or not isinstance(claims.get("snapshot_salt"), str)
        or _LOWER_HEX_32_PATTERN.fullmatch(claims["snapshot_salt"]) is None
        or not _decimal_id(claims.get("guild_id"))
        or not _decimal_id(claims.get("issued_by_id"))
        or not _timestamp(claims.get("iat"))
    ):
        return False

    kind = claims["kind"]
    if kind == FEMBOY_CARD_KIND:
        return bool(
            set(claims) == _CARD_CLAIMS
            and _decimal_id(claims.get("member_id"))
            and _decimal_id(claims.get("role_id"))
            and claims["member_id"] == claims["issued_by_id"]
        )
    return bool(
        set(claims) == _QUOTE_CLAIMS
        and _decimal_id(claims.get("channel_id"))
        and _decimal_id(claims.get("message_id"))
        and _decimal_id(claims.get("author_id"))
        and _timestamp(claims.get("source_iat"))
    )


def verify_verification_token(
    value: str,
    keyring: VerificationKeyring,
) -> VerifiedClaims:
    """Authenticate a compact proof before any database data is trusted."""
    if not isinstance(keyring, VerificationKeyring):
        raise VerificationConfigurationError(
            "A valid content-verification keyring is required."
        )
    token = normalize_verification_token(value)
    prefix, kid, payload_segment, signature_segment = token.split(".")
    key = keyring.keys.get(kid)
    if key is None:
        raise VerificationTokenError("Proof không có chữ ký TFVN hợp lệ.")

    signing_input = f"{prefix}.{kid}.{payload_segment}".encode("ascii")
    expected_signature = hmac.new(
        key,
        _TOKEN_DOMAIN + signing_input,
        hashlib.sha256,
    ).digest()
    try:
        supplied_signature = _b64url_decode(signature_segment, max_bytes=32)
    except ValueError as exc:
        raise VerificationTokenError("Proof không có chữ ký TFVN hợp lệ.") from exc
    if len(supplied_signature) != 32 or not hmac.compare_digest(
        supplied_signature,
        expected_signature,
    ):
        raise VerificationTokenError("Proof không có chữ ký TFVN hợp lệ.")

    try:
        claims_bytes = _b64url_decode(
            payload_segment,
            max_bytes=_MAX_CLAIMS_BYTES,
        )
        claims = _load_json_object(claims_bytes)
        if _canonical_json(claims) != claims_bytes:
            raise ValueError("Claims are not canonical JSON.")
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise VerificationTokenError("Payload proof không hợp lệ.") from exc
    if not _claims_are_valid(claims):
        raise VerificationTokenError("Payload proof không hợp lệ.")
    return VerifiedClaims(
        token=token,
        kid=kid,
        raw=MappingProxyType(claims),
    )


def verification_token_fingerprint(token: str) -> str:
    """Return the private-registry lookup key for an authenticated token."""
    return hashlib.sha256(_RECORD_DOMAIN + token.encode("ascii")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _positive_integer(value: object) -> bool:
    return bool(
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= (2**64 - 1)
    )


def _bounded_text(payload: Mapping[str, Any], key: str, limit: int) -> bool:
    value = payload.get(key)
    return bool(isinstance(value, str) and value.strip() and len(value) <= limit)


def _aware_iso_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _timestamp_from_iso(value: object) -> int | None:
    if not _aware_iso_timestamp(value):
        return None
    assert isinstance(value, str)
    try:
        timestamp = int(datetime.fromisoformat(value).timestamp())
    except (OSError, OverflowError, ValueError):
        return None
    return timestamp if _timestamp(timestamp) else None


def _timestamp_from_datetime(value: datetime) -> int | None:
    try:
        timestamp = int(_as_utc(value).timestamp())
    except (OSError, OverflowError, ValueError):
        return None
    return timestamp if _timestamp(timestamp) else None


def verification_payload_is_valid(kind: str, payload: Mapping[str, Any]) -> bool:
    """Validate the complete snapshot schema for a supported proof kind."""
    if (
        kind not in SUPPORTED_VERIFICATION_KINDS
        or not _positive_integer(payload.get("guild_id"))
        or not _aware_iso_timestamp(payload.get("issued_at"))
    ):
        return False

    if kind == FEMBOY_CARD_KIND:
        member_id = payload.get("member_id")
        issued_by_id = payload.get("issued_by_id")
        return bool(
            set(payload) == _CARD_PAYLOAD_KEYS
            and _positive_integer(member_id)
            and _positive_integer(payload.get("role_id"))
            and _positive_integer(issued_by_id)
            and issued_by_id == member_id
            and _bounded_text(payload, "member_name", 100)
            and _bounded_text(payload, "role_name", 100)
        )

    id_keys = ("channel_id", "message_id", "author_id", "issued_by_id")
    if set(payload) != _QUOTE_PAYLOAD_KEYS or not all(
        _positive_integer(payload.get(key)) for key in id_keys
    ):
        return False
    if not all(
        (
            _bounded_text(payload, "channel_name", 100),
            _bounded_text(payload, "author_name", 100),
            _bounded_text(payload, "issued_by_name", 100),
            _bounded_text(payload, "content", _MAX_SNAPSHOT_CONTENT),
            _aware_iso_timestamp(payload.get("source_created_at")),
        )
    ):
        return False

    message_url = payload.get("message_url")
    if not isinstance(message_url, str):
        return False
    match = _MESSAGE_URL_PATTERN.fullmatch(message_url)
    if match is None:
        return False
    return all(
        int(match.group(key)) == payload.get(key)
        for key in ("guild_id", "channel_id", "message_id")
    )


def _snapshot_digest(payload: Mapping[str, Any], salt: str) -> str:
    if _LOWER_HEX_32_PATTERN.fullmatch(salt) is None:
        raise ValueError("Invalid snapshot salt.")
    canonical = _canonical_json(payload)
    return hashlib.sha256(
        _SNAPSHOT_DOMAIN + bytes.fromhex(salt) + canonical
    ).hexdigest()


def _claims_for_snapshot(
    kind: str,
    payload: Mapping[str, Any],
    *,
    token_id: str,
    snapshot_salt: str,
    snapshot_sha256: str,
) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "v": VERIFICATION_VERSION,
        "iss": VERIFICATION_ISSUER,
        "jti": token_id,
        "kind": kind,
        "guild_id": str(payload["guild_id"]),
        "issued_by_id": str(payload["issued_by_id"]),
        "iat": _timestamp_from_iso(payload["issued_at"]),
        "snapshot_salt": snapshot_salt,
        "snapshot_sha256": snapshot_sha256,
    }
    if kind == FEMBOY_CARD_KIND:
        claims.update(
            member_id=str(payload["member_id"]),
            role_id=str(payload["role_id"]),
        )
    else:
        claims.update(
            channel_id=str(payload["channel_id"]),
            message_id=str(payload["message_id"]),
            author_id=str(payload["author_id"]),
            source_iat=_timestamp_from_iso(payload["source_created_at"]),
        )
    if not _claims_are_valid(claims):
        raise ValueError("Could not build valid verification claims.")
    return claims


def _sign_claims(
    claims: Mapping[str, Any],
    keyring: VerificationKeyring,
) -> str:
    payload_segment = _b64url_encode(_canonical_json(claims))
    signing_input = (
        f"{VERIFICATION_TOKEN_PREFIX}.{keyring.active_kid}.{payload_segment}"
    ).encode("ascii")
    signature = hmac.new(
        keyring.active_key,
        _TOKEN_DOMAIN + signing_input,
        hashlib.sha256,
    ).digest()
    token = f"{signing_input.decode('ascii')}.{_b64url_encode(signature)}"
    if len(token) > _MAX_TOKEN_LENGTH:
        raise ValueError("Verification proof exceeds the token size limit.")
    return token


def issue_verification(
    collection: _InsertCollection,
    keyring: VerificationKeyring,
    *,
    kind: str,
    payload: Mapping[str, Any],
    issued_at: datetime,
) -> str:
    """Sign a manifest, persist its bound snapshot, and return the proof token."""
    if not isinstance(keyring, VerificationKeyring):
        raise VerificationConfigurationError(
            "A valid content-verification keyring is required."
        )
    if kind not in SUPPORTED_VERIFICATION_KINDS:
        raise ValueError(f"Unsupported verification kind: {kind}")

    created_at = _as_utc(issued_at)
    stored_payload = dict(payload)
    stored_payload["issued_at"] = created_at.isoformat()
    if not verification_payload_is_valid(kind, stored_payload):
        raise ValueError(f"Invalid payload for verification kind: {kind}")

    for _ in range(_INSERT_ATTEMPTS):
        token_id = secrets.token_hex(16)
        snapshot_salt = secrets.token_hex(16)
        if (
            _LOWER_HEX_32_PATTERN.fullmatch(token_id) is None
            or _LOWER_HEX_32_PATTERN.fullmatch(snapshot_salt) is None
        ):
            raise VerificationStoreError("Secure random token generation failed.")
        snapshot_sha256 = _snapshot_digest(stored_payload, snapshot_salt)
        claims = _claims_for_snapshot(
            kind,
            stored_payload,
            token_id=token_id,
            snapshot_salt=snapshot_salt,
            snapshot_sha256=snapshot_sha256,
        )
        token = _sign_claims(claims, keyring)
        document = {
            "_id": token_id,
            "version": VERIFICATION_VERSION,
            "kid": keyring.active_kid,
            "token_id": token_id,
            "kind": kind,
            "claims": claims,
            "snapshot_salt": snapshot_salt,
            "snapshot_sha256": snapshot_sha256,
            "payload": stored_payload,
            "created_at": created_at,
            "token": token,
        }
        try:
            collection.insert_one(document)
        except DuplicateKeyError:
            continue
        except PyMongoError as exc:
            raise VerificationStoreError(
                "Could not persist the verification record."
            ) from exc
        return token

    raise VerificationStoreError("Could not allocate a unique verification proof.")


async def issue_verification_async(
    collection: _InsertCollection,
    keyring: VerificationKeyring,
    *,
    kind: str,
    payload: Mapping[str, Any],
    issued_at: datetime,
) -> str:
    """Issue a proof without blocking the Discord event loop on PyMongo I/O."""
    return await asyncio.to_thread(
        issue_verification,
        collection,
        keyring,
        kind=kind,
        payload=payload,
        issued_at=issued_at,
    )


def verification_document_is_valid(
    claims: VerifiedClaims,
    document: Mapping[str, Any] | None,
) -> bool:
    """Check that a stored snapshot is exactly the one bound by signed claims."""
    if not isinstance(claims, VerifiedClaims) or not isinstance(document, Mapping):
        return False
    document_keys = set(document)
    if document_keys not in (_LEGACY_DOCUMENT_KEYS, _DOCUMENT_KEYS):
        return False
    if (
        document.get("version") != VERIFICATION_VERSION
        or type(document.get("version")) is not int
        or document.get("kid") != claims.kid
        or document.get("token_id") != claims.token_id
        or document.get("kind") != claims.kind
        or document.get("snapshot_sha256") != claims.snapshot_sha256
    ):
        return False
    if document_keys == _DOCUMENT_KEYS:
        stored_token = document.get("token")
        if (
            document.get("_id") != claims.token_id
            or not isinstance(stored_token, str)
            or not hmac.compare_digest(stored_token, claims.token)
        ):
            return False
    elif document.get("_id") != verification_token_fingerprint(claims.token):
        return False

    stored_claims = document.get("claims")
    payload = document.get("payload")
    salt = document.get("snapshot_salt")
    created_at = document.get("created_at")
    if (
        not isinstance(stored_claims, Mapping)
        or not _claims_are_valid(stored_claims)
        or dict(stored_claims) != dict(claims.raw)
        or not isinstance(payload, Mapping)
        or not isinstance(salt, str)
        or _LOWER_HEX_32_PATTERN.fullmatch(salt) is None
        or salt != claims.raw["snapshot_salt"]
        or not isinstance(created_at, datetime)
        or not verification_payload_is_valid(claims.kind, payload)
    ):
        return False

    try:
        expected_snapshot = _snapshot_digest(payload, salt)
    except (TypeError, ValueError):
        return False
    if not hmac.compare_digest(expected_snapshot, claims.snapshot_sha256):
        return False

    if (
        payload.get("guild_id") != claims.guild_id
        or payload.get("issued_by_id") != claims.issued_by_id
        or _timestamp_from_iso(payload.get("issued_at")) != claims.issued_at
        or _timestamp_from_datetime(created_at) != claims.issued_at
    ):
        return False
    if claims.kind == FEMBOY_CARD_KIND:
        return bool(
            payload.get("member_id") == int(claims.raw["member_id"])
            and payload.get("role_id") == int(claims.raw["role_id"])
        )
    return bool(
        payload.get("channel_id") == int(claims.raw["channel_id"])
        and payload.get("message_id") == int(claims.raw["message_id"])
        and payload.get("author_id") == int(claims.raw["author_id"])
        and _timestamp_from_iso(payload.get("source_created_at"))
        == claims.raw["source_iat"]
    )
