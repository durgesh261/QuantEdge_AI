"""The credential seam between QuantEdge production execution and Delta India.

WHY THIS MODULE EXISTS
----------------------
The repository already has a working credential loader -- `load_project_env()`
plus `DeltaIndiaClient.from_env()`, reading `DELTA_API_KEY` / `DELTA_API_SECRET`
from the project `.env`. That loader is a DEVELOPER/TEST-account source. It is
deliberately NOT the production credential design, because a production process
must take the credential of the AUTHENTICATED USER, from that user's Settings /
account configuration, through the application's existing architecture.

So the composition never names a credential source directly. It asks a provider.
This module is that seam, and nothing else: it opens no socket, contacts no
exchange, places no order, and imports no strategy.

WHAT THE EXISTING ARCHITECTURE ACTUALLY DOES (verified, not assumed)
--------------------------------------------------------------------
The authenticated user's Delta credential is stored and owned by the Java
backend, encrypted, and is deliberately never handed back out:

  * `POST /v1/settings/delta/connect` (`DeltaSettingsController`) accepts the
    user's key/secret and persists it through `AccountManagementService`,
    encrypted with `DeltaCredentialService` (AES-256-GCM).
  * `GET /v1/settings/delta` answers with `apiKeyMasked` ONLY. The controller's
    stated invariant: "API secrets are never returned in responses, query
    parameters, or error messages."
  * The engine-facing internal API (`/api/engine/*`, `X-Engine-Api-Key`) states
    of its account listing: "Zero credentials or encryption keys are ever
    returned."
  * `execution/backend_client.py` states the same contract from this side: "The
    Python engine NEVER holds credentials."

Therefore there is, today, NO endpoint through which this engine can obtain the
authenticated user's production credential. `UserSettingsCredentialProvider`
below is the production provider and it FAILS CLOSED saying exactly that, rather
than inventing a decrypt-and-transmit endpoint that no mandate authorised and
that would be a new secret-exfiltration surface. A missing production credential
delivery contract is a reported blocker, not a silent fallback to `.env`.

CREDENTIAL DISCIPLINE
---------------------
No provider returns a secret to its caller. The one thing a provider can do is
BUILD AN AUTHENTICATED CLIENT, so the plaintext goes straight from its source
into `DeltaIndiaClient` and is never bound to a name in composition code, never
logged, never serialised, never written to execution state, and never returned
through `describe()` -- which reports provenance only.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, Optional

from quantedge.execution.delta_client import DeltaIndiaClient

logger = logging.getLogger("credential_provider")

class CredentialUnavailableError(RuntimeError):
    """A usable Delta credential could not be obtained -- so nothing proceeds.

    Raised, never returned as a falsy value, and never downgraded to a warning:
    a production process that cannot prove which account it is acting for must
    not open a socket, must not compose an execution stack, and must certainly
    not fall back to a developer credential. The message names the missing piece
    so an operator can fix it; it never contains a key, a secret or a ciphertext.
    """


class DeltaCredentialProvider(ABC):
    """The one interface production composition uses to reach a credential.

    Two rules make this a seam rather than a wrapper:

      * `build_client()` is the ONLY way out. There is no `get_api_key()`, no
        `credentials` property and no dict of secrets, so composition code has
        no name bound to a secret and cannot accidentally log or persist one.
      * `describe()` is provenance only -- source label, production grade,
        account identity if known. Never a key, never a masked key, never a
        length. It is written on the assumption that its output WILL be logged.
    """

    #: Stable, human-readable identifier for the credential source. Appears in
    #: safe diagnostics so a report can never be ambiguous about whether a run
    #: used a developer credential or the authenticated user's.
    source_label: ClassVar[str] = "unspecified"

    #: False marks a source that must never be used to trade a real user's
    #: account. Production composition refuses such a provider unless the caller
    #: explicitly and separately opted into it.
    production_grade: ClassVar[bool] = False

    def describe(self) -> Dict[str, Any]:
        """Safe provenance metadata. Assume the result is logged verbatim."""
        return {
            "source": self.source_label,
            "production_grade": self.production_grade,
        }

    @abstractmethod
    def build_client(
        self,
        *,
        allow_direct_execution: bool,
        timeout_seconds: float = 10.0,
    ) -> DeltaIndiaClient:
        """Return an authenticated client, or raise `CredentialUnavailableError`.

        `allow_direct_execution` is REQUIRED and keyword-only on purpose. It is
        the client-level order-submission authority, and a default here would let
        a caller obtain a write-capable production client by forgetting an
        argument (safety rule #13: a default must never answer a safety
        question).
        """

class UserSettingsCredentialProvider(DeltaCredentialProvider):
    """THE PRODUCTION PROVIDER: the authenticated user's Settings credential.

    Fronts the credential the user themselves connected through
    `POST /v1/settings/delta/connect`, which the Java backend holds encrypted
    against their account. That is the only credential a production process may
    trade with, because it is the only one whose owner consented to it.

    It currently REFUSES, and the refusal is the honest state of the system, not
    a bug in this class: the backend deliberately exposes no route that returns a
    decrypted secret to the engine, and the engine deliberately promises never to
    hold one. Closing that gap means designing an authorised credential-delivery
    contract between `AccountManagementService` / `DeltaConnection` and this
    engine -- a new secret-handling surface, with its own authentication,
    audit trail, rotation story and blast radius. That is a separate, explicitly
    authorised piece of work; guessing at it here would be inventing security
    architecture under the cover of a wiring change.

    Until then this provider does what a fail-closed component does: it names the
    missing contract and stops. It does NOT fall back to `.env`.
    """

    source_label: ClassVar[str] = "authenticated-user-settings"
    production_grade: ClassVar[bool] = True

    def __init__(
        self,
        account_id: str,
        user_id: str,
        *,
        backend_base_url: Optional[str] = None,
    ) -> None:
        self.account_id = account_id
        self.user_id = user_id
        self.backend_base_url = backend_base_url

    def describe(self) -> Dict[str, Any]:
        info = super().describe()
        # Identities, not secrets. `account_id` / `user_id` are the same values
        # the engine-facing account listing already returns in clear.
        info["account_id"] = self.account_id
        info["user_id"] = self.user_id
        info["available"] = False
        info["blocked_by"] = "NO_ENGINE_CREDENTIAL_DELIVERY_CONTRACT"
        return info

    def build_client(
        self,
        *,
        allow_direct_execution: bool,
        timeout_seconds: float = 10.0,
    ) -> DeltaIndiaClient:
        raise CredentialUnavailableError(
            "the authenticated user's Delta credential cannot be obtained by "
            "this engine: the backend stores it encrypted against the user's "
            "account and exposes no route that returns it (DeltaSettingsController "
            "returns apiKeyMasked only; the engine API returns zero credentials), "
            "and execution/backend_client.py states the engine never holds "
            "credentials. An authorised engine credential-delivery contract must "
            "be designed before production execution can use a real user's "
            "account. Refusing to substitute a developer credential for "
            f"account={self.account_id!r} user={self.user_id!r}"
        )

class DeveloperEnvCredentialProvider(DeltaCredentialProvider):
    """DEVELOPER / TEST ACCOUNT ONLY. The project `.env` credential.

    This is the temporary developer credential source, and it is labelled as one
    so no report can present a run made with it as a production run. It exists
    for exactly one job: read-only validation against a developer's own Delta
    account.

    It adds NO loader. `DeltaIndiaClient.from_env()` -- the loader that already
    existed, with its own `load_project_env()` search and its own fail-closed
    `ValueError` on a blank key or secret -- is called unchanged. A second
    hand-rolled `.env` reader would be a second thing to get wrong.

    `allow_env_file` must be passed True explicitly. The default refuses, so a
    process that never opted in cannot reach a developer credential by accident.
    """

    source_label: ClassVar[str] = "developer-env-file"
    production_grade: ClassVar[bool] = False

    def __init__(self, *, allow_env_file: bool = False) -> None:
        self.allow_env_file = bool(allow_env_file)

    def describe(self) -> Dict[str, Any]:
        info = super().describe()
        info["opted_in"] = self.allow_env_file
        return info

    def build_client(
        self,
        *,
        allow_direct_execution: bool,
        timeout_seconds: float = 10.0,
    ) -> DeltaIndiaClient:
        if not self.allow_env_file:
            raise CredentialUnavailableError(
                "the developer .env credential source was not opted into; it is a "
                "developer/test-account source and must be requested explicitly "
                "(allow_env_file=True). Production must use the authenticated "
                "user's Settings credential"
            )
        try:
            client = DeltaIndiaClient.from_env(
                timeout_seconds=timeout_seconds,
                allow_direct_execution=allow_direct_execution,
            )
        except ValueError as exc:
            # `from_env` raises with the variable NAMES only. Re-raised as the
            # seam's own fail-closed type, with no value interpolated.
            raise CredentialUnavailableError(
                f"the developer .env credential is absent or unusable: {exc}"
            ) from exc
        logger.warning(
            "DEVELOPER credential source in use | source=%s production_grade=%s "
            "order_submission_authority=%s -- this is NOT a production credential",
            self.source_label, self.production_grade, allow_direct_execution,
        )
        return client

def resolve_credential_provider(
    *,
    account_id: str,
    user_id: str,
    allow_developer_env: bool = False,
    backend_base_url: Optional[str] = None,
) -> DeltaCredentialProvider:
    """Choose the provider for one composition. Production first, always.

    `allow_developer_env=False` -- the default -- returns the production provider
    unconditionally, so the ordinary path is the authenticated user's credential
    and a caller cannot land on a developer credential by omission.

    `allow_developer_env=True` is the deliberate developer/read-only-validation
    path. Even then the returned provider announces itself as
    `production_grade=False`, which is what the composition checks before it
    would ever arm order submission.
    """
    if allow_developer_env:
        return DeveloperEnvCredentialProvider(allow_env_file=True)
    return UserSettingsCredentialProvider(
        account_id=account_id,
        user_id=user_id,
        backend_base_url=backend_base_url,
    )


__all__ = [
    "CredentialUnavailableError",
    "DeltaCredentialProvider",
    "UserSettingsCredentialProvider",
    "DeveloperEnvCredentialProvider",
    "resolve_credential_provider",
]
