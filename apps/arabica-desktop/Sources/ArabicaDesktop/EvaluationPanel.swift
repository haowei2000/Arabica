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
                    ContentUnavailableView(
                        "Blend Unavailable",
                        systemImage: "slider.horizontal.2.square",
                        description: Text("Connect with an Arabica agent that supports background evaluation and dynamic routing.")
                    )
                    .padding(.vertical, 24)
                } else if model.sessionID == nil {
                    ContentUnavailableView(
                        "No Conversation Selected",
                        systemImage: "bubble.left.and.bubble.right",
                        description: Text("Select a conversation from the sidebar to inspect its Blend routing, runs, and policy lifecycle.")
                    )
                    .padding(.vertical, 24)
                } else {
                    quickOptimizerCard

                    controlsBar

                    if let message = model.message {
                        Label(message, systemImage: "info.circle")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    if let error = model.error {
                        VStack(alignment: .leading, spacing: 6) {
                            Label(error, systemImage: "exclamationmark.triangle.fill")
                                .font(.footnote)
                                .foregroundStyle(.orange)
                            Button("Retry Query") { Task { await model.load() } }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                                .disabled(model.isRequesting)
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
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                            .monospacedDigit()
                    }
                }
            }
            .padding(16)
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

    // MARK: - Header

    private var headerView: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .center) {
                Label("Blend Routing & Tuning", systemImage: "slider.horizontal.2.square")
                    .font(.headline)
                Spacer()
                if let active = activePolicy {
                    statusBadge("v\(active.version) \(active.status)")
                }
            }
            Text("Multi-model routing, run acceptance, and continuous policy optimization.")
                .font(.footnote)
                .foregroundStyle(.secondary)
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
        cardContainer {
            if let candidate = pendingProposal {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Label("Optimization Recommended", systemImage: "sparkles")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.purple)
                        Spacer()
                        statusBadge(candidate.status)
                    }

                    if let prop = candidate.proposal {
                        Text(prop.rationale)
                            .font(.callout)
                            .foregroundStyle(.primary)

                        HStack(spacing: 8) {
                            Text("Metric: \(humanReadableMetric(prop.evidence.metric))")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                            Text("Observed: \(prop.evidence.observed_value)")
                                .font(.footnote.weight(.medium))
                                .monospacedDigit()
                            if let tool = prop.evidence.tool_name {
                                Text("Tool: \(tool)")
                                    .font(.caption2.weight(.medium))
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(Color(nsColor: .quaternaryLabelColor), in: Capsule())
                            }
                        }
                    }

                    HStack(spacing: 10) {
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

                        Button("Review Details") {
                            selectedTab = .tuning
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    }
                    .padding(.top, 2)
                }
            } else {
                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Active: \(activePolicy?.policy_id ?? "default") · v\(activePolicy?.version ?? 1)")
                            .font(.subheadline.weight(.semibold))
                            .monospacedDigit()
                        Text(activePolicy?.policy.default_model ?? "Standard Model")
                            .font(.footnote)
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
            }
        }
    }

    private var controlsBar: some View {
        HStack {
            Button { Task { await model.load(refresh: true) } } label: {
                Label("Refresh Status", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
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
                Text("Run Verification & Telemetry").font(.subheadline.weight(.semibold))
                Text("Verification checks, execution times, and tool calls per run.")
                    .font(.footnote).foregroundStyle(.secondary)
            }

            if let runs = report.runs, !runs.isEmpty {
                ForEach(runs.reversed()) { run in
                    cardContainer {
                        DisclosureGroup {
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text("Execution:")
                                        .foregroundStyle(.secondary)
                                        .font(.footnote)
                                    Text(run.snapshot.terminal_status.capitalized)
                                        .font(.footnote.weight(.medium))
                                    Spacer()
                                    if let verified = run.acceptance?.verified_success {
                                        Text(verified ? "VERIFIED PASS" : "FAILED")
                                            .font(.caption2.weight(.bold))
                                            .padding(.horizontal, 6)
                                            .padding(.vertical, 2)
                                            .background(verified ? Color.green.opacity(0.12) : Color.red.opacity(0.12), in: Capsule())
                                            .foregroundStyle(verified ? Color.green : Color.red)
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
                                    .monospacedDigit()

                                if let results = run.acceptance?.results, !results.isEmpty {
                                    Divider().padding(.vertical, 2)
                                    Text("Acceptance Criteria").font(.footnote.weight(.semibold))
                                    ForEach(results) { res in
                                        HStack(alignment: .top, spacing: 8) {
                                            Image(systemName: res.status.lowercased().contains("pass") ? "checkmark.circle.fill" : "xmark.circle.fill")
                                                .foregroundStyle(res.status.lowercased().contains("pass") ? .green : .red)
                                                .font(.subheadline)
                                            VStack(alignment: .leading, spacing: 2) {
                                                Text(res.criterion_id).font(.footnote.weight(.medium))
                                                if let ev = res.evidence, !ev.isEmpty {
                                                    Text(ev).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                                                }
                                            }
                                            Spacer()
                                            Text(res.status.capitalized)
                                                .font(.caption.weight(.medium))
                                                .foregroundStyle(res.status.lowercased().contains("pass") ? .green : .red)
                                        }
                                        .padding(.vertical, 2)
                                    }
                                }

                                if let tools = run.snapshot.tools, !tools.isEmpty {
                                    Divider().padding(.vertical, 2)
                                    Text("Observed Tool Calls (\(tools.count))").font(.footnote.weight(.semibold))
                                    ForEach(tools) { tool in
                                        HStack {
                                            Text("Step \(tool.step):")
                                                .foregroundStyle(.secondary)
                                                .font(.caption)
                                                .monospacedDigit()
                                            Text(tool.name).font(.footnote.weight(.medium))
                                            Spacer()
                                            Text(tool.outcome)
                                                .font(.caption)
                                                .foregroundStyle(tool.outcome.lowercased().contains("error") ? .red : .secondary)
                                            Text(elapsed(tool.elapsed_ms))
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
                                                .monospacedDigit()
                                        }
                                    }
                                }
                            }
                            .padding(.top, 6)
                        } label: {
                            HStack {
                                Text("Run \(run.id.prefix(8)) · \(run.snapshot.terminal_status)")
                                    .font(.subheadline.weight(.medium))
                                    .monospacedDigit()
                                Spacer()
                                if let verified = run.acceptance?.verified_success {
                                    Image(systemName: verified ? "checkmark.circle.fill" : "xmark.circle.fill")
                                        .foregroundStyle(verified ? .green : .red)
                                }
                            }
                        }
                    }
                }
            } else {
                notice("No Runs Evaluated Yet", "Complete a run to see scored acceptance, costs, and timings.")
            }
        } else {
            notice("No Report Yet", "Complete an interaction turn to view telemetry. The agent continues operating while background processing finishes.")
        }
    }

    // MARK: - Tab 2: Observability

    @ViewBuilder
    private var analyticsView: some View {
        if let report = model.snapshot?.report {
            cardContainer {
                result(report.context, context: true)
            }
            cardContainer {
                result(report.model, context: false)
            }
            Text("Model and context statistics describe empirical token usage; task acceptance is scored separately.")
                .font(.footnote).foregroundStyle(.secondary)
        } else {
            notice("No Observability Data Yet", "Observability metrics will appear here after runs complete.")
        }

        if let worker = model.snapshot?.worker_status {
            cardContainer {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Background Worker Health").font(.subheadline.weight(.semibold))
                    Text("Asynchronous scoring queue status.").font(.footnote).foregroundStyle(.secondary)
                    LabeledContent("Completed evaluations", value: "\(worker.completed)").monospacedDigit()
                    LabeledContent("Failed evaluations", value: "\(worker.failed)").monospacedDigit()
                    LabeledContent("Notifications dropped", value: "\(worker.dropped)").monospacedDigit()
                }
            }
        }
    }

    // MARK: - Tab 3: Blend Tuning

    @ViewBuilder
    private var tuningView: some View {
        let policies = model.snapshot?.policies ?? []

        if let active = activePolicy {
            cardContainer {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Active Blend Policy").font(.subheadline.weight(.semibold))
                            Text("\(active.policy_id) v\(active.version)")
                                .font(.title3.weight(.bold))
                                .monospacedDigit()
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
                        LabeledContent("Recovery threshold", value: "\(steps) steps").monospacedDigit()
                    }
                    if let dwell = active.policy.minimum_model_dwell_steps {
                        LabeledContent("Minimum dwell", value: "\(dwell) steps").monospacedDigit()
                    }
                    if let routes = active.policy.tool_routes, !routes.isEmpty {
                        Text("Tool Routes:").font(.footnote.weight(.medium))
                        ForEach(routes) { r in
                            Text("  • \(r.id): -> \(r.model)").font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    if let actAt = active.activated_at {
                        LabeledContent("Activated at", value: actAt).monospacedDigit()
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
            }
        }

        HStack {
            Text("Policy Versions (\(policies.count))")
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
            Spacer()
            Button {
                Task { await model.proposePolicy() }
            } label: {
                Label("Propose Tuning", systemImage: "sparkles")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .disabled(model.isRequesting)
            .help("Derive bounded proposals informed by evaluated evidence from this session.")
        }

        if policies.isEmpty {
            notice("No Policies Registered", "Configured policies and evaluated proposals will appear here.")
        } else {
            ForEach(policies) { p in
                cardContainer {
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
                                LabeledContent("Recovery threshold", value: "\(steps) steps").monospacedDigit()
                            }
                            if let dwell = p.policy.minimum_model_dwell_steps {
                                LabeledContent("Minimum dwell", value: "\(dwell) steps").monospacedDigit()
                            }
                            if let routes = p.policy.tool_routes, !routes.isEmpty {
                                Text("Explicit tool routes:").font(.footnote.weight(.medium))
                                ForEach(routes) { r in
                                    Text("  • \(r.id): -> \(r.model)").font(.footnote).foregroundStyle(.secondary)
                                }
                            }

                            if let prop = p.proposal {
                                Divider().padding(.vertical, 2)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("Proposal Rationale & Evidence").font(.footnote.weight(.semibold))
                                    Text(prop.rationale).font(.callout).foregroundStyle(.primary)
                                    Text("Metric: \(humanReadableMetric(prop.evidence.metric)) (observed: \(prop.evidence.observed_value), threshold: \(prop.evidence.threshold))")
                                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                                    if let tool = prop.evidence.tool_name {
                                        Text("Affected tool: \(tool)").font(.caption).foregroundStyle(.secondary)
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
                                    Button("Activate for New Runs") {
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
                        .padding(.top, 6)
                    } label: {
                        HStack {
                            Text("\(p.policy_id) v\(p.version)")
                                .font(.subheadline.weight(.medium))
                                .monospacedDigit()
                            Spacer()
                            statusBadge(p.status)
                        }
                    }
                }
            }
        }
    }

    // MARK: - Helpers

    @ViewBuilder
    private func cardContainer<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            content()
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(Color(nsColor: .separatorColor).opacity(0.6), lineWidth: 1)
        )
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
            case "active": (.green.opacity(0.12), .green)
            case "approved": (.orange.opacity(0.12), .orange)
            case "evaluated": (.purple.opacity(0.12), .purple)
            case "candidate": (.blue.opacity(0.12), .blue)
            case "rejected": (.red.opacity(0.12), .red)
            default: (Color(nsColor: .quaternaryLabelColor), .secondary)
            }
        }()
        return Text(status.uppercased())
            .font(.caption2.weight(.bold))
            .monospacedDigit()
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(bg, in: Capsule())
            .foregroundStyle(fg)
    }

    private func elapsed(_ value: UInt64?) -> String {
        value.map { String(format: "%.2f s", Double($0) / 1000) } ?? "Not available"
    }

    private func percent(_ value: UInt32?) -> String {
        value.map { String(format: "%.1f%%", Double($0) / 100) } ?? "Not evaluated"
    }

    private func notice(_ title: String, _ detail: String) -> some View {
        cardContainer {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.footnote).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    private func result(_ result: EvaluationSnapshot.Result, context: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(context ? "Context Token Efficiency" : "Model Selection Distribution")
                .font(.subheadline.weight(.semibold))
            LabeledContent("Strategy", value: "\(result.plugin.id) · v\(result.plugin.version)")
                .font(.footnote)
            ForEach(Array(result.data.diagnostics.enumerated()), id: \.offset) { _, diagnostic in
                Text(diagnostic.explanation).font(.caption).foregroundStyle(.secondary)
            }
            if result.data.groups.isEmpty {
                Text("No eligible samples.").font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(Array(result.data.groups.enumerated()), id: \.offset) { _, group in
                DisclosureGroup {
                    VStack(alignment: .leading, spacing: 6) {
                        if let policy = group.routing_policy_fingerprint {
                            LabeledContent("Routing policy", value: String(policy.prefix(12)))
                                .font(.caption)
                                .help(policy)
                        }
                        if let registry = group.model_registry_fingerprint {
                            LabeledContent("Model registry", value: String(registry.prefix(12)))
                                .font(.caption)
                                .help(registry)
                        }
                        let entries = context ? group.report.items ?? [:] : group.report.models ?? [:]
                        ForEach(entries.keys.sorted(), id: \.self) { key in
                            if let metrics = entries[key] {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(displayName(key))
                                        .font(.footnote.weight(.medium))
                                        .lineLimit(2)
                                        .help(key)
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
                                            .font(.caption)
                                            .monospacedDigit()
                                        LabeledContent("Reported output tokens", value: metrics["usage_reported_calls", default: 0] > 0 ? "\(metrics["output_tokens", default: 0])" : "Not reported")
                                            .font(.caption)
                                            .monospacedDigit()
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                        }
                    }
                    .padding(.top, 4)
                } label: {
                    Text("\(group.group_id) · \(group.sample_runs) runs")
                        .font(.footnote)
                        .monospacedDigit()
                }
            }
        }
    }

    private func metric(_ label: String, _ key: String, _ metrics: [String: UInt64]) -> some View {
        LabeledContent(label, value: metrics[key].map(String.init) ?? "Unavailable")
            .font(.caption)
            .monospacedDigit()
            .foregroundStyle(.secondary)
    }

    private func displayName(_ key: String) -> String {
        guard let data = key.data(using: .utf8), let tuple = try? JSONSerialization.jsonObject(with: data) as? [Any],
              let name = tuple.first as? String else { return key }
        return name
    }
}
