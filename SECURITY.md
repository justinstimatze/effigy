# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in effigy, please report it responsibly by emailing **justin@justinstimatze.com** rather than opening a public issue.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

You should receive a response within 72 hours.

## Scope

Effigy is a notation parser and prompt context generator. It does not make network calls, manage authentication, or store credentials (Layers 1-2). The optional Layer 3 memory synthesis and the discovery loop (`effigy.discovery`) make LLM API calls when explicitly invoked by the caller.

The `effigy.parser` module processes user-provided `.effigy` text. While there is no `eval()` or dynamic code execution, pathological input (extremely large files, deeply nested blocks) could cause memory or CPU exhaustion. Input validation is the caller's responsibility.
