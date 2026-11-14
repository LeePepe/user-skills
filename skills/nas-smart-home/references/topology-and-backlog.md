# NAS media-sync operational reference

This is a sanitized deployment reference, not an installable public skill package.
The `nas-smart-home` skill and household inventory remain in the private skill
repository; SSH identities, credentials, device entities, and network topology
are intentionally not reproduced here.

## Quark → local media library

| Component | Responsibility |
|---|---|
| `alist` | Existing authenticated Quark WebDAV gateway |
| `quark-mount` | Existing read-only browsing/mount entry; not the download scheduler |
| `quark-library-sync` | Persistent incremental downloads to the local TV library |

The download service is declared alongside the existing services in
`/volume1/docker/openlist/docker-compose.yaml`. Its executable is
`/volume1/docker/openlist/rclone/sync_to_library.sh`, using the existing private
`rclone.conf` and `alist_quark` remote. Downloads land in
`/volume1/迅雷下载/Media/library/tv`; logs are written to
`/volume1/docker/openlist/data/log/quark-library-sync.log`.

### Schedule and copy rules

- Run one round immediately on container start, then wait **3600 seconds after
  that round finishes** before starting the next. Long initial downloads delay
  the next start; this is not a fixed wall-clock cron schedule.
- Use `restart: unless-stopped` and a host-visible, non-blocking `flock` to avoid
  overlapping rounds. A concurrent manual invocation must log a skip and exit.
- Incrementally copy `来自：分享` into `library/tv`, retaining its child directory
  structure; then copy eligible user content from the Quark root into the same
  destination. Exclude the shared prefix during the root pass.
- Exclude official wallpaper/sample/test/guide content and recruitment folders
  **before** media inclusion rules; rclone filters use first-match ordering.
- Include only videos (`mp4`, `mkv`, `avi`, `mov`, `ts`, `flv`, `webm`, `m4v`,
  `iso`, `rmvb`), subtitles (`ass`, `srt`, `ssa`, `sub`, `sup`), and `nfo` metadata.
- Use `rclone copy --size-only`, not destructive `sync`. Same-path, same-size
  existing files are skipped; differing-size files may be replaced. Content
  changes that preserve size are deliberately not detected.
- Limit downloads to two concurrent transfers and four multi-thread streams;
  use four checkers, 30-second connection and 300-second I/O timeouts, three
  retries, and five low-level retries.
- Create files with readable permissions and run `chmod -R a+rX` on the TV
  library after the round so the local media manager can read completed files.
- Log transfer counters periodically and preserve failed exit statuses in logs;
  the scheduler still attempts the next round after its normal delay.

### Safe operations and acceptance

1. Check authenticated NAS SSH first. Stop if unreachable; do not bypass SSH
   through another service or an unauthenticated HTTP endpoint.
2. Run `/volume1/docker/_backups/backup.sh`, verify the new archive, and take a
   separate timestamped copy of the compose file and any existing sync script.
3. Reuse the already-installed Home Assistant image as the launcher; the
   approved deployment uses host networking/PID access and privileged
   `nsenter` to run the host script. This grants host-root capability: keep it
   restricted to this trusted deployment, with no new exposed ports or tokens.
4. Validate shell syntax and compose configuration. Start only
   `quark-library-sync`, without restarting `alist` or `quark-mount` or pulling
   a new image. Preserve existing container IDs and start times.
5. Verify startup execution, the lock-collision skip, exclusions, and unchanged
   size/mtime/inode of a pre-existing same-size media file.
6. Confirm actual progress using two timestamped transfer-byte counter samples
   and allocated blocks or process I/O. A new directory or a preallocated file
   size alone does **not** prove a live download. Full initial-library download
   completion is separate from deployment acceptance.
7. For rollback, stop only `quark-library-sync` and restore the timestamped
   configuration/script backups. Keep downloaded media and unrelated services.

## Backlog boundaries

- No Home Assistant, Node-RED, HomeKit, lighting, or cloud credential changes.
- No automatic deletion of local media, library rebuild, or image upgrade.
- Periodically check disk capacity, failed rounds, and completed-download logs.
  Do not assume an `Up` container means that its transfers succeeded.
