from contents_hub.naming import (
    CHROMUX_PROFILE,
    CLI_COMMAND,
    COMPATIBILITY_WARNING_STREAM,
    CONFIG_FILE,
    DISTRIBUTION_NAME,
    LAUNCHD_LABEL,
    MCP_SERVER_NAME,
    METADATA_DIR,
    PRODUCT_NAME,
    PYTHON_PACKAGE,
    VAULT_ENV_VARS,
    VAULT_RESOLUTION_ORDER,
)


def test_canonical_contents_hub_names_are_primary():
    assert PRODUCT_NAME.canonical == "contents-hub"
    assert DISTRIBUTION_NAME.canonical == "contents-hub"
    assert CLI_COMMAND.canonical == "contents-hub"
    assert PYTHON_PACKAGE.canonical == "contents_hub"
    assert MCP_SERVER_NAME.canonical == "contents_hub"


def test_legacy_llm_wiki_names_remain_explicit_aliases():
    assert PRODUCT_NAME.legacy == ("llm-wiki",)
    assert CLI_COMMAND.legacy == ("llm-wiki",)
    assert PYTHON_PACKAGE.legacy == ("llm_wiki",)
    assert MCP_SERVER_NAME.legacy == ("llm_wiki",)


def test_runtime_compatibility_policy_is_centralized():
    assert VAULT_RESOLUTION_ORDER == (
        "--vault",
        "CONTENTS_HUB_VAULT",
        "LLM_WIKI_VAULT",
        "cwd",
    )
    assert VAULT_ENV_VARS.all == ("CONTENTS_HUB_VAULT", "LLM_WIKI_VAULT")
    assert METADATA_DIR.all == (".contents-hub", ".llm-wiki")
    assert CONFIG_FILE.all == (".contents-hub.yaml", ".llm-wiki.yaml")
    assert LAUNCHD_LABEL.all == ("com.contents-hub.daemon", "com.llm-wiki.daemon")
    assert CHROMUX_PROFILE.all == ("contents-hub", "llm-wiki")


def test_compatibility_warning_policy_preserves_json_stdout():
    assert COMPATIBILITY_WARNING_STREAM == "stderr"
