Symptom: Embedded iPad Simulator interaction feels laggier than Apple Simulator.
User impact: Pointer and control feedback in the native Simulator pane is slower than the reference app.
Source: User report during tagged PR dogfood.
Target surface: macOS native Simulator pane and isolated Simulator worker.
Build/version/tag: feat-simulator-pane, sim785.
Repro workload: Repeated taps and drags on the booted cmux-simfg-ipad home screen and Settings app.
Expected bad behavior: Input-to-frame feedback is visibly delayed relative to Apple Simulator on the same device.

Owner: The Simulator worker's framebuffer publication path and the host view's DeviceKit artwork cache.
Invariant: Interactive input must reach the Simulator worker without per-event serialization behind frame or status work, and the newest frame must publish without avoidable buffering.
Why the old path failed: The worker discarded attach and resize geometry, rendered every native 2064x2752 iPad frame into a 22.7 MB shared-memory slot, and the host decoded DeviceKit PDF artwork during repeated draws.
Fix shape: Bound worker publication to the pane's backing-pixel geometry without upscaling, keep newest-frame coalescing, cache each DeviceKit image once per chrome profile, and remove the overlapping floating control capsule.
Deterministic proof: A 400x530 point pane at 2x now publishes a 795x1060 frame, reducing bytes per frame from 22,720,512 to 3,370,800, while aspect ratio and full native resolution for larger panes remain covered by tests.
Runtime proof: The exact sim785 build sustained 32 Computer Use home, app, and rotation actions while streaming. The after-fix host trace contained no `NSImage(contentsOf:)` sample and 2 `drawPortraitChrome` samples, versus 1 and 6 respectively in the shorter before-fix trace. Computer Use confirmed the native M5 iPad artwork renders without the old overlapping control capsule.
Artifact hygiene: Raw Instruments traces and full TOC exports were removed because Xcode embeds process environment metadata. This directory retains the redacted summary and target-only time-profile tables.
