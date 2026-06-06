"""Canonical naming policy for contents-hub.

This module is intentionally declarative. Public surfaces should import these
values instead of restating string literals.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NameAlias:
    """A canonical value.

    The tuple fields are intentionally empty in the open-source surface. The
    class remains so existing internal call sites can keep using `.canonical`
    and `.all` without carrying public aliases.
    """

    canonical: str
    legacy: tuple[str, ...] = ()

    @property
    def all(self) -> tuple[str, ...]:
        return (self.canonical, *self.legacy)


PRODUCT_NAME = NameAlias(canonical="contents-hub")
DISTRIBUTION_NAME = PRODUCT_NAME
CLI_COMMAND = PRODUCT_NAME
PYTHON_PACKAGE = NameAlias(canonical="contents_hub")
MCP_SERVER_NAME = PYTHON_PACKAGE

VAULT_ENV_VARS = NameAlias(canonical="CONTENTS_HUB_VAULT")
METADATA_DIR = NameAlias(canonical=".contents-hub")
CONFIG_FILE = NameAlias(canonical=".contents-hub.yaml")
LAUNCHD_LABEL = NameAlias(canonical="com.contents-hub.daemon")
CHROMUX_PROFILE = PRODUCT_NAME

VAULT_RESOLUTION_ORDER = (
    "--vault",
    VAULT_ENV_VARS.canonical,
    "cwd",
)

# Warnings must not be printed to stdout by commands with a machine-readable
# stdout contract.
COMPATIBILITY_WARNING_STREAM = "stderr"

__all__ = [
    "CHROMUX_PROFILE",
    "CLI_COMMAND",
    "COMPATIBILITY_WARNING_STREAM",
    "CONFIG_FILE",
    "DISTRIBUTION_NAME",
    "LAUNCHD_LABEL",
    "MCP_SERVER_NAME",
    "METADATA_DIR",
    "NameAlias",
    "PRODUCT_NAME",
    "PYTHON_PACKAGE",
    "VAULT_ENV_VARS",
    "VAULT_RESOLUTION_ORDER",
]
