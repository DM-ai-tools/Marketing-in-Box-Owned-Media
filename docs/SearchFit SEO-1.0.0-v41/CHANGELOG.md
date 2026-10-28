# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `LICENSE` file (MIT). The README had declared MIT since 1.0.0, but with no license file present GitHub's license detection reported none, leaving the terms unclear for anyone building on the plugin.
- `.claude-plugin/marketplace.json`, so `/plugin marketplace add searchfit/searchfit-seo` works as the README documents.
- `license` and `keywords` fields in `.claude-plugin/plugin.json`.
- `CONTRIBUTING.md`, `SECURITY.md`, and GitHub issue templates.

### Fixed

- The `/plugin marketplace add` install path documented in the README previously failed, because the repository contained no marketplace manifest.

## [1.0.0] - 2026-03-06

### Added

- 11 skills: SEO Audit, Technical SEO, On-Page SEO, Broken Links, Internal Linking, Schema Markup, Content Strategy, Content Brief, Keyword Clustering, AI Visibility, Content Translation.
- 6 commands: `/seo-check`, `/create-content`, `/create-topic`, `/generate-schema`, `/keyword-cluster`, `/translate-content`.
- 3 agents: SEO Auditor, Content Strategist, Competitor Analyzer.

[Unreleased]: https://github.com/searchfit/searchfit-seo/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/searchfit/searchfit-seo/releases/tag/v1.0.0
