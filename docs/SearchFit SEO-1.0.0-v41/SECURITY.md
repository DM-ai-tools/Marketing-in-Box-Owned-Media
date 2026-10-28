# Security Policy

## Supported versions

The latest released version of the plugin is the only supported version. Please upgrade before reporting an issue.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for security problems.

Email **hi@searchfit.ai** with:

- What the issue is and what an attacker could achieve
- Steps to reproduce, ideally with a minimal example
- The plugin version and your Claude Code version

We aim to acknowledge reports within 3 business days and to keep you updated as we work on a fix. If you'd like credit in the changelog, say so and tell us how you'd like to be named.

## Scope

This plugin is a collection of Markdown prompt files. It ships no executable code and no bundled MCP server, so the realistic risk surface is prompt-level rather than code execution:

**In scope**

- Prompt injection paths — these skills instruct an agent to fetch, crawl, and analyze arbitrary user-supplied URLs, so content from a third-party site enters the agent's context. Reports showing that a crafted page can steer the agent into unintended actions are valuable and in scope.
- Guidance that could cause an agent to leak credentials, API keys, or private repository contents.
- Instructions that could lead an agent to make destructive or unintended changes to a user's site or codebase.

**Out of scope**

- Vulnerabilities in Claude Code itself — report those to Anthropic.
- Vulnerabilities in the SearchFit.ai platform or its MCP server — email the same address, but note it's a platform issue.
- SEO advice you disagree with. That's an ordinary issue or pull request.

## Safe harbor

We won't pursue or support legal action against anyone who reports in good faith, acts within this policy, and avoids privacy violations, service disruption, and access to data that isn't theirs.
