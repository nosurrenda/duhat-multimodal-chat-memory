# Phase 2.5 K6 Go Re-run Matrix

This list is the Phase 2.5 Go evidence. Checks stay owned by their original
phase; this file records whether the Go runtime can exercise the same contract.

| Source check | Go evidence | Status |
| --- | --- | --- |
| V3 config hash determinism | `go test ./internal/config -run TestLoadHashesExactBytes`; K2a/K2e independent parity controls | Re-run |
| V6 manifest completeness | `go test ./internal/manifest`; K2d cross-language parser/writer control | Re-run |
| W1 schema version | `go test ./internal/trace -run TestReadRejectsUnknownAction` | Re-run |
| W2 closed action enum | `go test ./internal/trace -run TestReadRejectsUnknownAction` | Re-run |
| V7 Python AST storage rule | K4a/K4b Go AST equivalent | Replaced: language-specific source tree |
| V1, V2, V4–V5, V8–V14 | Python service/config/secret/rollback contracts | Excluded: no Go service or environment surface in Phase 2.5 |
| W3–W4b | Retired `jump` action semantics | Excluded: D33 replaces this action set in Phase 5 |
| W5–W19 | Python metric, capability, viewer, and network contracts | Excluded: no equivalent Go implementation is delivered in Phase 2.5 |

`make go-check` is the executable Phase 2.5 toolchain gate: it requires
`go1.27.1`, then runs vet, normal tests, and race tests.
