# Contributing to OmniVoice Server

Thank you for your interest in contributing!

## Quick Start

1. Fork and clone the repository
2. Install: `pip install -e ".[dev]"`
3. Create a branch: `git checkout -b feature/your-feature`
4. Make changes and add tests
5. Run tests: `pytest tests/ -v`
6. Run linting: `ruff check omnivoice_server/ tests/`
7. Commit: `git commit -m "feat: your feature"`
8. Push and create PR

## Code Style

- Follow PEP 8
- Use type hints
- Max line length: 100
- Run `ruff check --fix` before committing

## Testing

- Write tests for new features
- Maintain 80%+ coverage
- Use pytest fixtures

## Commit Convention

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `test`: Tests
- `refactor`: Refactoring

## Questions?

Open an issue or discussion!
