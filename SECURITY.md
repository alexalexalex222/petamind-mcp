# Security Policy

## Reporting a vulnerability

If you believe you’ve found a security issue in Petamind MCP, please **do not open a public issue**.

Preferred reporting method:

- Use GitHub’s **private vulnerability reporting** / Security Advisories for the repository (if available).

If private reporting is not available for the hosting platform, open a GitHub issue with **minimal** information
and clearly mark it as security-sensitive so maintainers can coordinate a private channel.

## What to include

- A clear description of the issue and impact
- Steps to reproduce (as minimal as possible)
- Any proof-of-concept code (only if needed)
- Affected versions / commit SHA (if known)

## Scope

This project executes external commands and manipulates repositories. In particular, consider:

- Command execution boundaries (`test_command`, `lint_command`, `preview_command`)
- File path traversal issues when applying patches
- Leaking secrets through logs / run artifacts
- Supply chain risks in dependencies

## Response expectations

We aim to acknowledge reports within a few days and provide a mitigation plan as soon as practical.
