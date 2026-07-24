Symptom: Embedded iPhone and iPad interaction feels slightly laggier than Simulator.app.
User impact: Pointer input in the native Simulator pane has perceptibly slower visual feedback than Apple’s reference app.
Source: User report after PR 7857 dogfood.
Target surface: macOS native Simulator pane and isolated Simulator worker.
Build/version/tag: feat-simulator-pane at ed9a9c5d5d, baseline tag `s785r`.
Device: Isolated iPad Pro 13-inch (M5), iOS 26.5, E79439C2-9B4B-4FF4-8CD8-DB71709FBBC6.
Repro workload: Warm repeated taps and drags on that same booted iPad, comparing the embedded pane with Simulator.app.
Expected bad behavior: Input-to-pixel feedback remains measurably slower in the embedded pane after the existing 16 ms interactive publication deadline.

Primary class: UI interaction latency.
Evidence plan: Measure HID dispatch, first post-input framebuffer callback, worker publication, host copy, and presentation on one warm workload; use Time Profiler only where runtime attribution is still ambiguous.

Root cause: The host polled at a fixed 60 Hz. A tick started one bounded off-main copy, but a completed copy was only cached and waited for the next tick before Core Animation received it. On the test Mac's 120 Hz display, this added up to 33.4 ms of host scheduling in the worst phase alignment: 16.7 ms to notice a frame and another 16.7 ms to present the completed copy.

Fix: Keep one copy in flight and newest-frame coalescing. Present accepted copy completions immediately on the main actor, and derive the strict polling interval from `NSScreen.maximumFramesPerSecond`, with a 60 Hz fallback and 120 Hz cap. On this Mac, host scheduling is now bounded by one 8.3 ms availability poll with no second presentation poll.

Parallelism decision: Frame publication in the worker and frame copying in the host already run off-main and overlap across processes. Starting multiple host copies would increase shared-memory and GPU pressure and allow older completions to race newer frames. The production path remains bounded and ordered.

Evidence:
- The regression `completedCopyPresentsImmediately` failed before the fix because the released frame never reached the layer without another explicit display tick.
- All 13 frame-surface lifecycle tests pass after the fix, including 60 Hz fallback, 120 Hz cadence, coalescing, replacement, and teardown coverage.
- All 458 `CmuxSimulator` tests across 74 suites pass.
- Baseline Time Profiler stacks present through `presentationTimerDidFire`. After-fix stacks present through `SimulatorFramePresentationPipeline.copyDidComplete`.
- Host and worker after traces show frame copies and Core Image readback remain off-main; no extra copy concurrency was introduced.
- Computer Use exercised pointer taps, drags, Home, App Switcher, and both rotations on the tagged pane. The tag-bound CLI ran twenty alternating eight-step swipes on `surface:2`.
- The same isolated iPad was inspected in Apple Simulator.app for the reference path.
- Tagged cloud build `s785r` passed: https://github.com/manaflow-ai/cmux/actions/runs/30054535188

Production hardening: Cross-process ownership publication now throws. Targeted camera configuration is rejected before worker delivery when the ownership file cannot be created, and location ownership failures preserve the prior lifecycle. The primitive failure and worker-delivery regressions pass.

Residual risk: Core Image framebuffer readback remains the dominant worker-side cost. This change removes host scheduling latency without changing private Simulator framework behavior or readback throughput.
