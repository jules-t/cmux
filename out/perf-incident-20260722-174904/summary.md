# Simulator input-to-pixel responsiveness

- Started: 2026-07-22 17:49:04 America/Los_Angeles
- PR: https://github.com/manaflow-ai/cmux/pull/7857
- Tag: `sim785`
- Symptom: interactive Simulator input feels laggier in the embedded iPad surface than in Simulator.app.
- Current evidence: warm debug-CLI swipes complete in about 100 ms, while framebuffer publication is capped at one frame every 30 ms and the host display tick is 60 Hz.
- Working hypothesis: input and framebuffer pacing are independent, so the first frame caused by input can wait for the idle 30 ms publication deadline before the host can present it.
- Safety constraint: retain newest-frame coalescing and the downscaled frame transport so interactive prioritization cannot restore unbounded full-resolution GPU readback.

## Resolution

- Owner: `SimulatorFramebufferFramePublisher` remains the sole frame-cadence owner.
- Invariant: idle callbacks stay coalesced at 30 ms; the first real framebuffer callback after pointer input uses a 16 ms deadline; geometry changes remain immediate.
- Wiring: ordered pointer events arm the publisher before HID injection, including every worker-generated scroll touch event.
- Regression proof: the pacing test uses a 10-second idle deadline and verifies an input-prioritized frame publishes within 250 ms. A second red-then-green regression proves exact down/up tap pairs use a 50 ms iPadOS-compatible hold while drags retain 4 ms pacing. The complete suite passes all 454 package tests.
- Live proof: final tagged build `sim785` at commit `d943b9d98c`, iPad `AC2C01F6-E504-476C-82CE-FE759D96F403`, 20 alternating 8-step swipes completed in 0.08-0.17 seconds each. The final 10 settled at 0.08-0.11 seconds. The ordinary two-event tap now opens Settings in 0.10 seconds. Hardware Home, four-orientation round trip, pane resize, renderer recovery, device-selection rollback, tools show/hide, and invalid-coordinate rejection all completed without a worker or host crash.
- GUI limitation: Computer Use initialized but its native Mac pipe failed on three app-list attempts. Physical mouse drag remains unverified; no alternate GUI injector was used.
