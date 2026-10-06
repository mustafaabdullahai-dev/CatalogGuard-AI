# Phase 28 — Docker / deployment

**Status:** Not started
**§64 scope:** Docker/deployment

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §47 Docker
- §45 Environment variables

## Entry conditions

- A live database and queue to deploy against.

## Exit criteria

- Containers for API and worker, with `.env` as the only secret source.
- Migration runs as a deployment step, not by the app at boot.
- Health and readiness wired to `/health` and `/ready`.

## Notes

- `docker/` exists and is empty.
