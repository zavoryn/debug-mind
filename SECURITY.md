# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in DebugMind, please report it via:

- Email: open a GitHub issue with "Security" label (preferred for non-sensitive issues)
- For sensitive vulnerabilities: create a [private security advisory](https://github.com/zavoryn/debug-mind/security/advisories/new)

We aim to respond within 7 days and provide a timeline for a fix.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Security Considerations for Users

- **MCP token**: Set `DEBUG_MIND_MCP_TOKEN` to restrict write access to your MCP server
- **API keys**: Store Anthropic API keys in environment variables or `.env` files (never commit them)
- **Memory directory**: The `memory/` directory contains bug case data — restrict filesystem access as appropriate
