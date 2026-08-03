# Alexandria authenticated local automation

Alexandria's automation API is a supplemental, loopback-only REST surface over
the same project, Task Bundle, Background Work, Produce, and Export authorities
used by the application. It is not a second control plane.

Task Bundles remain the primary portable ChatGPT workflow. MCP is deliberately
disabled because the current REST surface has no proven capability gap that
justifies another security boundary.

## Security contract

- Requests must arrive directly from an IPv4 or IPv6 loopback address.
- `Host` must be `127.0.0.1`, `localhost`, or `::1`, with an optional port.
- Requests with `Origin`, `Forwarded`, `X-Forwarded-*`, or `X-Real-IP` are
  rejected.
- Authentication uses `Authorization: Bearer ...`; query-string credentials are
  not accepted.
- Credentials are stored outside project data with `0600` permissions.
- Each credential has explicit scopes. Read access does not imply mutation
  access.
- Every mutation requires a one-time `X-Alexandria-Review-Token` and a unique
  `Idempotency-Key`.
- Review tokens are bound to the exact request body, credential, operation,
  scope, and expiration time. Replays and changed bodies fail closed.
- Automation does not expose generic shell execution, arbitrary file access, or
  provider/model/Voice credentials.

Do not place the bearer token in a browser, URL, source file, chat message, or
shell history.

## Provisioning

Run this locally inside Alexandria's Python environment:

```text
python app/automation_api.py provision
```

The command creates the credential record but does not print the bearer secret.
The default location is:

```text
~/.config/alexandria/automation/credential.json
```

Use `--scope` repeatedly to provision a narrower credential. Use `status` to
inspect the credential ID, fingerprint, scopes, and network policy without
returning the token.

## Calling the API without exposing the token in arguments

This Python example reads the credential locally and keeps the token out of the
URL and command line:

```python
import json
import urllib.request
from pathlib import Path

credential = json.loads(
    (Path.home() / ".config/alexandria/automation/credential.json")
    .read_text(encoding="utf-8")
)

request = urllib.request.Request(
    "http://127.0.0.1:4201/api/automation/capabilities",
    headers={
        "Host": "127.0.0.1:4201",
        "Authorization": f"Bearer {credential['token']}",
    },
)

with urllib.request.urlopen(request) as response:
    print(json.load(response))
```

Use Alexandria's actual local port. Direct HTTP clients must not send an
`Origin` or forwarded-client header.

## Scopes

| Scope | Allows |
| --- | --- |
| `automation:discover` | Capability and security-policy discovery |
| `state:read` | Redacted project flow and blockers |
| `work:read` | Redacted Background Work state |
| `work:cancel` | Reviewed cancellation of active work |
| `tasks:read` | Task Bundle registry, library, and download |
| `tasks:export` | Reviewed native Task Bundle creation |
| `tasks:import` | Reviewed native completed-result import |
| `operations:produce` | Reviewed native Produce execution |
| `operations:export` | Reviewed native Export execution |

## Read endpoints

```text
GET /api/automation/capabilities
GET /api/automation/state
GET /api/automation/blockers
GET /api/automation/work
GET /api/automation/tasks/registry
GET /api/automation/tasks/library
GET /api/automation/tasks/{task_id}/download
```

State and work responses omit project names, source filenames, filesystem
paths, provider secrets, Voice source material, private task payloads, and
free-form worker messages.

## Reviewed mutation flow

Every mutation has two phases.

1. Call the operation's `/review` endpoint.
2. Inspect the returned native plan or reviewed Task Bundle identity.
3. Send the returned `execute_payload` to the execution endpoint with:
   - `X-Alexandria-Review-Token: <review_token>`
   - `Idempotency-Key: <new unique key>`

The execute request must match the reviewed body exactly.

### Produce

```text
POST /api/automation/operations/produce/review
POST /api/automation/operations/produce/start
```

The review uses Alexandria's native Produce plan. Execution preserves native
optimistic fingerprints, scheduler backpressure, cancellation, and exact-once
publication behavior.

### Export

```text
POST /api/automation/operations/export/review
POST /api/automation/operations/export/start
```

The review uses Alexandria's native Export plan. Execution preserves the same
dependency fingerprint and joined publication gate as the application UI.

### Background Work cancellation

```text
POST /api/automation/work/cancel/review
POST /api/automation/work/cancel
```

Cancellation review is bound to the job's exact state and update timestamp.

### Task Bundle export and import

```text
POST /api/automation/tasks/export/review
POST /api/automation/tasks/export
POST /api/automation/tasks/import/review
POST /api/automation/tasks/import
```

Task Bundle export calls the same native bundle builder as the UI. Import review
validates completed results in private staging without creating a project
candidate. Execution revalidates the staged hashes and current project
dependencies, calls the native import route, and removes the private staged
copies.

## Deliberate exclusions

The automation API does not provide:

- shell or subprocess execution;
- arbitrary filesystem read/write;
- arbitrary HTTP fetching;
- provider, model, Voice, or credential management;
- unreviewed production mutation;
- remote network binding;
- browser-origin access;
- MCP tools.
