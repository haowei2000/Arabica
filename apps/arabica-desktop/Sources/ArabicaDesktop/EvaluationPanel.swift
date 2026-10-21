import SwiftUI

enum BlendTab: String, CaseIterable, Identifiable {
    case runs = "Runs & Costs"
    case analytics = "Observability"
    case tuning = "Blend Tuning"

    var id: String { rawValue }
}

struct EvaluationPanel: View {
    @ObservedObject var model: EvaluationModel
    var isVisible: Bool
    @State private var selectedTab: BlendTab = .runs
    @State private var confirmingRollbackPolicy: EvaluationSnapshot.PolicyRecord?
    @State private var confirmingAction: (action: String, policy: EvaluationSnapshot.PolicyRecord)?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                headerView

                if !model.supported {
                    notice("Blend unavailable", "Reconnect with an Arabica agent that supports background evaluation and dynamic routing.")
                } else if model.sessionID == nil {
                    notice("Choose a conversation", "Open a conversation to inspect its Blend routing, runs, and policy lifecycle.")
                } else {
                    quickOptimizerCard

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

                    Picker("Blend Section", selection: $selectedTab) {
                        ForEach(BlendTab.allCases) { tab in
                            Text(tab.rawValue).tag(tab)
                        }
                    }
                    .pickerStyle(.segmented)
                    .padding(.vertical, 2)

                    switch selectedTab {
                    case .runs:
                        runsView
                    case .analytics:
                        analyticsView
                    case .tuning:
                        tuningView
                    }

                    if let date = model.queriedAt {
                        Text("Status checked \(date.formatted(date: .omitted, time: .standard))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .font(.system(size: 12))
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .textSelection(.enabled)
        }
        .task(id: "\(isVisible):\(model.sessionID ?? "")") {
            guard isVisible, model.supported, model.sessionID != nil else { return }
            await model.poll()
        }
        .confirmationDialog(
            "Rollback Blend Policy",
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
            "Confirm Blend Policy Action",
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
            HStack {
                Label("Blend Routing & Tuning", systemImage: "slider.horizontal.2.square")
                    .font(.headline)
                Spacer()
                if let active = activePolicy {
                    statusBadge("v\(active.version) \(active.status)")
                }
            }
            Text("Multi-model routing, run acceptance, and continuous policy optimization.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var activePolicy: EvaluationSnapshot.PolicyRecord? {
        model.snapshot?.policies?.first(where: { $0.status.lowercased() == "active" })
    }

    private var pendingProposal: EvaluationSnapshot.PolicyRecord? {
        model.snapshot?.policies?.first(where: { $0.status == "candidate" || $0.status == "evaluated" })
    }

    // MARK: - Quick Optimizer Card

    @ViewBuilder
    private var quickOptimizerCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let candidate = pendingProposal {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Label("Optimization Recommended", systemImage: "sparkles")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(.purple)
                        Spacer()
                        statusBadge(candidate.status)
                    }

                    if let prop = candidate.proposal {
                        Text(prop.rationale)
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(.primary)

                        HStack(spacing: 8) {
                            Text("Metric: \(humanReadableMetric(prop.evidence.metric))")
                                .font(.system(size: 10))
                                .foregroundStyle(.secondary)
                            Text("Observed: \(prop.evidence.observed_value)")
                                .font(.system(size: 10, weight: .medium))
                            if let tool = prop.evidence.tool_name {
                                Text("Tool: \(tool)")
                                    .font(.system(size: 10))
                                    .padding(.horizontal, 4)
                                    .background(Color.secondary.opacity(0.12))
                                    .cornerRadius(3)
                            }
                        }
                    }

                    HStack(spacing: 8) {
                        Button {
                            Task {
                                await model.quickApplyPolicy(policyId: candidate.policy_id, version: candidate.version)
                            }
                        } label: {
                            Label("Quick Apply (v\(candidate.version))", systemImage: "bolt.fill")
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.purple)
                        .controlSize(.small)
                        .disabled(model.isRequesting)
                        .help("Approve and activate this optimized policy for all upcoming sessions.")

                        Button {
                            selectedTab = .tuning
                        } label: {
                            Text("Review Details")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    }
                    .padding(.top, 2)
                }
                .padding(12)
                .background(Color.purple.opacity(0.08))
                .cornerRadius(10)
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(Color.purple.opacity(0.2), lineWidth: 1)
                )
            } else {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Active Blend: \(activePolicy?.policy_id ?? "default") · v\(activePolicy?.version ?? 1)")
                            .font(.system(size: 11, weight: .semibold))
                        Text(activePolicy?.policy.default_model ?? "Standard Model")
                            .font(.system(size: 10))
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button {
                        Task { await model.proposePolicy() }
                    } label: {
                        Label("Propose Tuning", systemImage: "sparkles")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(model.isRequesting)
                    .help("Analyze execution failures and propose an optimized Blend routing policy.")
                }
                .padding(10)
                .background(Color.secondary.opacity(0.06))
                .cornerRadius(8)
            }
        }
    }

    private var controlsBar: some View {
        HStack {
            Button { Task { await model.load(refresh: true) } } label: {
                Label("Refresh Status", systemImage: "arrow.clockwise")
            }
            .controlSize(.small)
            .disabled(model.isRequesting)
            .help("Refresh Blend routing metrics and run verification status.")
            Spacer()
            if model.isRequesting {
                ProgressView().controlSize(.small).accessibilityLabel("Updating Blend status")
            }
        }
    }

    // MARK: - Tab 1: Runs & Costs

    @ViewBuilder
    private var runsView: some View {
        if let report = model.snapshot?.report {
            VStack(alignment: .leading, spacing: 4) {
                Text("Run verification & telemetry").font(.subheadline.weight(.semibold))
                Text("Verification checks, execution times, and tool calls per run.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            if let runs = report.runs, !runs.isEmpty {
                Divider()
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
                notice("No runs evaluated yet", "Complete a run to see scored acceptance, costs, and timings.")
            }
        } else {
            notice("No report yet", "Complete an interaction turn to view telemetry. The agent continues operating while background processing finishes.")
        }
    }

    // MARK: - Tab 2: Observability

    @ViewBuilder
    private var analyticsView: some View {
        if let report = model.snapshot?.report {
            result(report.context, context: true)
            Divider()
            result(report.model, context: false)
            Text("Model and context statistics describe empirical token usage; task acceptance is scored separately.")
                .font(.caption).foregroundStyle(.secondary)
        } else {
            notice("No observability data yet", "Observability metrics will appear here after runs complete.")
        }

        if let worker = model.snapshot?.worker_status {
            Divider()
            VStack(alignment: .leading, spacing: 8) {
                Text("Background Worker Health").font(.subheadline.weight(.semibold))
                Text("Asynchronous scoring queue status.").font(.caption).foregroundStyle(.secondary)
                LabeledContent("Completed evaluations", value: "\(worker.completed)")
                LabeledContent("Failed evaluations", value: "\(worker.failed)")
                LabeledContent("Notifications dropped", value: "\(worker.dropped)")
            }
        }
    }

    // MARK: - Tab 3: Blend Tuning

    @ViewBuilder
    private var tuningView: some View {
        let policies = model.snapshot?.policies ?? []

        if let active = activePolicy {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Active Blend Policy").font(.subheadline.weight(.semibold))
                        Text("\(active.policy_id) v\(active.version)")
                            .font(.system(size: 14, weight: .bold))
                            .foregroundStyle(.primary)
                    }
                    Spacer()
                    statusBadge("ACTIVE")
                }

                LabeledContent("Default model", value: active.policy.default_model)
                if let err = active.policy.after_tool_error {
                    LabeledContent("After tool error", value: err)
                }
                if let succ = active.policy.after_tool_success {
                    LabeledContent("After tool success", value: succ)
                }
                if let rec = active.policy.recovery_model {
                    LabeledContent("Recovery model", value: rec)
                }
                if let steps = active.policy.recovery_after_no_progress_steps {
                    LabeledContent("Recovery threshold", value: "\(steps) steps")
                }
                if let dwell = active.policy.minimum_model_dwell_steps {
                    LabeledContent("Minimum dwell", value: "\(dwell) steps")
                }
                if let routes = active.policy.tool_routes, !routes.isEmpty {
                    Text("Tool Routes:").font(.caption.weight(.medium))
                    ForEach(routes) { r in
                        Text("  • \(r.id): -> \(r.model)").font(.system(size: 11)).foregroundStyle(.secondary)
                    }
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
            Text("Policy Versions (\(policies.count))").font(.subheadline.weight(.semibold))
            Spacer()
            Button {
                Task { await model.proposePolicy() }
            } label: {
                Label("Propose Tuning", systemImage: "sparkles")
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
                                Text("Proposal rationale & evidence").font(.caption.weight(.semibold))
                                Text(prop.rationale).font(.system(size: 11)).foregroundStyle(.primary)
                                Text("Metric: \(humanReadableMetric(prop.evidence.metric)) (observed: \(prop.evidence.observed_value), threshold: \(prop.evidence.threshold))")
                                    .font(.system(size: 10)).foregroundStyle(.secondary)
                                if let tool = prop.evidence.tool_name {
                                    Text("Affected tool: \(tool)").font(.system(size: 10)).foregroundStyle(.secondary)
                                }
                            }
                        }

                        if !p.reason.isEmpty {
                            Text("Audit reason: \(p.reason)").font(.caption).foregroundStyle(.secondary)
                        }

                        // Lifecycle Action Buttons
                        HStack(spacing: 8) {
                            if p.status == "candidate" || p.status == "evaluated" {
                                Button("Quick Apply") {
                                    Task {
                                        await model.quickApplyPolicy(policyId: p.policy_id, version: p.version)
                                    }
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(.purple)
                                .controlSize(.small)

                                Button("Approve") {
                                    confirmingAction = (action: "approve", policy: p)
                                }
                                .buttonStyle(.bordered)
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

    private func humanReadableMetric(_ metric: String) -> String {
        switch metric {
        case "complex_category_failure_rate":
            return "Failure Rate in Complex Tasks"
        case "tool_failure_rate":
            return "Tool Failure Rate"
        case "no_progress_steps":
            return "Steps Without Progress"
        case "dwell_oscillation":
            return "Model Oscillation"
        default:
            return metric.replacingOccurrences(of: "_", with: " ").capitalized
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
            Text(context ? "Context token efficiency" : "Model selection distribution").font(.subheadline.weight(.semibold))
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
