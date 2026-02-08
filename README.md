# gitops-replacer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/gitops-replacer.svg)](https://pypi.org/project/gitops-replacer/)
[![PyPI downloads](https://img.shields.io/pypi/dm/gitops-replacer.svg)](https://pypi.org/project/gitops-replacer/)

A lightweight CLI tool that automates value updates in GitOps repositories using marker comments. Replace values across multiple GitHub repositories with a single command, enabling automated deployment workflows.

## Features

- **Marker-based Approach**: Uses `# gitops-replacer: <name>` comments to locate values
- **Format Preservation**: No YAML parsing - comments, quotes, and formatting are preserved
- **Multiple Dependencies**: Update different values in the same file via unique markers
- **Flexible Modes**: Dry-run for validation, apply mode for commits
- **CI/CD Integration**: Built-in CI mode with `GITHUB_REF` pattern matching
- **Multiple Repositories**: Update values across any number of repos and files
- **Configuration Formats**: JSON (default) and YAML support
- **Performance Optimized**: Response caching eliminates duplicate API calls
- **Robust HTTP**: Automatic retries, timeouts, and error handling

## Requirements

- Python 3.10+
- A GitHub/GitHub Enterprise token with content read/write access

## Installation

### Via pip (recommended)

```bash
# Install from PyPI
pip install gitops-replacer

# Verify installation
gitops-replacer --help
```

### From source

```bash
# Clone repository
git clone https://github.com/slauger/gitops-replacer.git
cd gitops-replacer

# Install in development mode
pip install -e .

# Or run directly
python -m gitops_replacer --help
```

## Quick Start

1. Add marker comments to your target files (see [Marker Format](#marker-format))
2. Create a configuration file (default: `gitops-replacer.json`)
3. Run a dry-run:
   ```bash
   gitops-replacer "1.2.3"
   ```
4. Apply changes (commit to target repos):
   ```bash
   gitops-replacer --apply "1.2.3"
   ```

## Marker Format

Add a comment **above** the line you want to update:

```yaml
dependencies:
  # gitops-replacer: eibtalerhof-mcp-server
  - name: eibtalerhof-mcp-server
    version: "0.0.0-e0f72bb"
    repository: oci://registry.apps.lnxlabs.de/eibtalerhof

  # gitops-replacer: another-chart
  - name: another-chart
    version: "1.0.0"
    repository: oci://registry.example.com/charts
```

The tool will:
1. Find the line with `# gitops-replacer: <depName>`
2. Replace the value on the **next line** (preserving key, quotes, and formatting)

### Examples

**Chart.yaml (Helm dependency version):**
```yaml
dependencies:
  # gitops-replacer: eibtalerhof-mcp-server
  - name: eibtalerhof-mcp-server
    version: "0.0.0-e0f72bb"
    repository: oci://registry.apps.lnxlabs.de/eibtalerhof
```

**values.yaml (image tag):**
```yaml
# gitops-replacer: mcp-server-image
image: registry.apps.lnxlabs.de/eibtalerhof/mcp-server:1.2.3
```

**Note:** Only YAML files are supported (JSON has no comments). GitOps manifests are typically YAML.

## CLI

```text
usage: gitops-replacer [-h] [--config <file>] [--apply] [--ci]
                        [--name <string>] [--email <string>]
                        [--message <string>] [--api <string>]
                        [--verbose]
                        <string>
```

- `--config` Path to the configuration file (default: `gitops-replacer.json`). JSON recommended.
- `--apply` Apply changes (commit). Without this flag the tool runs in dry-run.
- `--ci` CI mode: validates `GITHUB_REF` against `when`/`except` regex patterns from config.
- `--name` Commit author name (default: env `GIT_COMMIT_NAME` or `Replacer Bot`).
- `--email` Commit author email (default: env `GIT_COMMIT_EMAIL` or `replacer-bot@localhost.localdomain`).
- `--message` Commit message template (default: `fix: update {} to {}`). First `{}` is depName, second is value.
- `--api` GitHub API URL (default: env `GITHUB_API_URL` or `https://api.github.com`).
- `--verbose` Print file contents and desired state (use with care in CI logs).
- Positional: `value` - the new value to set at the marked location.

### Environment

- `GITHUB_TOKEN` **(required)** – token with access to read/write repository contents.
- `GITHUB_REF` *(required when `--ci`)* – the current ref string, e.g., `refs/heads/main`. Falls back to `GIT_REF` for backwards compatibility.

Recommended token scopes:
- Public repos only: `public_repo`
- Private repos: `repo`
- GitHub Enterprise: equivalent content permissions

## Configuration

Default format is **JSON**. YAML (`.yaml`/`.yml`) is supported as well.

### JSON schema (per entry)

```json
{
  "gitops-replacer": [
    {
      "repository": "slauger/gitops",
      "branch": "main",
      "file": "apps/eibtalerhof-mcp-server/Chart.yaml",
      "depName": "eibtalerhof-mcp-server",
      "when": "^refs/heads/main$"
    }
  ]
}
```

**Fields**

| Field | Description |
|-------|-------------|
| `repository` | Target repo on GitHub (`ORG/REPO` format) |
| `branch` | Target branch |
| `file` | Target file path relative to repo root |
| `depName` | Dependency name (must match marker in file) |
| `when` | Regex that must match `GITHUB_REF` when `--ci` is enabled (optional) |
| `except` | Regex that must **not** match `GITHUB_REF` when `--ci` is enabled (optional) |

> The tool uses `re.match` (anchored at the string start). Use `^...$` in your patterns if you require a full match.

### Examples

**JSON (default)**

```json
{
  "gitops-replacer": [
    {
      "repository": "acme/gitops",
      "branch": "main",
      "file": "apps/my-app/Chart.yaml",
      "depName": "my-app",
      "when": "^refs/heads/(main|release/.*)$"
    },
    {
      "repository": "acme/gitops",
      "branch": "develop",
      "file": "apps/my-app-dev/Chart.yaml",
      "depName": "my-app",
      "except": "^refs/heads/legacy/"
    }
  ]
}
```

**YAML (alternative)**

```yaml
gitops-replacer:
  - repository: acme/gitops
    branch: main
    file: apps/my-app/Chart.yaml
    depName: my-app
    when: '^refs/heads/(main|release/.*)$'
  - repository: acme/gitops
    branch: develop
    file: apps/my-app-dev/Chart.yaml
    depName: my-app
    except: '^refs/heads/legacy/'
```

## How it works

1. **Validation**: Checks CLI arguments, environment variables, and configuration file
2. **Precheck Phase**: Validates access to all target repositories/files (caches responses)
3. **Replace Phase**: Downloads files (reuses cached data), finds marker comments, replaces values
4. **Commit Phase**: If `--apply` is set and changes detected, commits via GitHub Contents API
5. **Exit Codes**: Returns `0` on success, non-zero on failures

### Why Marker-based?

Traditional approaches parse YAML, modify the data structure, and serialize back. This often breaks:
- Comments are lost
- Quote styles change (`"1.0"` becomes `'1.0'` or `1.0`)
- Key ordering may change
- Multi-line strings get reformatted

The marker-based approach works on raw text:
- **Explicit**: Only marked lines are modified
- **Safe**: No risk of unintended changes
- **Preserving**: Comments, quotes, and formatting stay intact

## Exit Codes

- `0` success (no changes or committed changes)
- `1` validation or API error

## Use Cases

### Automated Deployment Pipeline

Update chart version when a new release is built:

```bash
# In your CI/CD pipeline after publishing a chart
gitops-replacer --ci --apply "0.1.0-abc123"
```

### Multi-Environment Updates

Use CI mode to update different environments based on branch:

```json
{
  "gitops-replacer": [
    {
      "repository": "myorg/gitops",
      "branch": "main",
      "file": "apps/production/Chart.yaml",
      "depName": "myapp",
      "when": "^refs/heads/main$"
    },
    {
      "repository": "myorg/gitops",
      "branch": "main",
      "file": "apps/staging/Chart.yaml",
      "depName": "myapp",
      "when": "^refs/heads/(main|develop)$"
    }
  ]
}
```

## Troubleshooting

### Common Issues

**401 Unauthorized**
- Verify `GITHUB_TOKEN` is set correctly
- Check token has `repo` or `public_repo` scope
- For GitHub Enterprise, confirm token has access to the organization

**404 Not Found**
- Verify `repository`, `branch`, and `file` paths in config
- Check branch name spelling (case-sensitive)
- Ensure file exists at the specified path

**No marker found**
- Confirm the marker comment exists in the target file
- Check `depName` in config matches the marker exactly
- Marker format: `# gitops-replacer: <depName>`

**No changes detected**
- The current value already matches the new value
- Use `--verbose` to see file contents

### Debug Mode

Run with `--verbose` to see:
- Full API URLs being called
- Complete file contents before replacement
- Desired file contents after replacement

**Warning**: Verbose mode may expose sensitive data in logs.

## Contributing

Contributions are welcome! Please ensure:
- Code follows existing style and patterns
- Changes are tested with both dry-run and apply modes
- Documentation is updated for new features

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
