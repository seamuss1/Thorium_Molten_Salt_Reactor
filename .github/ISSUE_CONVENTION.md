# GitHub issue classification

Use exactly one work-queue label on every new issue:

| Label | Meaning |
| --- | --- |
| `owner-requested` | The owner explicitly asked to track the work, including when an agent creates the issue for them. Primary work queue. |
| `auto-generated` | An unsolicited agent, audit, automation, or review follow-up. Secondary backlog unless the owner adopts it. |

The GitHub author is not evidence of owner intent: agents may use the owner's
account. Requesting a review does not make every finding owner-requested.
Describe the request or finding source in the issue body without private chat
transcripts, credentials, or internal infrastructure details.

Use the corresponding template, or add the queue label alongside an existing
specialized template. CLI/API creators must explicitly supply the label; templates
do not enforce API behavior. Preserve existing topic, priority, severity, and
Project fields. Where used, `auto-review-p2` and `auto-review-p3` remain additional
labels on automated review follow-ups.

Prefer owner-requested work when choosing routine backlog tasks. This is not a
severity downgrade: urgent security/safety findings and P1 blockers still need
attention. Existing repository review, acceptance, and deployment rules apply.

If the owner explicitly adopts an automated issue, replace `auto-generated` with
`owner-requested`, preserve review/severity labels, and record the adoption in the
body. Merely asking to inspect an issue is not adoption. Do not leave both queue
labels on one issue. Historical issues with uncertain provenance stay unclassified;
neither missing labels nor a broad audit proves an explicit owner request.

## Filters

In this repository's Issues search:

- Owner queue: `is:issue is:open label:owner-requested`
- Automated backlog: `is:issue is:open label:auto-generated`
- Unclassified: `is:issue is:open -label:owner-requested -label:auto-generated`

For an account-wide view, add `user:YOUR_GITHUB_USERNAME` to the same query at
GitHub's global issue search. Queue labels classify issues, not pull requests.
