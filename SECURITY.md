# Security Policy

## Supported versions

Only the latest release on `master` receives security fixes.

| Version | Supported |
| ------- | --------- |
| 1.3.x   | Yes       |
| < 1.3   | No        |

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report them privately through GitHub: go to the repository's **Security** tab and choose **Report a vulnerability**. You will get a response as soon as the maintainer can look at it. Please include:

- what you found and where (file and command),
- steps to reproduce,
- the impact you expect.

## Things to know about how CLI CODER works

- **API keys** are stored in a local `.env` file and sent only to the provider you choose. `.env` is git-ignored. Do not commit it, and do not paste it into issues.
- **`/run`** executes the shell command you type, with your permissions. Nothing runs without you entering it.
- **`/code`** starts [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) in `-y` (no confirmation) mode in the current folder. Only use it in folders you are happy for an AI agent to change.
- **`/hermes`** downloads and runs an install script from the internet. It asks for confirmation first.
- **Dependencies** are pinned in `requirements.txt`. `litellm` 1.82.7 and 1.82.8 were malicious PyPI uploads in March 2026, so the project stays on 1.82.6. Do not upgrade it without checking the advisories first.
