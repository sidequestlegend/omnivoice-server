# Security Policy

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

If you discover a security vulnerability in omnivoice-server, please report it privately:

- **Email**: matthew.ngo1114@gmail.com
- **Subject**: [SECURITY] Brief description of the issue

Alternatively, you can use GitHub's private vulnerability reporting feature (enabled for this repository):

1. Go to the [Security tab](https://github.com/maemreyo/omnivoice-server/security)
2. Click "Report a vulnerability"
3. Fill out the form with details

## What to Include

Please include the following information in your report:

- Description of the vulnerability
- Steps to reproduce the issue
- Potential impact
- Suggested fix (if you have one)
- Your contact information for follow-up

## Response Timeline

- **Initial response**: Within 48 hours
- **Status update**: Within 7 days
- **Fix timeline**: Depends on severity (critical issues prioritized)

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Security Best Practices

When deploying omnivoice-server:

1. **Authentication**: Always set `OMNIVOICE_API_KEY` in production
2. **Network**: Bind to `127.0.0.1` (localhost) by default, use reverse proxy for external access
3. **File uploads**: The server validates audio file size and format, but consider additional validation at the reverse proxy level
4. **Rate limiting**: Implement rate limiting at the reverse proxy or load balancer level
5. **HTTPS**: Always use HTTPS in production (configure at reverse proxy level)

## Known Security Considerations

- **Voice cloning**: This server enables voice cloning. Users are responsible for ensuring they have rights to clone voices and comply with applicable laws
- **Resource limits**: Set appropriate `--max-concurrent` and `--timeout` values to prevent resource exhaustion
- **Profile directory**: The `--profile-dir` should have restricted permissions (not world-readable)

## Acknowledgments

We appreciate responsible disclosure and will acknowledge security researchers who report vulnerabilities (with permission).
