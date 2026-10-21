import SwiftUI

enum EvaluationTab: String, CaseIterable, Identifiable {
    case runAudit = "Run Audit"
    case analytics = "Model & Context"
    case lifecycle = "Policy Lifecycle"

    var id: String { rawValue }
}

struct EvaluationPanel: View {
    @ObservedObject var model: EvaluationModel
    var isVisible: Bool
    @State private var selectedTab: EvaluationTab = .runAudit
    @State private var confirmingRollbackPolicy: EvaluationSnapshot.PolicyRecord?
    @State private var confirmingAction: (action: String, policy: EvaluationSnapshot.PolicyRecord)?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                headerView

                if !model.supported {
                    notice("Evaluation unavailable", "Reconnect with an Arabica agent that supports background evaluation.")
                } else if model.sessionID == nil {
                    notice("Choose a conversation", "Open a conversation to inspect its evaluation report.")
                } else {
                    controlsBar

                    if let message = model.message {
                        Text(message).font(.caption).foregroundStyle(.secondary)
                    }
                    if let error = model.error {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(error).font(.caption).foregroundStyle(.orange)
                            Button("Retry query") { Task { await model.load() } }.disabled(model.isRequesting)
                        }
                    }

                    Picker("Evaluation Section", selection: $selectedTab) {
                        ForEach(EvaluationTab.allCases) { tab in
                            Text(tab.rawValue).tag(tab)
                        }
                    }
                    .pickerStyle(.segmented)
                    .padding(.vertical, 2)

                    switch selectedTab {
                    case .runAudit:
                        runAuditView
                    case .analytics:
                        analyticsView
                    case .lifecycle:
                        policyLifecycleView
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
        .confirmationDialog(
            "Rollback Policy",
            isPresented: Binding(
                get: { confirmingRollbackPolicy != nil },
                set: { if !$0 { confirmingRollbackPolicy = nil } }
            ),
            presenting: confirmingRollbackPolicy
        ) { policy in
            Button("Rollback to \(policy.policy_id) v\(policy.version)", role: .destructive) {
                Task {
                    await model.rollbackPolicy(policyId: policy.policy_id, targetVersion: policy.version)
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: { policy in
            Text("Rollback will reinstate version \(policy.version) as active for new sessions. Existing active runs remain unaffected.")
        }
        .confirmationDialog(
            "Confirm Action",
            isPresented: Binding(
                get: { confirmingAction != nil },
                set: { if !$0 { confirmingAction = nil } }
            ),
            presenting: confirmingAction
        ) { item in
            Button("\(item.action.capitalized) \(item.policy.policy_id) v\(item.policy.version)") {
                Task {
                    switch item.action {
                    case "approve":
                        await model.approvePolicy(policyId: item.policy.policy_id, version: item.policy.version)
                    case "reject":
                        await model.rejectPolicy(policyId: item.policy.policy_id, version: item.policy.version)
                    case "activate":
                        await model.activatePolicy(policyId: item.policy.policy_id, version: item.policy.version)
                    default:
                        break
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: { item in
            Text("Are you sure you want to \(item.action) policy version \(item.policy.version)?")
        }
    }

    private var headerView: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Background evaluation").font(.headline)
            Text("Decoupled scoring, acceptance checks, and policy lifecycle.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var controlsBar: some View {
        HStack {
            Button { Task { await model.load(refresh: true) } } label: {
                Label("Request refresh", systemImage: "arrow.clockwise")
            }
            .disabled(model.isRequesting)
            .help("Queue background evaluation; your conversation keeps running.")
            Spacer()
            if model.isRequesting {
                ProgressView().controlSize(.small).accessibilityLabel("Reading evaluation status")
            }
        }
    }

    // MARK: - Tab 1: Run Audit

    @ViewBuilder
    private var runAuditView: some View {
        if let report = model.snapshot?.report {
            VStack(alignment: .leading, spacing: 6) {
                Text("Latest available report").font(.subheadline.weight(.semibold))
                LabeledContent("Through event sequence", value: "\(report.checkpoint.sequence)")
                Text("Cached results update asynchronously after runs complete.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            if let runs = report.runs, !runs.isEmpty {
                Divider()
                Text("Run outcomes & acceptance").font(.subheadline.weight(.semibold))
                ForEach(runs.reversed()) { run in
                    DisclosureGroup {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("Execution:")
                                    .foregroundStyle(.secondary)
                                Text(run.snapshot.terminal_status.capitalized)
                                    .fontWeight(.medium)
                                Spacer()
                                if let verified = run.acceptance?.verified_success {
                                    Text(verified ? "VERIFIED PASS" : "FAILED")
                                        .font(.system(size: 9, weight: .bold))
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(verified ? Color.green.opacity(0.15) : Color.red.opacity(0.15))
                                        .foregroundStyle(verified ? Color.green : Color.red)
                                        .clipShape(Capsule())
                                }
                            }

                            LabeledContent("Completion of evaluated criteria", value: percent(run.acceptance?.completion_bps))
                            LabeledContent("Evaluation coverage", value: percent(run.acceptance?.coverage_bps))
                            LabeledContent("Total wall time", value: elapsed(run.snapshot.timing.wall_ms))
                            LabeledContent("Queue wait", value: elapsed(run.snapshot.timing.queue_ms))
                            LabeledContent("Approval wait", value: elapsed(run.snapshot.timing.approval_wait_ms))
                            LabeledContent("Active execution time", value: elapsed(run.snapshot.timing.active_ms))
                            LabeledContent("Model call total", value: elapsed(run.snapshot.timing.model_call_ms_total))
                            LabeledContent("Runner call total", value: elapsed(run.snapshot.timing.runner_ms_total))
                            LabeledContent("Usage reported calls", value: "\(run.snapshot.timing.usage_reported_calls) / \(run.snapshot.timing.model_calls)")

                            if let results = run.acceptance?.results, !results.isEmpty {
                                Divider().padding(.vertical, 2)
                                Text("Acceptance criteria").font(.caption.weight(.semibold))
                                ForEach(results) { res in
                                    HStack(alignment: .top) {
                                        Image(systemName: res.status.lowercased().contains("pass") ? "checkmark.circle.fill" : "xmark.circle.fill")
                                            .foregroundStyle(res.status.lowercased().contains("pass") ? .green : .red)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(res.criterion_id).font(.system(size: 11, weight: .medium))
                                            if let ev = res.evidence, !ev.isEmpty {
                                                Text(ev).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(3)
                                            }
                                        }
                                        Spacer()
                                        Text(res.status.capitalized)
                                            .font(.system(size: 10, weight: .medium))
                                            .foregroundStyle(res.status.lowercased().contains("pass") ? .green : .red)
                                    }
                                    .padding(.vertical, 2)
                                }
                            }

                            if let tools = run.snapshot.tools, !tools.isEmpty {
                                Divider().padding(.vertical, 2)
                                Text("Observed tool calls (\(tools.count))").font(.caption.weight(.semibold))
                                ForEach(tools) { tool in
                                    HStack {
                                        Text("Step \(tool.step):").foregroundStyle(.secondary).font(.system(size: 10))
                                        Text(tool.name).font(.system(size: 11, weight: .medium))
                                        Spacer()
                                        Text(tool.outcome)
                                            .font(.system(size: 10))
                                            .foregroundStyle(tool.outcome.lowercased().contains("error") ? .red : .secondary)
                                        Text(elapsed(tool.elapsed_ms)).font(.system(size: 10)).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                        .padding(.vertical, 6)
                    } label: {
                        HStack {
                            Text("Run \(run.id.prefix(8)) · \(run.snapshot.terminal_status)").font(.caption.weight(.medium))
                            Spacer()
                            if let verified = run.acceptance?.verified_success {
                                Image(systemName: verified ? "checkmark.circle.fill" : "xmark.circle.fill")
                                    .foregroundStyle(verified ? .green : .red)
                            }
                        }
                    }
                }
            } else {
                notice("No runs evaluated yet", "Complete a run to see scored acceptance and timing.")
            }
        } else {
            notice("No report yet", "Finish a run, then request a refresh. The agent continues operating while evaluation is pending.")
        }
    }

    // MARK: - Tab 2: Model & Context Analytics

    @ViewBuilder
    private var analyticsView: some View {
        if let report = model.snapshot?.report {
            result(report.context, context: true)
            Divider()
            result(report.model, context: false)
            Text("Model and context statistics describe empirical associations; task acceptance is scored separately.")
                .font(.caption).foregroundStyle(.secondary)
        } else {
            notice("No analytics yet", "Analytics will appear here after runs complete.")
        }

        if let worker = model.snapshot?.worker_status {
            Divider()
            VStack(alignment: .leading, spacing: 8) {
                Text("Worker totals").font(.subheadline.weight(.semibold))
                Text("Across all conversations in this agent process.").font(.caption).foregroundStyle(.secondary)
                LabeledContent("Completed evaluations", value: "\(worker.completed)")
                LabeledContent("Failed evaluations", value: "\(worker.failed)")
                LabeledContent("Notifications dropped", value: "\(worker.dropped)")
            }
        }
    }

    // MARK: - Tab 3: Policy Lifecycle

    @ViewBuilder
    private var policyLifecycleView: some View {
        let policies = model.snapshot?.policies ?? []
        let activePolicy = policies.first(where: { $0.status.lowercased() == "active" })

        if let active = activePolicy {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Active Policy").font(.subheadline.weight(.semibold))
                        Text("\(active.policy_id) v\(active.version)")
                            .font(.system(size: 14, weight: .bold))
                            .foregroundStyle(.primary)
                    }
                    Spacer()
                    Text("ACTIVE")
                        .font(.system(size: 10, weight: .bold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Color.green.opacity(0.15))
                        .foregroundStyle(Color.green)
                        .clipShape(Capsule())
                }

                LabeledContent("Default model", value: active.policy.default_model)
                if let rec = active.policy.recovery_model {
                    LabeledContent("Recovery model", value: rec)
                }
                if let steps = active.policy.recovery_after_no_progress_steps {
                    LabeledContent("Recovery threshold", value: "\(steps) steps")
                }
                if let dwell = active.policy.minimum_model_dwell_steps {
                    LabeledContent("Minimum dwell", value: "\(dwell) steps")
                }
                if let actAt = active.activated_at {
                    LabeledContent("Activated at", value: actAt)
                }

                let previousVersions = policies.filter { $0.version < active.version && ($0.status == "retired" || $0.status == "approved") }
                if let mostRecentPrev = previousVersions.first {
                    Button {
                        confirmingRollbackPolicy = mostRecentPrev
                    } label: {
                        Label("Rollback to v\(mostRecentPrev.version)", systemImage: "arrow.uturn.backward")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .padding(.top, 4)
                }
            }
            .padding(12)
            .background(Color.secondary.opacity(0.08))
            .cornerRadius(8)
            Divider()
        }

        HStack {
            Text("All policy versions (\(policies.count))").font(.subheadline.weight(.semibold))
            Spacer()
            Button {
                Task { await model.proposePolicy() }
            } label: {
                Label("Propose candidates", systemImage: "sparkles")
            }
            .controlSize(.small)
            .disabled(model.isRequesting)
            .help("Derive bounded proposals informed by evaluated evidence from this session.")
        }

        if policies.isEmpty {
            notice("No policies registered", "Configured policies and evaluated proposals will appear here.")
        } else {
            ForEach(policies) { p in
                DisclosureGroup {
                    VStack(alignment: .leading, spacing: 8) {
                        LabeledContent("Default model", value: p.policy.default_model)
                        if let err = p.policy.after_tool_error {
                            LabeledContent("After tool error", value: err)
                        }
                        if let succ = p.policy.after_tool_success {
                            LabeledContent("After tool success", value: succ)
                        }
                        if let rec = p.policy.recovery_model {
                            LabeledContent("Recovery model", value: rec)
                        }
                        if let steps = p.policy.recovery_after_no_progress_steps {
                            LabeledContent("Recovery threshold", value: "\(steps) steps")
                        }
                        if let dwell = p.policy.minimum_model_dwell_steps {
                            LabeledContent("Minimum dwell", value: "\(dwell) steps")
                        }
                        if let routes = p.policy.tool_routes, !routes.isEmpty {
                            Text("Explicit tool routes:").font(.caption.weight(.medium))
                            ForEach(routes) { r in
                                Text("  • \(r.id): -> \(r.model)").font(.system(size: 11)).foregroundStyle(.secondary)
                            }
                        }

                        if let prop = p.proposal {
                            Divider().padding(.vertical, 2)
                            VStack(alignment: .leading, spacing: 3) {
                                Text("Proposal evidence").font(.caption.weight(.semibold))
                                Text(prop.rationale).font(.system(size: 11)).foregroundStyle(.primary)
                                Text("Metric: \(prop.evidence.metric) (observed: \(prop.evidence.observed_value), threshold: \(prop.evidence.threshold))")
                                    .font(.system(size: 10)).foregroundStyle(.secondary)
                            }
                        }

                        if !p.reason.isEmpty {
                            Text("Audit reason: \(p.reason)").font(.caption).foregroundStyle(.secondary)
                        }

                        // Lifecycle Action Buttons
                        HStack(spacing: 8) {
                            if p.status == "candidate" || p.status == "evaluated" {
                                Button("Approve for promotion") {
                                    confirmingAction = (action: "approve", policy: p)
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(.orange)
                                .controlSize(.small)

                                Button("Reject") {
                                    confirmingAction = (action: "reject", policy: p)
                                }
                                .buttonStyle(.bordered)
                                .tint(.red)
                                .controlSize(.small)
                            } else if p.status == "approved" {
                                Button("Activate for new runs") {
                                    confirmingAction = (action: "activate", policy: p)
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(.green)
                                .controlSize(.small)
                            } else if p.status == "retired" {
                                Button("Reactivate (Rollback)") {
                                    confirmingRollbackPolicy = p
                                }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                            }
                        }
                        .padding(.top, 4)
                    }
                    .padding(.vertical, 4)
                } label: {
                    HStack {
                        Text("\(p.policy_id) v\(p.version)").font(.system(size: 12, weight: .medium))
                        Spacer()
                        statusBadge(p.status)
                    }
                }
            }
        }
    }

    private func statusBadge(_ status: String) -> some View {
        let (bg, fg): (Color, Color) = {
            switch status.lowercased() {
            case "active": (.green.opacity(0.15), .green)
            case "approved": (.orange.opacity(0.15), .orange)
            case "evaluated": (.purple.opacity(0.15), .purple)
            case "candidate": (.blue.opacity(0.15), .blue)
            case "rejected": (.red.opacity(0.15), .red)
            default: (.secondary.opacity(0.15), .secondary)
            }
        }()
        return Text(status.uppercased())
            .font(.system(size: 9, weight: .bold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(bg)
            .foregroundStyle(fg)
            .clipShape(Capsule())
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
