# Contributing to Home Monitor

Thank you for your interest in contributing to Home Monitor! This document provides guidelines and instructions for contributing.

## Getting Started

1. **Fork the repository** and clone your fork locally
2. **Set up your development environment**:
   ```bash
   make setup-env
   # Edit .env and sites.json with your configuration
   make deps
   make infra-up-local
   make init-db-local
   ```

3. **Make your changes** following the project's coding standards (see below)

4. **Test your changes**:
   ```bash
   make format      # Auto-format code
   make lint        # Check code style
   make test        # Run unit tests
   ```

5. **Submit a pull request** with a clear description of your changes

## Code Quality Standards

- **Formatting**: Code must be formatted with `black` and `isort`
  ```bash
  make format
  ```

- **Linting**: Code must pass `flake8` checks
  ```bash
  make lint
  ```

- **Type hints**: Use type hints where appropriate (especially for function parameters and return values)

- **Documentation**: Add docstrings to new functions and classes

## Adding New API Integrations

If you're adding a new API integration, follow these steps:

1. **Create API client** in `home_monitor/apis/` following existing patterns
2. **Add configuration** in `home_monitor/config.py`
3. **Add fetcher function** in `home_monitor/fetcher.py`
4. **Update site config validation** in `home_monitor/site_config.py`
5. **Add test function** in `scripts/test_service.py`
6. **Update documentation** (README.md, env.example, sites.example.json)

See `AGENTS.md` for detailed guidelines on adding new API integrations.

## Database Schema Changes

- **Backward compatibility**: All schema changes must be backward-compatible
- **Use helpers**: Use `_add_columns_if_not_exists()` for adding columns
- **Raw data column**: Always keep `raw_data` as the last column
- **Migration scripts**: Never drop columns/tables in `init_database()` - use separate migration scripts if needed

## Commit Messages

Write clear, descriptive commit messages:
- Use the imperative mood ("Add feature" not "Added feature")
- Keep the first line under 72 characters
- Add more details in the body if needed

## Pull Request Process

1. **Update documentation** if you've changed functionality
2. **Add tests** for new features when possible
3. **Ensure all checks pass** (formatting, linting, tests)
4. **Request review** from maintainers

## Questions?

If you have questions or need help, please open an issue for discussion.

Thank you for contributing! 🎉
