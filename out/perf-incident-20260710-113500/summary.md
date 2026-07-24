Incident: Local tagged Simulator-pane preflight
Symptom: The tagged cmux host remains alive but its UI and control socket stall for tens of seconds.
User impact: A stalled or failed Simulator framebuffer can make the rest of cmux unresponsive.
Source: Reproduction through the simux tagged debug socket.
Target surface: macOS Simulator pane.
Build/version/tag: cmux 0.64.17, simux, commit 300198e54e.
Repro workload: Create a native Simulator surface, attach 136ADF66-395A-409B-99AC-376196013B60, then issue Simulator input or capture a window screenshot.
Expected bad behavior: Main-thread socket waits exceed 50 seconds and the UI stops updating.

Evidence:
- /tmp/cmux-hang-20260710-113747/
- /tmp/simux-live-stall-2.sample.txt
- /tmp/simux-live-stall-3.sample.txt
- /var/folders/rr/vmfx6xh12dz2tlvgtmyvjmf80000gn/T/cmux-screenshots/simux-preflight_2026-07-10T18-39-04Z_0D3028F6.png

Owner: Simulator frame presentation.
Invariant: The main process must never give Core Animation pixel storage whose producer can stall or die in another process.
Why the old path failed: The layer used a worker-global IOSurface directly. Core Animation synchronously waited in CABackingStoreGetFrontTexture while preparing that content.
Fix shape: Copy completed shared frames off the main actor into bounded host-private surfaces, then present only the completed host copies.
Proof required: The exact pane path renders, two worker kills leave the same host PID and responsive socket, explicit recovery streams again, and a follow-up sample shows no sustained shared-surface backing-store wait.

Post-fix proof:
- Host PID 53083 remained alive and the control socket answered in 0.03 seconds after each worker SIGKILL.
- The first crash automatically started replacement worker 63225. The second crash tripped the restart fuse and returned `simulator_unavailable` without blocking the host.
- `simulator.recover` completed in 1.15 seconds, started worker 67504, and restored SpringBoard streaming on the same surface and host PID.
- `/tmp/simux-after-fix.sample.txt` captured five seconds after recovery. The main thread was idle for 3583 of 3608 samples. Core Animation's ordinary host-image preparation contained five one-millisecond queue waits, versus all 2271 samples blocked on the worker-global IOSurface before the fix.
- `/var/folders/rr/vmfx6xh12dz2tlvgtmyvjmf80000gn/T/cmux-screenshots/simux-recovered_2026-07-10T20-27-15Z_26D22554.png` shows the recovered native pane rendering an upright, correctly colored frame.
