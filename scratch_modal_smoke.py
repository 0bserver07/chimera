"""Live Modal smoke: confirm sandbox create-from-registry + exec + snapshot work
on the active (0bserver07) profile, natively amd64. Delete after use."""
import modal

app = modal.App.lookup("chimera-pb-smoke", create_if_missing=True)
img = modal.Image.from_registry("python:3.11-slim")

print("creating sandbox...", flush=True)
sb = modal.Sandbox.create("sleep", "120", image=img, app=app)
try:
    p = sb.exec("bash", "-lc", "echo hello-from-modal && uname -m && nproc")
    p.wait()
    print("EXEC stdout:", repr(p.stdout.read()), flush=True)
    print("EXEC returncode:", p.returncode, flush=True)

    # write a file in, read it back (copy_in_tar analogue)
    with sb.open("/tmp/probe.txt", "w") as f:
        f.write("submission-bytes")
    r = sb.exec("bash", "-lc", "cat /tmp/probe.txt")
    r.wait()
    print("FILE roundtrip:", repr(r.stdout.read()), flush=True)

    print("snapshotting filesystem...", flush=True)
    snap = sb.snapshot_filesystem()
    print("SNAPSHOT type:", type(snap).__name__, "| is Image:", isinstance(snap, modal.Image), flush=True)
finally:
    sb.terminate()
    print("terminated.", flush=True)
