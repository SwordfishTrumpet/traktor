## Description

<!-- Provide a brief description of the changes in this PR -->

## Related Issue

<!-- Link to the issue this PR fixes or relates to using #issue_number -->
Fixes #(issue)

## Type of Change

<!-- Mark the relevant option(s) with an [x] -->
- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Code refactoring
- [ ] Performance improvement
- [ ] Test addition or update
- [ ] CI/CD improvement

## Changes Made

<!-- List the key changes made in this PR -->
- 
- 
- 

## Testing

<!-- Describe the testing you've done -->
- [ ] Unit tests pass (`uv run pytest`)
- [ ] Linting passes (`uv run ruff check src/ tests/`)
- [ ] Code is formatted (`uv run black src/ tests/`)
- [ ] Tested manually with real Plex/Trakt credentials
- [ ] Tested in Docker

## Checklist

<!-- Mark completed items with [x] -->
- [ ] My code follows the project's style guidelines (Black, 100 char line length)
- [ ] I have performed a self-review of my own code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes

## Screenshots / Logs (if applicable)

<!-- If UI changes or log output is relevant, include them here -->

## Additional Notes

<!-- Any additional information for reviewers -->

## Security Considerations

<!-- If your change affects authentication, authorization, or data handling, describe the security implications -->
- [ ] No sensitive data (tokens, credentials) is logged or exposed
- [ ] Token files maintain 0600 permissions
- [ ] No new external network calls without proper error handling
