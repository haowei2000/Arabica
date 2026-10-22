import SwiftUI

enum BlendInspectorTab: String, CaseIterable, Identifiable {
    case evaluate = "Evaluate"
    case generate = "Generate Policy"
    case update = "Update Policy"

    var id: String { rawValue }
}

struct EvaluationPanel: View {
    @ObservedObject var model: EvaluationModel
    var isVisible: Bool
    @State private var selectedTab: BlendInspectorTab = .evaluate
    @State private var selectedPolicyFilter: String = "all"
    @State private var selectedRunIDs: Set<String> = []
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
                    tabPicker

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

                    switch selectedTab {
                    case .evaluate:
                        evaluateSection
                    case .generate:
                        generatePolicySection
                    case .update:
                        updatePolicySection
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
        .onChange(of: model.sessionID) { _, _ in
            selectedRunIDs.removeAll()
            selectedPolicyFilter = "all"
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

    // MARK: - Header & Tab Picker

    private var headerView: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .center) {
                Label("Blend Inspector", systemImage: "slider.horizontal.2.square")
                    .font(.headline)
                Spacer()
                if let active = activePolicy {
                    statusBadge("v\(active.version) \(active.status)")
                }
            }
            Text("Continuous multi-model evaluation, candidate generation, and policy lifecycle.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private var tabPicker: some View {
        Picker("Inspector Mode", selection: $selectedTab) {
            ForEach(BlendInspectorTab.allCases) { tab in
                Text(tab.rawValue).tag(tab)
            }
        }
        .pickerStyle(.segmented)
        .padding(.vertical, 2)
    }

    private var activePolicy: EvaluationSnapshot.PolicyRecord? {
        model.snapshot?.policies?.first(where: { $0.status.lowercased() == "active" })
    }

    private var pendingProposal: EvaluationSnapshot.PolicyRecord? {
        model.snapshot?.policies?.first(where: { $0.status == "candidate" || $0.status == "evaluated" })
    }

    // MARK: - SECTION 1: EVALUATE

    @ViewBuilder
    private var evaluateSection: some View {
        // 1. Current Session Context Card
        cardContainer {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Target Conversation")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(model.sessionID ?? "Unknown Session")
                        .font(.subheadline.weight(.semibold))
                        .monospacedDigit()
                        .lineLimit(1)
                }
                Spacer()
                Button {
                    Task { await model.load(refresh: true) }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .controlSize(.small)
                .disabled(model.isRequesting)
            }
        }

        // 2. Policy Filter & Run Selection
        let allRuns = model.snapshot?.report?.runs ?? []
        let availablePolicies = extractAvailablePolicies(from: allRuns)

        cardContainer {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("Filter Runs by Policy")
                        .font(.footnote.weight(.semibold))
                    Spacer()
                    Picker("Policy Filter", selection: $selectedPolicyFilter) {
                        Text("All Policies").tag("all")
                        ForEach(availablePolicies, id: \.self) { policy in
                            Text(policy).tag(policy)
                        }
                    }
                    .pickerStyle(.menu)
                    .controlSize(.small)
                }

                Divider().padding(.vertical, 2)

                HStack {
                    Text("Turns / Processes (\(filteredRuns.count))")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Select All") {
                        selectedRunIDs = Set(filteredRuns.map(\.id))
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)

                    Text("·").foregroundStyle(.tertiary)

                    Button("Clear") {
                        selectedRunIDs.removeAll()
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                }

                if filteredRuns.isEmpty {
                    Text("No runs matching the selected policy.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 4)
                } else {
                    VStack(spacing: 6) {
                        ForEach(filteredRuns) { run in
                            runSelectionRow(run)
                        }
                    }
                }
            }
        }

        // 3. Evaluation Scoring & Diagnostic Report
        if !effectiveEvaluatedRuns.isEmpty {
            scoringAndDiagnosticReport
        } else {
            cardContainer {
                Text("Select one or more runs above to generate an evaluation score and diagnostic report.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 8)
            }
        }
    }

    private func extractAvailablePolicies(from runs: [EvaluationSnapshot.Run]) -> [String] {
        var set = Set<String>()
        if let policies = model.snapshot?.policies {
            for p in policies {
                set.insert("\(p.policy_id) v\(p.version)")
            }
        }
        for run in runs {
            if let fps = run.snapshot.policy_fingerprints {
                for fp in fps {
                    set.insert(String(fp.prefix(12)))
                }
            }
        }
        return Array(set).sorted()
    }

    private var filteredRuns: [EvaluationSnapshot.Run] {
        guard let runs = model.snapshot?.report?.runs else { return [] }
        if selectedPolicyFilter == "all" {
            return runs
        }
        return runs.filter { run in
            if let fps = run.snapshot.policy_fingerprints {
                return fps.contains(where: { $0.hasPrefix(selectedPolicyFilter) || selectedPolicyFilter.contains($0.prefix(8)) })
            }
            return true
        }
    }

    private var effectiveEvaluatedRuns: [EvaluationSnapshot.Run] {
        if selectedRunIDs.isEmpty {
            return filteredRuns
        }
        return filteredRuns.filter { selectedRunIDs.contains($0.id) }
    }

    @ViewBuilder
    private func runSelectionRow(_ run: EvaluationSnapshot.Run) -> some View {
        let isSelected = selectedRunIDs.isEmpty || selectedRunIDs.contains(run.id)
        HStack(spacing: 8) {
            Button {
                toggleRunSelection(run.id)
            } label: {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? Color.accentColor : Color.secondary)
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text("Run \(run.id.prefix(8))")
                        .font(.footnote.weight(.medium))
                        .monospacedDigit()
                    Text("· \(run.snapshot.terminal_status.capitalized)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    if let verified = run.acceptance?.verified_success {
                        Text(verified ? "PASS" : "FAIL")
                            .font(.caption2.weight(.bold))
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(verified ? Color.green.opacity(0.12) : Color.red.opacity(0.12), in: Capsule())
                            .foregroundStyle(verified ? Color.green : Color.red)
                    }
                }
                HStack(spacing: 8) {
                    Text(elapsed(run.snapshot.timing.wall_ms))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                    if let tools = run.snapshot.tools, !tools.isEmpty {
                        Text("\(tools.count) tools")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(.vertical, 4)
        .padding(.horizontal, 6)
        .background(isSelected ? Color.accentColor.opacity(0.06) : Color.clear, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
    }

    private func toggleRunSelection(_ id: String) {
        if selectedRunIDs.isEmpty {
            // When starting manual selection from an all-selected default, select all except this one
            var all = Set(filteredRuns.map(\.id))
            all.remove(id)
            selectedRunIDs = all
        } else if selectedRunIDs.contains(id) {
            selectedRunIDs.remove(id)
        } else {
            selectedRunIDs.insert(id)
        }
    }

    struct ToolDiagnosticSummary {
        var total: Int
        var errors: Int
        var elapsedMs: UInt64
    }

    struct RunAggregateMetrics {
        let totalRuns: Int
        let passedRuns: Int
        let totalCriteria: Int
        let passedCriteria: Int
        let scoreRate: Double
        let totalWallMs: UInt64
        let totalActiveMs: UInt64
        let totalApprovalWaitMs: UInt64
    }

    private func computeAggregateMetrics(runs: [EvaluationSnapshot.Run]) -> RunAggregateMetrics {
        let totalRuns = runs.count
        let passedRuns = runs.filter { $0.acceptance?.verified_success == true }.count
        let totalCriteria = runs.reduce(0) { $0 + ($1.acceptance?.results.count ?? 0) }
        let passedCriteria = runs.reduce(0) { sum, run in
            sum + (run.acceptance?.results.filter { $0.status.lowercased().contains("pass") }.count ?? 0)
        }
        let scoreRate = totalCriteria > 0 ? Double(passedCriteria) / Double(totalCriteria) : (totalRuns > 0 ? Double(passedRuns) / Double(totalRuns) : 1.0)
        let totalWallMs: UInt64 = runs.reduce(0) { $0 + ($1.snapshot.timing.wall_ms ?? 0) }
        let totalActiveMs: UInt64 = runs.reduce(0) { $0 + ($1.snapshot.timing.active_ms ?? 0) }
        let totalApprovalWaitMs: UInt64 = runs.reduce(0) { $0 + ($1.snapshot.timing.approval_wait_ms ?? 0) }
        return RunAggregateMetrics(
            totalRuns: totalRuns,
            passedRuns: passedRuns,
            totalCriteria: totalCriteria,
            passedCriteria: passedCriteria,
            scoreRate: scoreRate,
            totalWallMs: totalWallMs,
            totalActiveMs: totalActiveMs,
            totalApprovalWaitMs: totalApprovalWaitMs
        )
    }

    private func computeToolDiagnostics(runs: [EvaluationSnapshot.Run]) -> [String: ToolDiagnosticSummary] {
        var toolUsage = [String: ToolDiagnosticSummary]()
        for run in runs {
            for tool in run.snapshot.tools ?? [] {
                var current = toolUsage[tool.name] ?? ToolDiagnosticSummary(total: 0, errors: 0, elapsedMs: 0)
                current.total += 1
                if tool.outcome.lowercased().contains("error") {
                    current.errors += 1
                }
                current.elapsedMs += tool.elapsed_ms
                toolUsage[tool.name] = current
            }
        }
        return toolUsage
    }

    // MARK: - Scoring & Diagnostic Report Card

    @ViewBuilder
    private var scoringAndDiagnosticReport: some View {
        let runs = effectiveEvaluatedRuns
        let metrics = computeAggregateMetrics(runs: runs)
        let toolUsage = computeToolDiagnostics(runs: runs)

        cardContainer {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Evaluation Score & Diagnostics")
                            .font(.subheadline.weight(.semibold))
                        Text("Scored across \(metrics.totalRuns) selected turn(s)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    // Score Pill
                    VStack(alignment: .trailing, spacing: 1) {
                        Text(String(format: "%.0f%%", metrics.scoreRate * 100))
                            .font(.title2.weight(.bold))
                            .monospacedDigit()
                            .foregroundStyle(metrics.scoreRate >= 0.8 ? Color.green : (metrics.scoreRate >= 0.5 ? Color.orange : Color.red))
                        Text(metrics.scoreRate >= 0.8 ? "EXCELLENT" : (metrics.scoreRate >= 0.5 ? "MARGINAL" : "NEEDS REPAIR"))
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(metrics.scoreRate >= 0.8 ? Color.green : (metrics.scoreRate >= 0.5 ? Color.orange : Color.red))
                    }
                }

                Divider()

                // Key Performance Metrics
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                    GridRow {
                        metricItem(title: "Verified Runs", value: "\(metrics.passedRuns) / \(metrics.totalRuns)")
                        metricItem(title: "Criteria Passed", value: "\(metrics.passedCriteria) / \(metrics.totalCriteria)")
                    }
                    GridRow {
                        metricItem(title: "Total Wall Time", value: elapsed(metrics.totalWallMs))
                        metricItem(title: "Model Active Time", value: elapsed(metrics.totalActiveMs))
                    }
                    GridRow {
                        metricItem(title: "Approval Wait", value: elapsed(metrics.totalApprovalWaitMs))
                        metricItem(title: "Tools Executed", value: "\(toolUsage.values.reduce(0) { $0 + $1.total })")
                    }
                }

                Divider()

                // Tool Diagnostics Breakdown
                VStack(alignment: .leading, spacing: 6) {
                    Text("Tool Diagnostics")
                        .font(.footnote.weight(.semibold))
                    if toolUsage.isEmpty {
                        Text("No tools executed in selected runs.").font(.caption).foregroundStyle(.secondary)
                    } else {
                        ForEach(toolUsage.keys.sorted(), id: \.self) { toolName in
                            if let stat = toolUsage[toolName] {
                                let errorRate = stat.total > 0 ? Double(stat.errors) / Double(stat.total) : 0.0
                                HStack {
                                    Text(toolName)
                                        .font(.footnote.weight(.medium))
                                    Spacer()
                                    if stat.errors > 0 {
                                        Text("\(stat.errors) err (\(String(format: "%.0f%%", errorRate * 100)))")
                                            .font(.caption.weight(.medium))
                                            .foregroundStyle(errorRate > 0.2 ? Color.red : Color.orange)
                                    } else {
                                        Text("100% clean")
                                            .font(.caption)
                                            .foregroundStyle(.green)
                                    }
                                    Text("· \(elapsed(stat.elapsedMs))")
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                        .monospacedDigit()
                                }
                            }
                        }
                    }
                }

                Divider()

                // Diagnostic Conclusion & Next Step
                VStack(alignment: .leading, spacing: 6) {
                    Text("Optimization Diagnostic")
                        .font(.footnote.weight(.semibold))

                    let errorTools = toolUsage.filter { $0.value.errors > 0 }
                    if !errorTools.isEmpty {
                        Text("Observed errors on tool(s): \(errorTools.keys.joined(separator: ", ")). Generating a candidate policy can bind these tools to dedicated models or add failure recovery.")
                            .font(.caption)
                            .foregroundStyle(.primary)
                    } else {
                        Text("Execution clean with no tool errors. Candidate generation can explore cost-saving fast models for downstream steps.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Button {
                        selectedTab = .generate
                        Task { await model.proposePolicy() }
                    } label: {
                        HStack {
                            Text("Generate Policy Based on Findings")
                            Image(systemName: "arrow.right")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.purple)
                    .controlSize(.small)
                    .padding(.top, 4)
                }
            }
        }
    }

    private func metricItem(title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.footnote.weight(.semibold)).monospacedDigit()
        }
    }

    // MARK: - SECTION 2: GENERATE POLICY

    @ViewBuilder
    private var generatePolicySection: some View {
        cardContainer {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Policy Generation Engine")
                            .font(.subheadline.weight(.semibold))
                        Text("Derive bounded candidate routing policies from evaluation evidence.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button {
                        Task { await model.proposePolicy() }
                    } label: {
                        Label("Propose Candidate", systemImage: "sparkles")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.purple)
                    .controlSize(.small)
                    .disabled(model.isRequesting)
                }
            }
        }

        if let candidate = pendingProposal {
            cardContainer {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Proposed Candidate Policy")
                                .font(.subheadline.weight(.semibold))
                            Text("\(candidate.policy_id) v\(candidate.version)")
                                .font(.title3.weight(.bold))
                                .monospacedDigit()
                        }
                        Spacer()
                        statusBadge(candidate.status)
                    }

                    if let prop = candidate.proposal {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Rationale & Evidence Basis")
                                .font(.footnote.weight(.semibold))
                            Text(prop.rationale)
                                .font(.callout)
                                .foregroundStyle(.primary)

                            HStack(spacing: 8) {
                                Text("Metric: \(humanReadableMetric(prop.evidence.metric))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text("Observed: \(prop.evidence.observed_value)")
                                    .font(.caption.weight(.medium))
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
                    }

                    Divider()

                    // Parameter Changes (Delta View)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Policy Configuration Delta").font(.footnote.weight(.semibold))
                        LabeledContent("Default model", value: candidate.policy.default_model)
                        if let err = candidate.policy.after_tool_error {
                            LabeledContent("After tool error", value: err)
                        }
                        if let succ = candidate.policy.after_tool_success {
                            LabeledContent("After tool success", value: succ)
                        }
                        if let rec = candidate.policy.recovery_model {
                            LabeledContent("Recovery model", value: rec)
                        }
                        if let steps = candidate.policy.recovery_after_no_progress_steps {
                            LabeledContent("Recovery threshold", value: "\(steps) steps").monospacedDigit()
                        }
                        if let routes = candidate.policy.tool_routes, !routes.isEmpty {
                            Text("Explicit tool routes:").font(.footnote.weight(.medium))
                            ForEach(routes) { r in
                                Text("  • \(r.id): -> \(r.model)").font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                    }

                    Divider()

                    HStack(spacing: 10) {
                        Button {
                            selectedTab = .update
                        } label: {
                            HStack {
                                Text("Review & Update Policy")
                                Image(systemName: "arrow.right")
                            }
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.accentColor)
                        .controlSize(.small)

                        Button {
                            Task {
                                await model.quickApplyPolicy(policyId: candidate.policy_id, version: candidate.version)
                            }
                        } label: {
                            Label("Quick Apply to New Runs", systemImage: "bolt.fill")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .disabled(model.isRequesting)
                    }
                    .padding(.top, 2)
                }
            }
        } else {
            cardContainer {
                VStack(alignment: .leading, spacing: 6) {
                    Text("No Candidate Proposal Yet").font(.subheadline.weight(.semibold))
                    Text("Click 'Propose Candidate' above or complete an evaluation run to generate an evidence-backed routing proposal.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 8)
            }
        }
    }

    // MARK: - SECTION 3: UPDATE POLICY

    @ViewBuilder
    private var updatePolicySection: some View {
        let policies = model.snapshot?.policies ?? []

        // Active Policy Card
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

        // Policy Version History & Actions
        HStack {
            Text("Policy Version History (\(policies.count))")
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
            Spacer()
        }

        if policies.isEmpty {
            cardContainer {
                Text("No policies registered in the SQLite store.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 8)
            }
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
                                }
                            }

                            if !p.reason.isEmpty {
                                Text("Audit reason: \(p.reason)").font(.caption).foregroundStyle(.secondary)
                            }

                            // Actions
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
        value.map { String(format: "%.2f s", Double($0) / 1000) } ?? "0.00 s"
    }
}
