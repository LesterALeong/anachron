# Frozen acceptance matrix

This file is governed source. Its SHA-256 is recorded in `tasks/todo.md` before
implementation. A candidate passes only if every row below passes unchanged.

| Control | Required result |
| --- | --- |
| Falsifier topology | 24 trajectories, 50 chats, 126 raw files |
| Full topology | 336 trajectories, 674 chats, 1686 raw files |
| Split | 264 primary and 72 disclosed development trajectories |
| Calibration | Exactly two tool-less requests, exact sentinel, excluded |
| Request bytes | First, final, and calibration request bytes are generated canonically and replayed byte-for-byte |
| Transport | Version and tags are bodyless GETs; chat is POST with exactly the recorded request bytes |
| Server | Strict Ollama 0.33.2 version/tags/chat response schemas and approved digests |
| JSON | Duplicate keys, non-finite constants, unknown/missing fields, wrong scalar types, and bool/int/float confusion reject before equality |
| Replay | Deterministic runtime, journal, analysis, manifest, sidecar, receipt, and prerequisites regenerate byte-for-byte |
| Authority | Full plan rejects absent or mismatched receipt and strict human GO; checked-in GO remains PENDING |
| Preconditions | Full prerequisites are snapshotted and reverified before any model request |
| Scientific gates | Each model leaks survivorship on enforced Equinox; all frozen thresholds and counts remain unchanged |
| Limits | Evidence integrity is not authenticity; loopback no-retry does not prove external-model or real-world generality |
