---
name: nas-smart-home
description: Sanitized operational reference for an approved NAS Quark-to-library incremental download service. Not the private household-control skill or a marketplace plugin.
---

# NAS media-sync reference entry

This public entry only routes to the
[media-sync operational reference](references/topology-and-backlog.md).
It is not registered as a marketplace plugin and must not replace an existing
private `nas-smart-home` installation. Household inventory, SSH identities,
authentication, and smart-home control contracts belong to the owner's private
skill source, not this directory.

Use the reference when checking an already-approved `quark-library-sync`
deployment: startup execution, hourly-after-completion scheduling, Alist native
`/api/fs/copy` task queueing (`:5244/@manage/tasks/copy`), undone/same-size
idempotency, readable files, live progress, and targeted rollback.

Before NAS access, require the owner's authenticated SSH alias and run a single
connectivity check with `ConnectTimeout=5` and `BatchMode=yes`. If it fails, stop;
do not scan alternative ports or bypass SSH. Before approved modifications,
verify a fresh snapshot and separate configuration backup. Never infer
credentials, silently grant approval, delete media, or restart unrelated
services. Keep private verification evidence outside the public repository.
