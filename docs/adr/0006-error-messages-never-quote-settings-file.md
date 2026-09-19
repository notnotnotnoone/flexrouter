# 0006. Error messages never quote the settings file's contents

Date: 2026-09-18
Status: Accepted

## Context

A YAML parse error from PyYAML normally embeds a literal snippet of the offending source line. Because `config.yaml` can (still, per ADR-0003's deprecated path) contain a key typed inline, that snippet could be a secret — and it would land in a terminal, a log file, or a bug report without anyone intending it to.

## Decision

`load_config()` (`flexrouter/config.py`) catches the parse exception and never interpolates `str(e)`. It reports only the file path, the exception's type name, and — when PyYAML supplies a `problem_mark` — the 1-indexed line and column. The source text itself is never included, for both parse errors and (per `flexrouter/cli.py::doctor`) other reported problems.

## Consequences

- A parser error can no longer leak a credential through a stack trace, a CI log, or a screenshot.
- The tradeoff is diagnostic detail: "line 14, column 3, ScannerError" tells you where to look but not what's wrong there. Anyone debugging a malformed settings file has to open it themselves and read the indicated line rather than trusting the error message to show the problem.
