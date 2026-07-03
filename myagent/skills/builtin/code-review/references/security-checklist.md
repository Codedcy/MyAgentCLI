# Security Checklist

This checklist guides code review for common security vulnerabilities.

## Injection
- [ ] SQL queries use parameterized queries / ORM
- [ ] Shell commands avoid user input or are properly sanitized
- [ ] File paths are validated to prevent path traversal

## Authentication & Authorization
- [ ] Authentication logic is in a centralized, tested location
- [ ] Authorization checks happen before any data access
- [ ] Session tokens are stored securely (HttpOnly, Secure, SameSite)

## Data Handling
- [ ] Sensitive data is not logged or exposed in error messages
- [ ] Passwords and secrets are never hardcoded
- [ ] Environment variables are used for sensitive configuration
- [ ] Data is validated on both client and server side

## Cryptography
- [ ] Standard cryptographic libraries are used (no custom crypto)
- [ ] Keys are rotated and stored in a secure key store
- [ ] HTTPS is enforced for all external communication

## Dependency Security
- [ ] Dependencies are pinned to specific versions
- [ ] No known vulnerabilities in dependency tree
- [ ] Regular dependency audits are configured

## File System
- [ ] File uploads are validated for type and size
- [ ] Uploaded files are stored outside web root
- [ ] Temporary files are cleaned up after use
