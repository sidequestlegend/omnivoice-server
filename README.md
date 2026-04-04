# omnivoice-server — Documentation Index

Documentation cho dự án `omnivoice-server`. Đọc theo thứ tự này nếu bạn mới onboard.

## Cấu trúc folder

```
docs/
├── system/          # System docs, ecosystem, specifications
├── architecture/    # Architecture diagrams, component maps
├── design/          # Data flow, API design, implementation details
├── plan/            # Sprint plans, execution tracking
└── roadmap/         # Long-term vision, milestones
```

---

## Đọc theo thứ tự

| #   | File                                                        | Nội dung                                                                      | Khi nào cần           |
| --- | ----------------------------------------------------------- | ----------------------------------------------------------------------------- | --------------------- |
| 1   | [system/ecosystem.md](./docs/system/ecosystem.md)           | Vị trí repo trong TTS landscape, hardware requirements, deployment topologies | Trước khi bắt đầu     |
| 2   | [system/specification.md](./docs/system/specification.md)   | System specification chi tiết — đọc để hiểu toàn bộ yêu cầu                   | Trước khi implement   |
| 3   | [architecture/overview.md](./docs/architecture/overview.md) | Layer diagram, concurrency model, component map, startup sequence             | Trước khi implement   |
| 4   | [design/dataflow.md](./docs/design/dataflow.md)             | Data transformation per-endpoint, audio format reference, client examples     | Khi implement routers |

---

## Diagrams có trong bộ tài liệu này

| Diagram                           | File                         | Loại        |
| --------------------------------- | ---------------------------- | ----------- |
| Ecosystem map (L0→L4)             | architecture/overview.md §1  | Context     |
| Layer architecture                | architecture/overview.md §2  | Structural  |
| Internal component map            | architecture/overview.md §3  | Structural  |
| Concurrency model                 | architecture/overview.md §4  | Sequence    |
| Request lifecycle — non-streaming | architecture/overview.md §5  | Flowchart   |
| Request lifecycle — streaming     | architecture/overview.md §6  | Flowchart   |
| Voice mode decision tree          | architecture/overview.md §7  | Flowchart   |
| Startup & shutdown sequence       | architecture/overview.md §8  | Sequence    |
| Profile storage schema            | architecture/overview.md §9  | ER diagram  |
| Service dependency graph          | architecture/overview.md §10 | Graph       |
| Error taxonomy                    | architecture/overview.md §11 | Flowchart   |
| TTS landscape                     | system/ecosystem.md          | Graph       |
| Dependency risk register          | system/ecosystem.md          | Graph       |
| Deployment topologies (3x)        | system/ecosystem.md          | Text + code |
| Non-streaming data flow           | design/dataflow.md           | ASCII       |
| Streaming data flow               | design/dataflow.md           | ASCII       |
| Per-endpoint flows (all)          | design/dataflow.md           | ASCII       |

---

## Tạo tài liệu mới

Khi cần thêm tài liệu:
1. Chọn folder phù hợp (system/, architecture/, design/, plan/, roadmap/)
2. Đặt tên file theo **kebab-case**: `feature-name.md`
3. Update README của folder đó
4. Update [docs/README.md](./docs/README.md) nếu cần

*(Reserved for future documentation)*
