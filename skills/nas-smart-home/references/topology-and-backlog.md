# NAS media-sync operational reference

This is a sanitized deployment reference, not an installable public skill package.
The `nas-smart-home` skill and household inventory remain in the private skill
repository; SSH identities, credentials, device entities, and network topology
are intentionally not reproduced here.

## Quark → local media library

| Component | Responsibility |
|---|---|
| `alist` | Authenticated Quark gateway and native copy-task executor |
| `quark-mount` | Existing read-only browsing/mount entry; not the download scheduler |
| `quark-library-sync` | Hourly incremental discovery and native copy-task submission |

The download service is declared alongside the existing services in
`/volume1/docker/openlist/docker-compose.yaml`. Its executable,
`/volume1/docker/openlist/rclone/sync_to_library.sh`, invokes the colocated
`alist_copy_sync.py`; the directory name is retained for deployment compatibility,
not because downloads still use `rclone copy`. Downloads land in
`/volume1/迅雷下载/Media/library/tv`; logs are written to
`/volume1/docker/openlist/data/log/quark-library-sync.log`.

### Schedule and copy rules

- Run one discovery/enqueue round immediately on container start, then wait
  **3600 seconds after that round finishes** before starting the next. Alist
  executes queued downloads independently; a round does not wait for the full
  library to finish downloading. This is not a fixed wall-clock cron schedule.
- Use `restart: unless-stopped` and a host-visible, non-blocking
  `flock -n /tmp/quark_library_sync.lock` to avoid overlapping submission rounds.
  A concurrent manual invocation must log a skip and exit.
- Authenticate locally through `/api/auth/login` using protected NAS-side
  configuration. Never put passwords or tokens in the repository, logs, command
  output, or public receipts.
- Recursively scan `/quark/来自：分享`, retaining its child directory structure
  under `library/tv`; also scan eligible user content under `/quark` without
  visiting the shared prefix twice. Prioritize directories containing `芙莉莲`
  before other series when submitting new tasks.
- Prune `夸克精选壁纸`, `夸克自动转存测试`, `夸克网盘使用指南*`,
  `夸克网盘能干什么.mp4`, and names containing `招新` before media selection.
- Include only videos (`mp4`, `mkv`, `avi`, `mov`, `ts`, `flv`, `webm`, `m4v`,
  `iso`, `rmvb`), subtitles (`ass`, `srt`, `ssa`, `sub`, `sup`), and `nfo` metadata.
- Before enqueueing, read `/api/admin/task/copy/undone`. Skip existing queued or
  running copies and same-path, same-size completed local files. Preserve full
  source/destination paths when identifying a task: episode basenames alone
  collide across seasons. Fail closed if authentication or queue discovery
  fails; do not blindly retry an ambiguous copy submission.
- Create the corresponding local destination directory, then submit
  `POST /api/fs/copy` with `src_dir`, `dst_dir`, and `names: [filename]`, mapping
  `/quark/...` to `/local/library/tv/...`. This is incremental copy, not
  destructive `sync`; completed files are not deleted. Same-size content changes
  are deliberately not detected. Transfer concurrency belongs to Alist's task
  executor, not the old rclone transfer/stream flags.
- Run `chmod -R a+rX` on the TV library after the round so the local media manager
  can read files. Keep scan/submission counts and failed exit statuses in logs;
  the persistent launcher attempts another round after its normal delay.

### Live progress and idempotency

Open the authenticated Alist management page at
`http://<nas-host>:5244/@manage/tasks/copy` to view individual episode tasks and
live progress bars. The same task data is available through the authenticated
`/api/admin/task/copy/undone` endpoint. Scheduler enqueue counts are **not**
download-progress counters.

For acceptance, sample a running task's ID, progress and byte count at two
recorded times; require actual growth, not just queue presence or preallocated
file size. Rerun `sync_to_library.sh` after a successful scan: unchanged content
must report `enqueued=0`, with completed files and undone tasks skipped and no
duplicate source/destination task keys. Initial full-library completion is a
separate milestone from scheduler deployment.

### Safe operations and acceptance

1. Check authenticated NAS SSH first. Stop if unreachable; do not bypass SSH
   through another service or an unauthenticated HTTP endpoint.
2. Run `/volume1/docker/_backups/backup.sh`, verify the new archive, and take a
   separate timestamped copy of the compose file and any replaced sync script or
   helper. Preserve `ssh-keepalive.timer` and `ssh-keepalive.service` in their
   removed/inactive state; do not recreate SSH keepalive units.
3. Reuse the already-installed Home Assistant image as the launcher; the
   approved deployment uses host networking/PID access and privileged
   `nsenter` to run the host script. This grants host-root capability: keep it
   restricted to this trusted deployment, with no new exposed ports or tokens.
4. When retiring an older `rclone copy` run, stop only the old copy process and
   delete only confirmed incomplete `*.partial` regular files under `library/tv`.
   Validate Python and shell syntax plus compose configuration, then restart only
   `quark-library-sync` without restarting `alist` or `quark-mount` or pulling a
   new image. Preserve existing container IDs, start times, and completed media.
5. Verify startup execution, the lock-collision skip, exclusions, and unchanged
   size/mtime/inode of a pre-existing same-size media file. Confirm that a second
   pass reports `enqueued=0` and creates no duplicate undone tasks.
6. Confirm live progress using two timestamped samples from `/api/admin/task/copy/undone`
   (matching the `:5244/@manage/tasks/copy` UI), showing actual progress/byte
   growth for a running task. A new directory, queue entry, or preallocated file
   size alone does **not** prove a live download.
7. For rollback, stop only `quark-library-sync` and restore the timestamped
   configuration/script backups. Keep downloaded media and unrelated services.

## Backlog boundaries

- No Home Assistant, Node-RED, HomeKit, lighting, or cloud credential changes.
- No automatic deletion of local media, library rebuild, or image upgrade.
- Periodically check disk capacity, failed rounds, and completed-download logs.
  Do not assume an `Up` container means that its transfers succeeded.
