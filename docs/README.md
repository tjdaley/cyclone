# Cyclone documentation

| | For | |
| - | --- | - |
| [**User guide**](user-guide/README.md) | Paralegals, legal assistants, and the attorneys looking over their shoulder | Doing the work: clients, matters, statement discovery, categorizing, exhibits, compliance |
| [**Operator guide**](operator-guide/README.md) | Whoever runs the servers | Configuration, secrets, deploys, and proving the nodes agree |
| [**Client guide**](client-guide/README.md) | A client given portal access | One page: what they can see, how marking a transaction works, and what a struck-through tag means. **Published in the portal** as its *How this works* tab, by the same sync script as the user guide |

## Runbooks

Procedures rather than explanation. Follow them step by step when the time
comes; the operator guide says *why* each one is shaped the way it is.

| | |
| - | - |
| [`aws-ssm-setup.md`](aws-ssm-setup.md) | The full AWS build-out for SSM-backed secrets: IAM, instance metadata, cutover, rollback, Terraform |
| [`supabase-key-migration.md`](supabase-key-migration.md) | Swapping the legacy Supabase JWT keys before Supabase retires them at the end of 2026 |

## Proposals — not built

| | |
| - | - |
| [`specs/document-labeling/`](specs/document-labeling/README.md) | The document pipeline: scan, split, OCR, label, dispatch, and a Rule 196.3(c) production index. **Partly built now** — receiving, routing and the splitting bench ship; classification and the index do not. `SPEC_splitting_bench.md` §8 marks what is done. The three as-received files came from a chat session with no access to this repo and are superseded |

## Reference and working notes

| | |
| - | - |
| [`erd.md`](erd.md) | The database, table by table |
| [`config-hardening-notes.md`](config-hardening-notes.md) | Why the configuration work was done, what shipped, and what was deliberately left undone |
| [`notes-for-doc-update.md`](notes-for-doc-update.md) | Developer handoff notes behind the operator guide — the reasoning the diff does not carry |
| [`../config/README.md`](../config/README.md) | The committed, non-secret half of the configuration |
| [`../CLAUDE.md`](../CLAUDE.md) | Project conventions, and the design decisions behind every service |

> The user guide and the operator guide are written for different people and
> overlap nowhere. The one place they touch is roles: in-app permissions are
> [user guide, Chapter 12](user-guide/12-user-roles.md); AWS permissions are
> [operator guide, Chapter 2](operator-guide/02-secrets.md).
