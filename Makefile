.PHONY: build test lint format-check check run benchmark benchmark-unit benchmark-smoke test-proxy
build:
	cargo build --workspace
test:
	cargo test --workspace
lint:
	cargo clippy --workspace --all-targets -- -D warnings
format-check:
	cargo fmt --all --check
check: format-check lint build test benchmark-smoke test-proxy
run:
	cargo run -p structure-server
benchmark: benchmark-smoke
benchmark-unit:
	cargo test -p structure-short-memory-benchmark
benchmark-smoke:
	cargo run -p structure-short-memory-benchmark -- --baseline b0 --turns 4 --fail-on-gate
	cargo run -p structure-short-memory-benchmark --bin tier_b -- --fixture --repetitions 3 --fail-on-task
test-proxy:
	node --test benchmarks/cli-comparison/responses-chat-proxy.test.mjs
