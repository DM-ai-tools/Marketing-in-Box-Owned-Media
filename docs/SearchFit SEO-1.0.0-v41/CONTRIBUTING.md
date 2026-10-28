# Contributing

Thanks for helping improve the SearchFIT SEO Toolkit. It's free and MIT-licensed, and contributions are welcome.

## Licensing of contributions

By submitting a pull request, you agree that your contribution is licensed under the [MIT License](LICENSE), the same terms that cover the rest of this repository. We don't require a CLA.

## Building on this plugin

You do not need permission to build on this toolkit. MIT lets you use, modify, redistribute, and sell derivative work — including bundling these files into a commercial product — as long as you keep the copyright and license notice. You're welcome to tell us what you built, but you're not obliged to.

## What makes a good contribution

This repo is prompts, not code. Every file is Markdown:

| Directory   | What lives there                                       |
| ----------- | ------------------------------------------------------ |
| `skills/`   | Auto-activating capabilities (`<name>/SKILL.md`)        |
| `commands/` | Explicit slash commands (`<name>.md`)                   |
| `agents/`   | Specialized subagents (`<name>/AGENT.md`)               |

Good contributions usually:

- **Sharpen a description.** Skills activate off their `description` frontmatter. A vague description means the skill never fires; a sharper one is a real fix.
- **Fix guidance that produces bad SEO advice.** If a skill recommends something outdated or harmful, say so in the issue with a source.
- **Add a genuinely distinct capability.** Prefer improving an existing skill over adding a near-duplicate — overlapping skills compete to activate and make the whole toolkit less reliable.

## Before you open a PR

1. Confirm frontmatter is valid YAML and `name` matches the directory name.
2. Test it in a real Claude Code session — install the plugin locally and confirm the skill activates from a natural request, not just when named directly.
3. Keep the existing voice: direct, concrete, no filler.
4. One logical change per PR.

## Reporting bugs

Open an issue using the bug template. The site URL you ran against and your Claude Code version are the two things that make a report actionable.

## Security

Please don't open a public issue for security problems — see [SECURITY.md](SECURITY.md).
