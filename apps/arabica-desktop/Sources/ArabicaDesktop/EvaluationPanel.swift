import SwiftUI

struct EvaluationPanel: View {
    @ObservedObject var model: EvaluationModel
    var isVisible: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Background evaluation").font(.headline)
                    Text("Reports run separately from your conversation.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if !model.supported {
                    notice("Evaluation unavailable", "Reconnect with an Arabica agent that supports background evaluation.")
                } else if model.sessionID == nil {
                    notice("Choose a conversation", "Open a conversation to inspect its evaluation report.")
                } else {
                    HStack {
                        Button { Task { await model.load(refresh: true) } } label: {
                            Label("Request refresh", systemImage: "arrow.clockwise")
                        }
                        .disabled(model.isRequesting)
                        .help("Queue background evaluation; your conversation keeps running.")
                        Spacer()
                        if model.isRequesting { ProgressView().controlSize(.small).accessibilityLabel("Reading evaluation status") }
                    }
                    if let message = model.message {
                        Text(message).font(.caption).foregroundStyle(.secondary)
                    }
                    if let error = model.error {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(error).font(.caption).foregroundStyle(.orange)
                            Button("Retry query") { Task { await model.load() } }.disabled(model.isRequesting)
                        }
                    }
                    if let report = model.snapshot?.report {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Latest available report").font(.subheadline.weight(.semibold))
                            LabeledContent("Through event", value: "\(report.checkpoint.sequence)")
                            Text("Cached results may be from an earlier run. Refresh acceptance does not mean evaluation is complete.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if let runs = report.runs, !runs.isEmpty {
                            Divider()
                            Text("Run outcomes").font(.subheadline.weight(.semibold))
                            ForEach(runs.reversed()) { run in
                                DisclosureGroup {
                                    LabeledContent("Execution", value: run.snapshot.terminal_status)
                                    LabeledContent("Acceptance", value: run.acceptance?.verified_success.map { $0 ? "Passed" : "Failed" } ?? "Not verified")
                                    LabeledContent("Completion of evaluated criteria", value: percent(run.acceptance?.completion_bps))
                                    LabeledContent("Evaluation coverage", value: percent(run.acceptance?.coverage_bps))
                                    LabeledContent("Total time", value: elapsed(run.snapshot.timing.wall_ms))
                                    LabeledContent("Queue", value: elapsed(run.snapshot.timing.queue_ms))
                                    LabeledContent("Approval wait", value: elapsed(run.snapshot.timing.approval_wait_ms))
                                    LabeledContent("Active time", value: elapsed(run.snapshot.timing.active_ms))
                                    LabeledContent("Model call total", value: elapsed(run.snapshot.timing.model_call_ms_total))
                                    LabeledContent("Runner call total", value: elapsed(run.snapshot.timing.runner_ms_total))
                                    LabeledContent("Calls with token usage", value: "\(run.snapshot.timing.usage_reported_calls) / \(run.snapshot.timing.model_calls)")
                                    ForEach(run.acceptance?.results ?? []) { criterion in
                                        LabeledContent(criterion.criterion_id, value: criterion.status)
                                    }
                                } label: {
                                    Text("Run \(run.id.prefix(8)) · \(run.snapshot.terminal_status)").font(.caption)
                                }
                            }
                            Text("Acceptance requires explicit criteria. An execution marked completed can still be unverified.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Divider()
                        result(report.context, context: true)
                        Divider()
                        result(report.model, context: false)
                        Text("Model and context statistics describe associations. Task acceptance is evaluated separately.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        notice("No report yet", "Finish a run, then request a refresh. Your Agent can continue working while evaluation is pending.")
                    }
                    if let worker = model.snapshot?.worker_status {
                        Divider()
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Worker totals").font(.subheadline.weight(.semibold))
                            Text("Across all conversations in this agent process.").font(.caption).foregroundStyle(.secondary)
                            LabeledContent("Completed", value: "\(worker.completed)")
                            LabeledContent("Failed", value: "\(worker.failed)")
                            LabeledContent("Notifications dropped", value: "\(worker.dropped)")
                        }
                    }
                    if let policies = model.snapshot?.policies, !policies.isEmpty {
                        Divider()
                        VStack(alignment: .leading, spacing: 8) {
                            Text("All policy versions").font(.subheadline.weight(.semibold))
                            Text("Configured and background-evolved policies.").font(.caption).foregroundStyle(.secondary)
                            ForEach(policies) { p in
                                DisclosureGroup {
                                    VStack(alignment: .leading, spacing: 5) {
                                        LabeledContent("Default model", value: p.policy.default_model)
                                        if let err = p.policy.after_tool_error {
                                            LabeledContent("After tool error", value: err)
                                        }
                                        if let rec = p.policy.recovery_model {
                                            LabeledContent("Recovery model", value: rec)
                                        }
                                        if let succ = p.policy.after_tool_success {
                                            LabeledContent("After tool success", value: succ)
                                        }
                                        if let steps = p.policy.recovery_after_no_progress_steps {
                                            LabeledContent("Recovery threshold", value: "\(steps) steps")
                                        }
                                        if let dwell = p.policy.minimum_model_dwell_steps {
                                            LabeledContent("Minimum dwell", value: "\(dwell) steps")
                                        }
                                        if !p.reason.isEmpty {
                                            Text("Reason: \(p.reason)").font(.caption).foregroundStyle(.secondary)
                                        }
                                    }
                                    .padding(.vertical, 4)
                                } label: {
                                    HStack {
                                        Text("\(p.policy_id) v\(p.version)").font(.system(size: 12, weight: .medium))
                                        Spacer()
                                        Text(p.status.uppercased())
                                            .font(.system(size: 9, weight: .bold))
                                            .padding(.horizontal, 5)
                                            .padding(.vertical, 2)
                                            .background(p.status == "active" ? Color.green.opacity(0.15) : Color.blue.opacity(0.15))
                                            .foregroundStyle(p.status == "active" ? Color.green : Color.blue)
                                            .clipShape(Capsule())
                                    }
                                }
                            }
                        }
                    }
                    if let date = model.queriedAt {
                        Text("Status checked \(date.formatted(date: .omitted, time: .standard))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .font(.system(size: 12))
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .textSelection(.enabled)
        }
        .task(id: "\(isVisible):\(model.sessionID ?? "")") {
            guard isVisible, model.supported, model.sessionID != nil else { return }
            await model.poll()
        }
    }

    private func elapsed(_ value: UInt64?) -> String {
        value.map { String(format: "%.2f s", Double($0) / 1000) } ?? "Not available"
    }

    private func percent(_ value: UInt32?) -> String {
        value.map { String(format: "%.1f%%", Double($0) / 100) } ?? "Not evaluated"
    }

    private func notice(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.semibold))
            Text(detail).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 12)
    }

    private func result(_ result: EvaluationSnapshot.Result, context: Bool) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(context ? "Context evaluation" : "Model evaluation").font(.subheadline.weight(.semibold))
            LabeledContent("Strategy", value: "\(result.plugin.id) · v\(result.plugin.version)")
            ForEach(Array(result.data.diagnostics.enumerated()), id: \.offset) { _, diagnostic in
                Text(diagnostic.explanation).font(.caption).foregroundStyle(.secondary)
            }
            if result.data.groups.isEmpty { Text("No eligible samples.").foregroundStyle(.secondary) }
            ForEach(Array(result.data.groups.enumerated()), id: \.offset) { _, group in
                DisclosureGroup {
                    if let policy = group.routing_policy_fingerprint {
                        LabeledContent("Routing policy", value: String(policy.prefix(12))).help(policy)
                    }
                    if let registry = group.model_registry_fingerprint {
                        LabeledContent("Model registry", value: String(registry.prefix(12))).help(registry)
                    }
                    let entries = context ? group.report.items ?? [:] : group.report.models ?? [:]
                    ForEach(entries.keys.sorted(), id: \.self) { key in
                        if let metrics = entries[key] {
                            VStack(alignment: .leading, spacing: 7) {
                                Text(displayName(key)).font(.system(size: 12, weight: .medium)).lineLimit(2).help(key)
                                if context {
                                    metric("Eligible runs", "eligible_runs", metrics)
                                    metric("Exposed runs", "exposed_runs", metrics)
                                    metric("Activated runs", "activated_runs", metrics)
                                    metric("Calls started", "started_calls", metrics)
                                    metric("Failed calls", "failed_calls", metrics)
                                } else {
                                    metric("Calls selected", "selected_calls", metrics)
                                    metric("Calls observed", "observed_calls", metrics)
                                    metric("Provider failures", "provider_failures", metrics)
                                    metric("Unknown outcomes", "calls_with_unknown_outcome", metrics)
                                    metric("Total elapsed (ms)", "elapsed_ms_total", metrics)
                                    LabeledContent("Reported input tokens", value: metrics["usage_reported_calls", default: 0] > 0 ? "\(metrics["input_tokens", default: 0])" : "Not reported")
                                    LabeledContent("Reported output tokens", value: metrics["usage_reported_calls", default: 0] > 0 ? "\(metrics["output_tokens", default: 0])" : "Not reported")
                                }
                            }.padding(.vertical, 8)
                        }
                    }
                } label: {
                    Text("\(group.group_id) · \(group.sample_runs) runs").font(.caption)
                }
            }
        }
    }

    private func metric(_ label: String, _ key: String, _ metrics: [String: UInt64]) -> some View {
        LabeledContent(label, value: metrics[key].map(String.init) ?? "Unavailable")
            .foregroundStyle(.secondary)
    }
    private func displayName(_ key: String) -> String {
        guard let data = key.data(using: .utf8), let tuple = try? JSONSerialization.jsonObject(with: data) as? [Any],
              let name = tuple.first as? String else { return key }
        return name
    }
}
