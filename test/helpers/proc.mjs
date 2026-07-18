// R-F2742 — shared child-server shutdown helper.
//
// R-F2739 fixed a kill-then-delete race in ONE capability test
// (admin-system-status-live-rf429): `proc.kill()` returns immediately, so a
// following `rmSync(tmpDir)` could race the child still holding handles in that
// dir → EBUSY/EPERM on Windows (`force:true` masks ENOENT, not a locked file).
// Four sibling tests still had the un-synchronized pattern; this hoists the
// proven fix into one place so every server-booting test awaits real exit
// before deleting its temp dir, and there is a single definition to maintain.
//
// Usage:  await stopServer(proc); rmSync(dir, { recursive: true, force: true });
export function stopServer(proc) {
  if (proc.exitCode !== null) return Promise.resolve();
  return new Promise(resolveP => {
    proc.once('exit', resolveP);
    proc.kill();
  });
}
