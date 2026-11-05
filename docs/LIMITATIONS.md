# Limitations & Known Failure Modes

Honest engineering disclosure of Spatial-QAT v1 limitations.

## Spatial Perception

- **Tiny SMD components:** sam-3.1 struggles with components smaller than ~2mm on the board, especially when densely packed.
- **Dense pin headers:** Individual pins in multi-pin headers are often segmented as a single region rather than per-pin.
- **Silk-screen occlusion:** Text and markings on the board surface can interfere with segmentation boundaries.
- **Reflections & shadows:** Board photos with strong reflections or uneven lighting produce lower-quality masks.

## Component Identity

- **Perception ≠ Identity:** sam-3.1 proposes candidate spatial regions, not guaranteed component identities. Verification status tracks confidence.
- **Synonym ambiguity:** A component labeled "voltage regulator" may also be called "LDO" or "3.3V regulator" depending on context.

## Q&A & Citations

- **Scanned PDFs:** PDFs without embedded text layers are flagged as `needs_ocr` and cannot be searched until OCR is applied.
- **Ungrounded claims:** Where evidence is absent, the system flags uncertainty rather than inventing pin numbers or voltage specs.

## Voice

- **Accent & noise:** muse-voice-transcribe-1.0 accuracy degrades with heavy accents or background noise at a hardware bench.
- **No server-side TTS:** Text-to-speech uses browser `window.speechSynthesis`, which varies by platform.

## Budget

- **Hard cap at $120:** Once the budget is exhausted, only cached demo paths continue working. New analyses require manual reset.

## Non-Goals (v1)

- Non-electronics verticals (anatomy, mechanical assemblies)
- Live webcam video tracking or AR overlays
- Autonomous electrical fault diagnosis on energized mains hardware
- Multi-tenant enterprise RBAC or billing
