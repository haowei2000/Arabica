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
                        Divider()
                        result(report.context, context: true)
                        Divider()
                        result(report.model, context: false)
                        Text("These observations describe associations, not task correctness or causal contribution.")
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
