# Consolidated Review: Multi-Speaker Script API Specification

**Document reviewed**: `multi-speaker-script-api.md` v1.0  
**Review date**: 2026-04-17  
**Sources**: Technical Review A + Technical Review B (merged & deduplicated)

---

## Verdict

> **❌ Not ready for implementation sign-off.**  
> Resolve all Critical gaps and Major gaps M1–M7 before proceeding.

Tài liệu được chuẩn bị tốt — background research solid, design decisions có rationale, API contract rõ ràng. Tuy nhiên tồn tại **4 Critical gaps** có thể gây production incident hoặc breaking change sau launch, cùng **7 Major gaps** cần resolve trước release đầu tiên.

---

## Strengths

- OmniVoice model limitations được document với evidence cụ thể (file path + line numbers).
- Section 7 (Design Decisions) tuân theo cấu trúc "decision → rationale → alternative considered" — tốt.
- Voice inheritance logic giảm boilerplate hiệu quả, khớp mental model của PlayHT.
- Validation limits được định nghĩa là constants, nhất quán với codebase hiện tại.
- Implementation plan phân phase hợp lý, scope estimate 6–9 ngày thực tế.

---

## Critical Gaps 🔴

*Phải giải quyết trước khi approve implementation.*

---

### C1 — Script Endpoint Có Thể Monopolize Toàn Bộ Server

**Phần liên quan**: Section 6.3, Section 8.1.2

Server hiện tại dùng semaphore `MAX_CONCURRENT=2`. Một script request 100 segments gọi `inference_svc.synthesize()` 100 lần tuần tự — mỗi lần acquire/release semaphore, nhưng tổng wall-clock time có thể lên đến **~8 phút** (100 segments × ~5s). Trong thời gian đó, toàn bộ client khác gọi `/v1/audio/speech` phải queue chờ.

Tài liệu không đề cập bất kỳ cơ chế nào để ngăn điều này: không có dedicated semaphore slot, không có priority queue, không có per-endpoint concurrency budget.

**Yêu cầu**: Định nghĩa rõ concurrency policy — ví dụ: script endpoint giữ **1 dedicated slot** cho toàn bộ duration của request (không per-segment), tách biệt với pool của `/v1/audio/speech`.

---

### C2 — Multi-Track Output Mất Temporal Synchronization

**Phần liên quan**: Section 5.3

Multi-track mode trả một blob audio riêng cho từng speaker, trong đó tất cả segments của speaker đó được concatenate lại. Ví dụ:

```
alice: [seg1: 3s][seg3: 2s][seg5: 4s]  → 1 blob (9s)
bob:   [seg2: 2.5s][seg4: 3.5s]        → 1 blob (6s)
```

Client nhận hai blob này **không có cách nào tái tạo lại timeline**. Không có per-segment timestamps, không có segment offsets trong metadata. `speaker_order` array không đủ để đồng bộ hóa playback.

**Yêu cầu**: Một trong hai lựa chọn:
- **(a)** Bổ sung per-segment timestamps vào metadata response để client có thể reconstruct timeline.
- **(b)** Document rõ ràng rằng multi-track **không dùng được cho synchronized playback** — chỉ dùng cho post-processing workflows — và thêm caveat này trực tiếp vào API reference.

---

### C3 — Không Có Progress Feedback Cho Long Scripts

**Phần liên quan**: Section 4, Section 10 (Future Considerations)

Endpoint hiện tại `/v1/audio/speech` hỗ trợ sentence-level streaming. Endpoint mới yêu cầu client gửi request rồi **chờ im lặng** — không có progress, không có interim feedback. Với script 50+ segments, thời gian chờ dễ vượt 60–120 giây, tức là **regression về UX so với endpoint hiện tại**.

Spec liệt kê streaming là "Future Consideration" (Phase 3 optional) nhưng không define timeout contract rõ ràng. Con số `120s` trong Section 5.4 (error schema) không rõ là per-segment hay total-request.

**Yêu cầu**:
1. Làm rõ timeout `120s` áp dụng cho cái gì (per-segment / total-request).
2. Nâng streaming hoặc job-based output lên **Phase 2** thay vì optional Phase 3. Nếu không làm được, ít nhất định nghĩa một cơ chế tối thiểu — ví dụ: polling endpoint trả về trạng thái (`{"status": "processing", "completed_segments": 3, "total_segments": 50}`), hoặc SSE stream trả event per segment.

> ⚠️ Lưu ý: `X-Progress` header trên chunked response **không phải chuẩn HTTP** — response header chỉ được gửi một lần ở đầu, không thể cập nhật mid-stream. Trailing headers là một alternative nhưng không được support rộng rãi. Polling endpoint hoặc SSE là lựa chọn thực tế hơn.

---

### C4 — Không Có Authentication & Rate-Limiting Model

**Phần liên quan**: Toàn bộ tài liệu

Spec không đề cập bất kỳ cơ chế auth nào cho endpoint mới, trong khi đây là endpoint:
- **Tốn kém nhất** trên server (100 synthesis calls / request).
- **Có thể access voice profiles** của caller khác qua `clone:profile_id` nếu không có access control.
- **Không có rate-limiting** được định nghĩa.

Nếu server hiện tại có auth (API key, Bearer token), spec cần xác nhận endpoint mới kế thừa cùng cơ chế. Nếu không, đây là security gap nghiêm trọng.

**Yêu cầu**: Bổ sung section về auth model (dù chỉ là "kế thừa từ server-wide auth"), per-profile access control, và rate-limiting strategy cho requests đắt tiền.

---

## Major Gaps 🟠

*Nên giải quyết trước release đầu tiên.*

---

### M1 — Tương Tác Giữa Global `speed` và Per-Segment `speed` Không Được Định Nghĩa

**Phần liên quan**: Section 5.1

`ScriptRequest` có field `speed` (global) và `ScriptSegment` cũng có field `speed`. Spec không nêu:
- Hai field này **multiply** (`global × segment`) hay segment **override** hoàn toàn global?
- Nếu segment bỏ qua `speed`, nó dùng global hay model default?

**Yêu cầu**: Một câu hoặc một bảng nhỏ định nghĩa composition rule là đủ.

---

### M2 — Format `pcm` Bị Bỏ Mà Không Có Giải Thích

**Phần liên quan**: Section 5.1

Section 1.2 liệt kê `pcm` là output format được hỗ trợ của server hiện tại. Endpoint mới chỉ liệt kê `wav | mp3 | opus | flac | aac` — bỏ `pcm` mà không có giải thích. `pcm` thường được dùng trong streaming/low-latency pipelines, nên việc bỏ nó có thể gây incompatibility cho clients hiện tại.

**Yêu cầu**: Hoặc thêm `pcm` vào danh sách, hoặc document lý do exclusion có chủ ý.

---

### M3 — Validation `design:` Voice Xảy Ra Lazy, Không Phải Upfront

**Phần liên quan**: Section 5.2, Section 6.3 Step 2

Step 2 (Resolve Voices) đề cập "validate all voices exist", nhưng `design:` voices là free-text attributes — không có gì để validate trước khi synthesis. Attribute không hợp lệ chỉ fail tại Step 3 (synthesis time).

Hệ quả: script nhiều segments có `design:` voice sai ở một segment gần cuối sẽ **có thể lãng phí nhiều synthesis calls** trước khi báo lỗi.

**Yêu cầu**: Document rõ loại validation nào xảy ra upfront vs. lazy. Cân nhắc trả về segment index trong error response để caller biết đúng chỗ bị lỗi.

---

### M4 — Edge Cases Của `on_error: "skip"` Không Được Xử Lý

**Phần liên quan**: Section 5.4, Section 7.2

Các tình huống chưa được định nghĩa:

| Tình huống | Behavior hiện tại |
|---|---|
| Tất cả segments đều fail | ? (empty WAV? 0-byte? error?) |
| Script chỉ có 1 segment, nó fail | ? (skip → 200 với empty audio?) |
| Segment đầu hoặc cuối bị skip | Có pause ở đầu/cuối audio không? |
| Segment bị skip: pause có bị insert không? | ? |

**Yêu cầu**: Thêm một bảng hoặc danh sách định nghĩa behavior cho từng edge case trên.

---

### M5 — Memory Budget Bị Vượt Ngay Cả Trong Happy Path

**Phần liên quan**: Section 9.1

Spec đặt target "memory usage < 100MB" cho 100-segment script. Tuy nhiên phép tính đơn giản cho thấy target này không thực tế:

```
100 segments × 15s avg × 24,000 samples/s × 4 bytes (float32) = 144 MB
```

Đây là **trước** format conversion overhead, và toàn bộ tensors đang được giữ trong memory đồng thời trước khi mix.

**Yêu cầu**: Bổ sung memory budget analysis thực tế. Xem xét streaming tensor processing (process-and-discard từng segment sau khi mix) hoặc đặt limit trên tổng estimated audio duration per request.

---

### M6 — Không Có Observability Strategy Cho Endpoint Mới

**Phần liên quan**: Section 6.1, Section 9

`MetricsService` hiện tại track latency, timeouts, errors cho `/v1/audio/speech`. Spec không định nghĩa metrics mới nào sẽ được emit cho endpoint phức tạp hơn nhiều này.

**Yêu cầu**: Định nghĩa tối thiểu các metrics: `script_request_latency_s`, `script_segments_synthesized`, `script_segments_skipped`, `script_voice_resolution_failures`.

---

### M7 — Behavior Pause Giữa Các Segment Cùng Speaker Không Được Nêu

**Phần liên quan**: Section 6.3 Step 4

Spec nói pause được insert khi "speaker changes." Nhưng:
- Nếu Alice có 3 segments liên tiếp → có pause giữa chúng không?
- `pause_between_speakers=0.0` có hợp lệ không? Nghĩa là gì (hard cut)?
- Có cần tách `pause_between_speakers` (khác speaker) và `pause_between_segments` (cùng speaker) không?

**Yêu cầu**: Document rõ trigger condition của pause insertion: ví dụ `"pause chỉ được insert khi segment[i].speaker != segment[i-1].speaker"` — hoặc quyết định khác, nhưng phải explicit.

---

## Minor Gaps 🟡

*Có thể address trong follow-up releases.*

| ID | Mô tả | Đề nghị |
|----|--------|---------|
| **m1** | Default fallback voice hardcoded (`"male, middle-aged, british accent"`) không qua config | Expose qua `Settings` / `config.py` để deployer tùy chỉnh cho non-English use cases |
| **m2** | OpenAPI/Swagger update không có trong task list (Section 8.2.3) | Thêm vào Documentation tasks |
| **m3** | `X-Speakers: alice,bob,alice,...` header có thể vượt HTTP header size limit (~8KB) với script 100 segments | Cap header hoặc chỉ giữ `X-Speakers-Unique` |
| **m4** | Không có idempotency key — script 100 segments fail ở network layer sau khi synthesis xong phải chạy lại từ đầu | Định nghĩa `Idempotency-Key` header hoặc job-based output |
| **m5** | Phase 3 parallel synthesis ("max 4 parallel") không có deadlock analysis với `MAX_CONCURRENT=2` semaphore | Thêm analysis trước khi implement Phase 3 |
| **m6** | Không có per-segment language/locale — OmniVoice hỗ trợ 600+ ngôn ngữ nhưng multilingual scripts không được support | Thêm `language` field vào `ScriptSegment`, hoặc đưa vào Future Considerations |

---

## Bảng Tổng Hợp

| ID | Severity | Category | Mô tả ngắn | Block impl? |
|----|----------|----------|------------|-------------|
| C1 | 🔴 Critical | Concurrency | Script monopolizes server semaphore | ✅ |
| C2 | 🔴 Critical | API Design | Multi-track mất temporal sync | ✅ |
| C3 | 🔴 Critical | UX | Không có progress/streaming cho long scripts | ✅ |
| C4 | 🔴 Critical | Security | Không có auth/rate-limit model | ✅ |
| M1 | 🟠 Major | API Design | `speed` global vs per-segment undefined | — |
| M2 | 🟠 Major | API Design | `pcm` format bị drop không giải thích | — |
| M3 | 🟠 Major | Implementation | `design:` voice validation lazy, không upfront | — |
| M4 | 🟠 Major | API Design | `on_error: "skip"` edge cases unspecified | — |
| M5 | 🟠 Major | Performance | Memory math vượt target ngay trong happy path | — |
| M6 | 🟠 Major | Observability | Không có metrics strategy cho endpoint mới | — |
| M7 | 🟠 Major | API Design | Same-speaker consecutive pause undefined | — |
| m1 | 🟡 Minor | Config | Default voice hardcoded | — |
| m2 | 🟡 Minor | Docs | OpenAPI update không trong task list | — |
| m3 | 🟡 Minor | API Design | `X-Speakers` header có thể quá dài | — |
| m4 | 🟡 Minor | Resilience | Không có idempotency key | — |
| m5 | 🟡 Minor | Concurrency | Phase 3 parallel synthesis thiếu deadlock analysis | — |
| m6 | 🟡 Minor | Feature | Không có per-segment language support | — |

---

## Action Items

**Block implementation sign-off (phải resolve trước):**
1. **C1** — Định nghĩa concurrency policy cho script endpoint vs shared semaphore.
2. **C2** — Thêm per-segment timestamps vào multi-track metadata, hoặc document rõ limitation.
3. **C3** — Làm rõ timeout contract; nâng streaming/job output lên Phase 2; dùng polling endpoint hoặc SSE thay vì header-based progress.
4. **C4** — Thêm section auth model và rate-limiting, dù chỉ là delegate sang server-wide auth.

**Phải resolve trước release đầu tiên:**
5. **M1** — Thêm composition rule cho `speed` (một câu là đủ).
6. **M2** — Giải thích `pcm` exclusion hoặc thêm lại vào supported formats.
7. **M3** — Document rõ validation nào upfront vs lazy; thêm segment index vào error response.
8. **M4** — Document behavior của tất cả `on_error: "skip"` edge cases.
9. **M5** — Thêm memory budget analysis và enforcement mechanism.
10. **M6** — Định nghĩa metrics tối thiểu cho endpoint mới.
11. **M7** — Document explicitly trigger condition của pause insertion.

**Có thể address trong follow-up:**
12–17. m1 → m6 theo thứ tự ưu tiên của team.

---

*End of Consolidated Review*
