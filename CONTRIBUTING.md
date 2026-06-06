# Contributing

Thanks for helping improve contents-hub.

## Development Setup

```bash
git clone https://github.com/yansfil/contents-hub
cd contents-hub
uv sync --all-extras
./dev test
```

Useful commands:

```bash
./dev test
./dev web --vault ~/contents-vault
./dev daemon run --vault ~/contents-vault
```

## Code Guidelines

- Keep `contents_hub` as the only public Python package.
- Keep `contents-hub` as the only public CLI command.
- Do not add mandatory provider SDK dependencies to the base install.
- Put agent provider code behind runner modules and optional extras.
- Put channel SDK code behind adapter modules or examples.
- Preserve existing CLI, DB, web, digest, Lens, exploration, and promotion
  behavior with regression tests.
- Keep CLI JSON surfaces machine-readable and stable.

## Tests

Run the full suite before opening a PR:

```bash
./dev test
```

For changes to CLI or docs, also run:

```bash
contents-hub --help
python -m contents_hub --help
```

## Public Hygiene

Do not commit local planning state, vault data, personal paths, credentials, or
runtime logs. Use placeholders in docs and fixtures.
