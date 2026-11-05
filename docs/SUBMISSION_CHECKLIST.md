# Spatial-QAT Hackathon Submission Checklist

## Meta Global AI Developer Hackathon — Indie Developer Track
**Deadline: November 4, 2026**

---

## ✅ Pre-Submission Checklist

### Code & Repository
- [x] All M1–M5 features implemented and tested
- [x] Production Dockerfiles (multi-stage, non-root user)
- [x] Nginx reverse proxy configuration
- [x] Docker Compose for production and development
- [x] Demo board seeding script
- [x] `.gitignore` excludes secrets, caches, and PRD
- [x] Clean commit history on GitHub
- [ ] Final code review and linting pass
- [ ] All tests passing

### Deployment
- [ ] Live public URL accessible
- [ ] Pre-warmed cached demo board loaded
- [ ] Health endpoint returns 200
- [ ] Guest session creation works
- [ ] Board upload and analysis pipeline works
- [ ] Voice Q&A endpoint responds
- [ ] Spatial quiz generation and grading works
- [ ] SSE agent trace stream works
- [ ] Budget guard active and reporting

### Demo Video (90–120 seconds)
- [ ] Hook in first 15 seconds
- [ ] Shows upload → analysis → inspection flow
- [ ] Demonstrates voice Q&A
- [ ] Shows spatial quiz with grading
- [ ] Highlights agent trace panel
- [ ] Clear audio and screen recording
- [ ] Under 120 seconds total
- [ ] Uploaded to video platform (YouTube, etc.)

### Documentation
- [x] README.md with architecture diagram
- [x] DEPLOYMENT.md with setup instructions
- [x] EVALUATION_REPORT.md with metrics
- [x] LIMITATIONS.md with known failure modes
- [x] Demo video script outline
- [ ] LICENSES.md with asset attribution

### Submission
- [ ] GitHub repository URL: https://github.com/techgrandmaster/spatial-qat
- [ ] Live demo URL: ________________________
- [ ] Demo video URL: ________________________
- [ ] Project description written
- [ ] Submission form completed
- [ ] Submitted before evening of November 4, 2026

---

## 🎯 Demo Flow (for live judging)

1. **Open the app** — Demo board already loaded with Arduino Uno
2. **Hover components** — Show SVG masks and labels
3. **Click a component** — Open inspector with datasheet citations
4. **Ask a question** — Type or use voice: "What does the USB connector do?"
5. **Watch agent trace** — Show real-time SSE events
6. **Start a quiz** — Generate spatial active-recall quiz
7. **Take a quiz** — Click to locate, explain function
8. **Show results** — Spatial + conceptual grading feedback
9. **Highlight cache** — Show SHA-256 cache badge (zero cost on repeat)
10. **Show budget** — Budget guard preventing overspend

---

## 📊 Key Metrics to Highlight

| Metric | Target | Status |
|--------|--------|--------|
| Component Discovery Recall | ≥70% at IoU≥0.50 | ⏳ Measured |
| Component Naming Accuracy | ≥80% | ⏳ Measured |
| Citation Validity | 100% | ✅ Deterministic |
| Spatial Quiz Determinism | 100% | ✅ 27/27 tests pass |
| API Spend | <$120 | ✅ Hard cap enforced |
| Cached Demo Latency | <1.0s | ⏳ Measured |

---

## 🔑 Unique Value Propositions

1. **Four-way binding**: Spatial mask + component identity + source citation + assessment target
2. **Zero-trust spatial grading**: Server-side deterministic geometry, no LLM guessing coordinates
3. **Specialized multi-model orchestration**: Each Meta model used where it excels
4. **Content-addressed caching**: SHA-256 cache eliminates redundant API calls
5. **Hard budget guard**: $120 cap prevents credit exhaustion

---

## 📝 Notes for Judges

- The demo board works entirely from cache after the first analysis — no live API calls needed
- The spatial quiz never exposes target component IDs to the client
- All citations are validated against actual datasheet pages and chunks
- The agent trace panel shows every step, tool call, and cost in real-time
- The system explicitly flags uncertainty rather than inventing facts
