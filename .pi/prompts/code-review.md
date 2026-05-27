Review this code for security issues, performance problems, and anti-patterns.

Focus areas:
- **Security**: SQL injection, path traversal, unsafe eval, hardcoded secrets, insufficient input validation
- **Performance**: N+1 queries, unnecessary allocations, blocking I/O in async paths, missing caching
- **Maintainability**: Duplicated logic, missing type hints, over-complicated control flow, dead code
- **FastAPI patterns**: Proper dependency injection, response models, background tasks, lifespan events
- **Python**: PEP8, ruff compliance, proper exception handling, resource cleanup (context managers)
- **Tests**: Missing edge cases, untested error paths, brittle mocks

Output format:
1. **Summary**: One-sentence verdict (PASS / NEEDS WORK / BLOCKING)
2. **Critical findings**: Any security or correctness issues
3. **Recommendations**: Prioritized list of improvements with file:line references
4. **Time estimate**: Rough effort to address findings

Be concise. Prefer actionable suggestions over explanation.
