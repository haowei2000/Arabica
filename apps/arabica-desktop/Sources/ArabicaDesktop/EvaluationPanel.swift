import AppKit
import SwiftUI

enum BlendInspectorTab: String, CaseIterable, Identifiable {
    case evaluate = "Evaluate"
    case generate = "Generate"
    case update = "Policies"

    var id: String { rawValue }
}

enum EvaluationRunSelection: Equatable {
    case all
    case custom(Set<String>)

    func isSelected(_ id: String) -> Bool {
        switch self {
        case .all: return true
        case .custom(let ids): return ids.contains(id)
        }
    }

    mutating func selectAll() {
        self = .all
    }

    mutating func clear() {
        self = .custom([])
    }

    mutating func toggle(_ id: String, among allIDs: [String]) {
        switch self {
        case .all:
            var remaining = Set(allIDs)
            remaining.remove(id)
            self = .custom(remaining)
        case .custom(var ids):
            if ids.contains(id) {
                ids.remove(id)
            } else {
                ids.insert(id)
            }
            self = .custom(ids)
        }
    }

    func selectedRuns(from runs: [EvaluationSnapshot.Run]) -> [EvaluationSnapshot.Run] {
        switch self {
        case .all: return runs
        case .custom(let ids): return runs.filter { ids.contains($0.id) }
        }
    }
}

struct EvaluationPanel: View {
    @ObservedObject var model: EvaluationModel
    @ObservedObject private var theme = ThemeManager.shared
    var isVisible: Bool
    @State private var selectedTab: BlendInspectorTab = .evaluate
    @State private var selectedPolicyFilter: String = "all"
    @State private var runSelection: EvaluationRunSelection = .all
    @State private var confirmingRollbackPolicy: EvaluationSnapshot.PolicyRecord?
    @State private var confirmingAction: (action: String, policy: EvaluationSnapshot.PolicyRecord)?
    @State private var copiedSessionID = false

    var body: some View {
        VStack(spacing: 0) {
            stickyHeader

            Rectangle()
                .fill(Palette.rule)
                .frame(height: 1)

            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if !model.supported {
                        ContentUnavailableView(
                            "Blend Unavailable",
                            systemImage: "slider.horizontal.2.square",
                            description: Text("Connect with an Arabica agent that supports background evaluation and dynamic routing.")
                        )
                        .padding(.vertical, 32)
                    } else if model.sessionID == nil {
                        ContentUnavailableView(
                            "No Conversation Selected",
                            systemImage: "bubble.left.and.bubble.right",
                            description: Text("Select a conversation from the sidebar to inspect its Blend routing, runs, and policy lifecycle.")
                        )
                        .padding(.vertical, 32)
                    } else {
                        if let message = model.message {
                            banner(
                                icon: "info.circle.fill",
                                iconColor: Palette.accent,
                                text: message
                            )
                        }

                        if let error = model.error {
                            VStack(alignment: .leading, spacing: 8) {
                                HStack(alignment: .top, spacing: 7) {
                                    Image(systemName: "exclamationmark.triangle.fill")
                                        .font(AppFont.sans(12))
                                        .foregroundStyle(.orange)
                                        .accessibilityLabel("Error")
                                    Text(error)
                                        .font(AppFont.sans(11.5))
                                        .foregroundStyle(Palette.ink)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                Button("Retry Query") { Task { await model.load() } }
                                    .buttonStyle(.bordered)
                                    .controlSize(.small)
                                    .disabled(model.isRequesting)
                            }
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                            .overlay(
                                RoundedRectangle(cornerRadius: 8, style: .continuous)
                                    .stroke(Color.orange.opacity(0.28), lineWidth: 1)
                            )
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
                            HStack(spacing: 4) {
                                Image(systemName: "clock")
                                    .font(AppFont.sans(9.5))
                                Text("Updated \(date.formatted(date: .omitted, time: .standard))")
                                    .font(AppFont.sans(10.5))
                                    .monospacedDigit()
                            }
                            .foregroundStyle(Palette.subtle)
                            .padding(.top, 2)
                        }
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
            }
        }
        .background(Palette.sidebar)
        .task(id: "\(isVisible):\(model.sessionID ?? "")") {
            guard isVisible, model.supported, model.sessionID != nil else { return }
            await model.poll()
        }
        .onChange(of: model.sessionID) { _, _ in
            runSelection.selectAll()
            selectedPolicyFilter = "all"
            copiedSessionID = false
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
            Button("\(actionLabel(item.action)) \(item.policy.policy_id) v\(item.policy.version)") {
                Task {
                    switch item.action {
                    case "approve":
                        await model.approvePolicy(policyId: item.policy.policy_id, version: item.policy.version)
                    case "reject":
                        await model.rejectPolicy(policyId: item.policy.policy_id, version: item.policy.version)
                    case "activate":
                        await model.activatePolicy(policyId: item.policy.policy_id, version: item.policy.version)
                    case "quick_apply":
                        await model.quickApplyPolicy(policyId: item.policy.policy_id, version: item.policy.version)
                    default:
                        break
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: { item in
            if item.action == "quick_apply" {
                Text("This will approve and immediately activate policy version \(item.policy.version) for new sessions.")
            } else {
                Text("Are you sure you want to \(item.action) policy version \(item.policy.version)?")
            }
        }
    }

    // MARK: - Sticky Header & Tabs

    private var stickyHeader: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 8) {
                Label {
                    Text("Blend Inspector")
                        .font(AppFont.sans(13.5, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                } icon: {
                    Image(systemName: "slider.horizontal.2.square")
                        .font(AppFont.sans(13))
                        .foregroundStyle(Palette.accent)
                }

                Spacer(minLength: 4)

                if let active = activePolicy {
                    statusBadge("v\(active.version)", tint: .green)
                        .help("Active policy: \(active.policy_id) v\(active.version)")
                }

                if model.supported, model.sessionID != nil {
                    Button {
                        Task { await model.load(refresh: true) }
                    } label: {
                        Group {
                            if model.isRequesting {
                                ProgressView().controlSize(.mini)
                            } else {
                                Image(systemName: "arrow.clockwise")
                                    .font(AppFont.sans(11.5, weight: .medium))
                            }
                        }
                        .frame(width: 24, height: 24)
                        .background(Palette.raised, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 6, style: .continuous)
                                .stroke(Palette.rule, lineWidth: 1)
                        )
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Palette.muted)
                    .disabled(model.isRequesting)
                    .help("Refresh evaluation report and policies")
                    .accessibilityLabel("Refresh Blend Inspector")
                }
            }

            if model.supported, let sessionID = model.sessionID {
                HStack(spacing: 6) {
                    Text("Session")
                        .font(AppFont.sans(10.5, weight: .medium))
                        .foregroundStyle(Palette.muted)
                    Text(sessionID)
                        .font(AppFont.code(10.5))
                        .foregroundStyle(Palette.ink)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 4)
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(sessionID, forType: .string)
                        copiedSessionID = true
                        Task {
                            try? await Task.sleep(for: .seconds(1.5))
                            copiedSessionID = false
                        }
                    } label: {
                        Image(systemName: copiedSessionID ? "checkmark" : "doc.on.doc")
                            .font(AppFont.sans(10))
                            .foregroundStyle(copiedSessionID ? Color.green : Palette.muted)
                            .frame(width: 18, height: 18)
                    }
                    .buttonStyle(.plain)
                    .help("Copy session ID")
                    .accessibilityLabel(copiedSessionID ? "Session ID copied" : "Copy session ID")
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
                .background(Palette.raised, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .stroke(Palette.rule, lineWidth: 1)
                )
                .help(sessionID)

                tabPicker
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Palette.sidebar)
    }

    private var tabPicker: some View {
        HStack(spacing: 4) {
            ForEach(BlendInspectorTab.allCases) { tab in
                let isSelected = selectedTab == tab
                Button {
                    withAnimation(.easeInOut(duration: 0.14)) {
                        selectedTab = tab
                    }
                } label: {
                    HStack(spacing: 4) {
                        Text(tab.rawValue)
                            .font(AppFont.sans(11.5, weight: isSelected ? .semibold : .medium))
                            .lineLimit(1)
                        if tab == .generate, pendingProposal != nil {
                            Circle()
                                .fill(Color.purple)
                                .frame(width: 5, height: 5)
                        }
                    }
                    .foregroundStyle(isSelected ? Palette.ink : Palette.muted)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 5)
                    .background(
                        isSelected ? Palette.card : Color.clear,
                        in: RoundedRectangle(cornerRadius: 6, style: .continuous)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .stroke(isSelected ? Palette.rule : Color.clear, lineWidth: 1)
                    )
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(isSelected ? .isSelected : [])
            }
        }
        .padding(3)
        .background(Palette.raised, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Palette.rule, lineWidth: 1)
        )
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
        let allRuns = model.snapshot?.report?.runs ?? []
        let availablePolicies = extractAvailablePolicies(from: allRuns)

        // 1. Score & Diagnostic Report first for immediate scannability
        if !effectiveEvaluatedRuns.isEmpty {
            scoringAndDiagnosticReport
        } else {
            cardContainer {
                HStack(spacing: 8) {
                    Image(systemName: "chart.bar.xaxis")
                        .font(AppFont.sans(14))
                        .foregroundStyle(Palette.muted)
                    Text(
                        filteredRuns.isEmpty
                            ? "No runs recorded for this filter yet."
                            : "Select one or more runs below to view the score and diagnostics."
                    )
                    .font(AppFont.sans(11.5))
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.vertical, 4)
            }
        }

        // 2. Policy Filter & Run Selection
        cardContainer {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Text("Runs")
                        .font(AppFont.sans(12, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                    Text("\(effectiveEvaluatedRuns.count)/\(filteredRuns.count)")
                        .font(AppFont.code(10.5))
                        .foregroundStyle(Palette.muted)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(Palette.raised, in: Capsule())

                    Spacer(minLength: 4)

                    if !availablePolicies.isEmpty {
                        Picker("Policy Filter", selection: $selectedPolicyFilter) {
                            Text("All Policies").tag("all")
                            ForEach(availablePolicies, id: \.self) { policy in
                                Text(policy).tag(policy)
                            }
                        }
                        .pickerStyle(.menu)
                        .controlSize(.small)
                        .labelsHidden()
                        .frame(maxWidth: 140)
                    }
                }

                if !filteredRuns.isEmpty {
                    HStack(spacing: 8) {
                        Button("Select All") {
                            runSelection.selectAll()
                        }
                        .buttonStyle(.plain)
                        .font(AppFont.sans(11, weight: .medium))
                        .foregroundStyle(Palette.accent)

                        Text("·").foregroundStyle(Palette.subtle)

                        Button("Clear") {
                            runSelection.clear()
                        }
                        .buttonStyle(.plain)
                        .font(AppFont.sans(11, weight: .medium))
                        .foregroundStyle(Palette.muted)

                        Spacer()
                    }
                }

                if filteredRuns.isEmpty {
                    Text("No runs matching the selected policy.")
                        .font(AppFont.sans(11.5))
                        .foregroundStyle(Palette.muted)
                        .padding(.vertical, 4)
                } else {
                    VStack(spacing: 4) {
                        ForEach(filteredRuns) { run in
                            runSelectionRow(run)
                        }
                    }
                }
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
            guard let fps = run.snapshot.policy_fingerprints, !fps.isEmpty else {
                return false
            }
            return fps.contains(where: { $0.hasPrefix(selectedPolicyFilter) || selectedPolicyFilter.contains($0.prefix(8)) })
        }
    }

    private var effectiveEvaluatedRuns: [EvaluationSnapshot.Run] {
        runSelection.selectedRuns(from: filteredRuns)
    }

    @ViewBuilder
    private func runSelectionRow(_ run: EvaluationSnapshot.Run) -> some View {
        let isSelected = runSelection.isSelected(run.id)
        Button {
            toggleRunSelection(run.id)
        } label: {
            HStack(spacing: 8) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .font(AppFont.sans(13))
                    .foregroundStyle(isSelected ? Palette.accent : Palette.subtle)

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 5) {
                        Text(String(run.id.prefix(8)))
                            .font(AppFont.code(11.5, weight: .medium))
                            .foregroundStyle(Palette.ink)
                        Text(run.snapshot.terminal_status.capitalized)
                            .font(AppFont.sans(11))
                            .foregroundStyle(Palette.muted)
                            .lineLimit(1)
                        Spacer(minLength: 4)
                        if let verified = run.acceptance?.verified_success {
                            statusBadge(verified ? "PASS" : "FAIL", tint: verified ? .green : .red)
                        }
                    }
                    HStack(spacing: 8) {
                        Text(elapsed(run.snapshot.timing.wall_ms))
                            .font(AppFont.code(10))
                            .foregroundStyle(Palette.muted)
                        if let tools = run.snapshot.tools, !tools.isEmpty {
                            let failed = tools.filter(\.failed).count
                            Text("\(tools.count) \(tools.count == 1 ? "tool" : "tools")\(failed > 0 ? " · \(failed) err" : "")")
                                .font(AppFont.sans(10))
                                .foregroundStyle(failed > 0 ? Color.orange : Palette.muted)
                        }
                    }
                }
            }
            .padding(.vertical, 6)
            .padding(.horizontal, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                isSelected ? Palette.selectionBg : Palette.raised.opacity(0.5),
                in: RoundedRectangle(cornerRadius: 7, style: .continuous)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .stroke(isSelected ? Palette.accent.opacity(0.35) : Color.clear, lineWidth: 1)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func toggleRunSelection(_ id: String) {
        runSelection.toggle(id, among: filteredRuns.map(\.id))
    }

    struct ToolDiagnosticSummary {
        var total: Int
        var errors: Int
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
                var current = toolUsage[tool.name] ?? ToolDiagnosticSummary(total: 0, errors: 0)
                current.total += 1
                if tool.failed {
                    current.errors += 1
                }
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
        let scoreColor: Color = metrics.scoreRate >= 0.8 ? .green : (metrics.scoreRate >= 0.5 ? .orange : .red)
        let scoreLabel = metrics.scoreRate >= 0.8 ? "EXCELLENT" : (metrics.scoreRate >= 0.5 ? "MARGINAL" : "NEEDS REPAIR")

        cardContainer {
            VStack(alignment: .leading, spacing: 12) {
                // Hero score row with compact ring
                HStack(spacing: 12) {
                    ZStack {
                        Circle()
                            .stroke(Palette.rule, lineWidth: 4.5)
                        Circle()
                            .trim(from: 0, to: CGFloat(max(0, min(1, metrics.scoreRate))))
                            .stroke(scoreColor, style: StrokeStyle(lineWidth: 4.5, lineCap: .round))
                            .rotationEffect(.degrees(-90))
                        Text(String(format: "%.0f%%", metrics.scoreRate * 100))
                            .font(AppFont.code(11.5, weight: .bold))
                            .foregroundStyle(Palette.ink)
                    }
                    .frame(width: 46, height: 46)

                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text("Evaluation Score")
                                .font(AppFont.sans(12.5, weight: .semibold))
                                .foregroundStyle(Palette.ink)
                            statusBadge(scoreLabel, tint: scoreColor)
                        }
                        Text("Aggregated across \(metrics.totalRuns) \(metrics.totalRuns == 1 ? "turn" : "turns")")
                            .font(AppFont.sans(11))
                            .foregroundStyle(Palette.muted)
                    }
                    Spacer(minLength: 0)
                }

                // 2x3 Metric Tiles
                LazyVGrid(
                    columns: [GridItem(.flexible(), spacing: 6), GridItem(.flexible(), spacing: 6)],
                    spacing: 6
                ) {
                    metricTile(title: "Verified Runs", value: "\(metrics.passedRuns)/\(metrics.totalRuns)")
                    metricTile(title: "Criteria Passed", value: "\(metrics.passedCriteria)/\(metrics.totalCriteria)")
                    metricTile(title: "Wall Time", value: elapsed(metrics.totalWallMs))
                    metricTile(title: "Model Active", value: elapsed(metrics.totalActiveMs))
                    metricTile(title: "Approval Wait", value: elapsed(metrics.totalApprovalWaitMs))
                    metricTile(title: "Tool Calls", value: "\(toolUsage.values.reduce(0) { $0 + $1.total })")
                }

                Divider().overlay(Palette.rule)

                // Tool Diagnostics Breakdown
                VStack(alignment: .leading, spacing: 7) {
                    Text("Tool Diagnostics")
                        .font(AppFont.sans(11.5, weight: .semibold))
                        .foregroundStyle(Palette.ink)

                    if toolUsage.isEmpty {
                        Text("No tools executed in selected runs.")
                            .font(AppFont.sans(11))
                            .foregroundStyle(Palette.muted)
                    } else {
                        let maxCalls = max(1, toolUsage.values.map(\.total).max() ?? 1)
                        ForEach(toolUsage.keys.sorted(), id: \.self) { toolName in
                            if let stat = toolUsage[toolName] {
                                let errorRate = stat.total > 0 ? Double(stat.errors) / Double(stat.total) : 0.0
                                let barRatio = Double(stat.total) / Double(maxCalls)
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack(spacing: 6) {
                                        Text(toolName)
                                            .font(AppFont.code(11, weight: .medium))
                                            .foregroundStyle(Palette.ink)
                                            .lineLimit(1)
                                            .truncationMode(.middle)
                                        Spacer(minLength: 4)
                                        if stat.errors > 0 {
                                            Text("\(stat.errors) err (\(String(format: "%.0f%%", errorRate * 100)))")
                                                .font(AppFont.sans(10.5, weight: .medium))
                                                .foregroundStyle(errorRate > 0.2 ? Color.red : Color.orange)
                                        } else {
                                            Text("Clean")
                                                .font(AppFont.sans(10.5, weight: .medium))
                                                .foregroundStyle(.green)
                                        }
                                        Text("· \(stat.total)")
                                            .font(AppFont.code(10.5))
                                            .foregroundStyle(Palette.muted)
                                    }
                                    GeometryReader { geo in
                                        ZStack(alignment: .leading) {
                                            Capsule().fill(Palette.raised)
                                            Capsule()
                                                .fill(stat.errors > 0 ? (errorRate > 0.2 ? Color.red.opacity(0.75) : Color.orange.opacity(0.75)) : Color.green.opacity(0.65))
                                                .frame(width: max(4, geo.size.width * barRatio))
                                        }
                                    }
                                    .frame(height: 3)
                                }
                            }
                        }
                    }
                }

                Divider().overlay(Palette.rule)

                // Diagnostic Conclusion & Next Step
                VStack(alignment: .leading, spacing: 8) {
                    Text("Optimization Insight")
                        .font(AppFont.sans(11.5, weight: .semibold))
                        .foregroundStyle(Palette.ink)

                    let errorTools = toolUsage.filter { $0.value.errors > 0 }
                    Text(
                        !errorTools.isEmpty
                            ? "Observed errors on \(errorTools.keys.sorted().joined(separator: ", ")). Proposing a candidate policy can bind these tools to stronger models or add failure recovery."
                            : "Execution is clean with no tool errors. Candidate generation can explore faster, lower-cost models for routine steps."
                    )
                    .font(AppFont.sans(11.5))
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)

                    Button {
                        selectedTab = .generate
                        Task { await model.proposePolicy() }
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "sparkles")
                            Text("Generate Policy from Findings")
                            Spacer()
                            Image(systemName: "arrow.right")
                        }
                        .font(AppFont.sans(11.5, weight: .semibold))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.purple)
                    .controlSize(.small)
                    .disabled(model.isRequesting)
                }
            }
        }
    }

    private func metricTile(title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(AppFont.sans(10))
                .foregroundStyle(Palette.muted)
                .lineLimit(1)
            Text(value)
                .font(AppFont.code(12, weight: .semibold))
                .foregroundStyle(Palette.ink)
                .lineLimit(1)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.raised, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
    }

    // MARK: - SECTION 2: GENERATE POLICY

    @ViewBuilder
    private var generatePolicySection: some View {
        cardContainer {
            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Policy Generation Engine")
                        .font(AppFont.sans(12.5, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                    Text("Derive bounded candidate routing policies from recent evaluation evidence.")
                        .font(AppFont.sans(11.5))
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Button {
                    Task { await model.proposePolicy() }
                } label: {
                    HStack(spacing: 6) {
                        if model.isRequesting {
                            ProgressView().controlSize(.mini)
                        } else {
                            Image(systemName: "sparkles")
                        }
                        Text(model.isRequesting ? "Proposing Candidate…" : "Propose Candidate")
                    }
                    .font(AppFont.sans(11.5, weight: .semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 2)
                }
                .buttonStyle(.borderedProminent)
                .tint(.purple)
                .controlSize(.small)
                .disabled(model.isRequesting)
            }
        }

        if let candidate = pendingProposal {
            cardContainer {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Proposed Candidate")
                                .font(AppFont.sans(10.5, weight: .medium))
                                .foregroundStyle(Palette.muted)
                            Text("\(candidate.policy_id) v\(candidate.version)")
                                .font(AppFont.sans(14, weight: .bold))
                                .foregroundStyle(Palette.ink)
                                .monospacedDigit()
                        }
                        Spacer()
                        statusBadge(candidate.status)
                    }

                    if let prop = candidate.proposal {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Rationale & Evidence")
                                .font(AppFont.sans(11, weight: .semibold))
                                .foregroundStyle(Palette.ink)
                            Text(prop.rationale)
                                .font(AppFont.sans(11.5))
                                .foregroundStyle(Palette.ink)
                                .fixedSize(horizontal: false, vertical: true)

                            VStack(alignment: .leading, spacing: 4) {
                                policyParamRow("Metric", value: humanReadableMetric(prop.evidence.metric))
                                policyParamRow("Observed", value: prop.evidence.observed_value, isCode: true)
                                if let tool = prop.evidence.tool_name {
                                    policyParamRow("Tool", value: tool, isCode: true)
                                }
                            }
                            .padding(.top, 2)
                        }
                        .padding(9)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Palette.raised, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
                    }

                    Divider().overlay(Palette.rule)

                    VStack(alignment: .leading, spacing: 6) {
                        Text("Policy Configuration")
                            .font(AppFont.sans(11.5, weight: .semibold))
                            .foregroundStyle(Palette.ink)
                        policyDetailsList(candidate.policy)
                    }

                    Divider().overlay(Palette.rule)

                    VStack(spacing: 6) {
                        Button {
                            confirmingAction = (action: "quick_apply", policy: candidate)
                        } label: {
                            Label("Quick Apply to New Runs", systemImage: "bolt.fill")
                                .font(AppFont.sans(11.5, weight: .semibold))
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.purple)
                        .controlSize(.small)
                        .disabled(model.isRequesting)

                        Button {
                            selectedTab = .update
                        } label: {
                            HStack(spacing: 4) {
                                Text("Review in Policies")
                                Image(systemName: "arrow.right")
                            }
                            .font(AppFont.sans(11.5, weight: .medium))
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    }
                }
            }
        } else {
            cardContainer {
                VStack(alignment: .leading, spacing: 5) {
                    Text("No Candidate Proposal Yet")
                        .font(AppFont.sans(12, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                    Text("Click Propose Candidate above or complete an evaluation run to generate an evidence-backed routing proposal.")
                        .font(AppFont.sans(11.5))
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.vertical, 4)
            }
        }
    }

    // MARK: - SECTION 3: POLICIES (UPDATE)

    @ViewBuilder
    private var updatePolicySection: some View {
        let policies = model.snapshot?.policies ?? []

        // Active Policy Card
        if let active = activePolicy {
            cardContainer(accentBorder: Color.green.opacity(0.35)) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Active Blend Policy")
                                .font(AppFont.sans(10.5, weight: .medium))
                                .foregroundStyle(Palette.muted)
                            Text("\(active.policy_id) v\(active.version)")
                                .font(AppFont.sans(14, weight: .bold))
                                .monospacedDigit()
                                .foregroundStyle(Palette.ink)
                        }
                        Spacer()
                        statusBadge("ACTIVE")
                    }

                    policyDetailsList(active.policy, activatedAt: active.activated_at)

                    let previousVersions = policies.filter {
                        $0.version < active.version && ($0.status == "retired" || $0.status == "approved")
                    }
                    if let mostRecentPrev = previousVersions.first {
                        Button {
                            confirmingRollbackPolicy = mostRecentPrev
                        } label: {
                            Label("Rollback to v\(mostRecentPrev.version)", systemImage: "arrow.uturn.backward")
                                .font(AppFont.sans(11.5, weight: .medium))
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .disabled(model.isRequesting)
                        .padding(.top, 2)
                    }
                }
            }
        }

        // Policy Version History & Actions
        HStack {
            Text("Version History")
                .font(AppFont.sans(12, weight: .semibold))
                .foregroundStyle(Palette.ink)
            Text("\(policies.count)")
                .font(AppFont.code(10.5))
                .foregroundStyle(Palette.muted)
                .padding(.horizontal, 5)
                .padding(.vertical, 1)
                .background(Palette.raised, in: Capsule())
            Spacer()
        }
        .padding(.top, 2)

        if policies.isEmpty {
            cardContainer {
                Text("No policies registered in the SQLite store.")
                    .font(AppFont.sans(11.5))
                    .foregroundStyle(Palette.muted)
                    .padding(.vertical, 4)
            }
        } else {
            ForEach(policies) { p in
                cardContainer {
                    DisclosureGroup {
                        VStack(alignment: .leading, spacing: 8) {
                            policyDetailsList(p.policy)

                            if let prop = p.proposal {
                                Divider().overlay(Palette.rule)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("Proposal Rationale")
                                        .font(AppFont.sans(11, weight: .semibold))
                                        .foregroundStyle(Palette.ink)
                                    Text(prop.rationale)
                                        .font(AppFont.sans(11.5))
                                        .foregroundStyle(Palette.ink)
                                        .fixedSize(horizontal: false, vertical: true)
                                    Text("\(humanReadableMetric(prop.evidence.metric)): \(prop.evidence.observed_value) (threshold \(prop.evidence.threshold))")
                                        .font(AppFont.code(10.5))
                                        .foregroundStyle(Palette.muted)
                                }
                            }

                            if !p.reason.isEmpty {
                                Text("Audit: \(p.reason)")
                                    .font(AppFont.sans(10.5))
                                    .foregroundStyle(Palette.muted)
                            }

                            // Actions
                            if p.status == "candidate" || p.status == "evaluated" {
                                VStack(spacing: 6) {
                                    Button("Quick Apply") {
                                        confirmingAction = (action: "quick_apply", policy: p)
                                    }
                                    .buttonStyle(.borderedProminent)
                                    .tint(.purple)
                                    .controlSize(.small)
                                    .frame(maxWidth: .infinity)
                                    .disabled(model.isRequesting)

                                    HStack(spacing: 6) {
                                        Button("Approve") {
                                            confirmingAction = (action: "approve", policy: p)
                                        }
                                        .buttonStyle(.bordered)
                                        .tint(.orange)
                                        .controlSize(.small)
                                        .frame(maxWidth: .infinity)
                                        .disabled(model.isRequesting)

                                        Button("Reject") {
                                            confirmingAction = (action: "reject", policy: p)
                                        }
                                        .buttonStyle(.bordered)
                                        .tint(.red)
                                        .controlSize(.small)
                                        .frame(maxWidth: .infinity)
                                        .disabled(model.isRequesting)
                                    }
                                }
                                .padding(.top, 4)
                            } else if p.status == "approved" {
                                Button("Activate for New Runs") {
                                    confirmingAction = (action: "activate", policy: p)
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(.green)
                                .controlSize(.small)
                                .frame(maxWidth: .infinity)
                                .disabled(model.isRequesting)
                                .padding(.top, 4)
                            } else if p.status == "retired" {
                                Button("Reactivate (Rollback)") {
                                    confirmingRollbackPolicy = p
                                }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                                .frame(maxWidth: .infinity)
                                .disabled(model.isRequesting)
                                .padding(.top, 4)
                            }
                        }
                        .padding(.top, 8)
                    } label: {
                        HStack(spacing: 8) {
                            Text("\(p.policy_id) v\(p.version)")
                                .font(AppFont.sans(12, weight: .semibold))
                                .foregroundStyle(Palette.ink)
                                .monospacedDigit()
                                .lineLimit(1)
                            Spacer(minLength: 4)
                            statusBadge(p.status)
                        }
                    }
                    .tint(Palette.muted)
                }
            }
        }
    }

    // MARK: - Reusable Components

    @ViewBuilder
    private func policyDetailsList(_ policy: EvaluationSnapshot.PolicyRecord.PolicyDetails, activatedAt: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            policyParamRow("Default model", value: policy.default_model, isCode: true)
            if let err = policy.after_tool_error {
                policyParamRow("After error", value: err, isCode: true)
            }
            if let succ = policy.after_tool_success {
                policyParamRow("After success", value: succ, isCode: true)
            }
            if let rec = policy.recovery_model {
                policyParamRow("Recovery", value: rec, isCode: true)
            }
            if let steps = policy.recovery_after_no_progress_steps {
                policyParamRow("Recovery after", value: "\(steps) steps", isCode: true)
            }
            if let dwell = policy.minimum_model_dwell_steps {
                policyParamRow("Min dwell", value: "\(dwell) steps", isCode: true)
            }
            if let activatedAt {
                policyParamRow("Activated", value: activatedAt, isCode: true)
            }
            if let routes = policy.tool_routes, !routes.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Tool routes")
                        .font(AppFont.sans(10.5, weight: .medium))
                        .foregroundStyle(Palette.muted)
                        .padding(.top, 2)
                    ForEach(routes) { r in
                        HStack(spacing: 4) {
                            Text(r.id)
                                .font(AppFont.code(10.5, weight: .medium))
                                .foregroundStyle(Palette.ink)
                            Image(systemName: "arrow.right")
                                .font(AppFont.sans(8.5))
                                .foregroundStyle(Palette.subtle)
                            Text(r.model)
                                .font(AppFont.code(10.5))
                                .foregroundStyle(Palette.muted)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer(minLength: 0)
                        }
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(Palette.raised, in: RoundedRectangle(cornerRadius: 5, style: .continuous))
                    }
                }
            }
        }
    }

    private func policyParamRow(_ label: String, value: String, isCode: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label)
                .font(AppFont.sans(11))
                .foregroundStyle(Palette.muted)
                .lineLimit(1)
            Spacer(minLength: 4)
            Text(value)
                .font(isCode ? AppFont.code(11) : AppFont.sans(11, weight: .medium))
                .foregroundStyle(Palette.ink)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }

    private func banner(icon: String, iconColor: Color, text: String) -> some View {
        HStack(alignment: .top, spacing: 7) {
            Image(systemName: icon)
                .font(AppFont.sans(12))
                .foregroundStyle(iconColor)
            Text(text)
                .font(AppFont.sans(11.5))
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.raised, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Palette.rule, lineWidth: 1)
        )
    }

    @ViewBuilder
    private func cardContainer<Content: View>(
        accentBorder: Color? = nil,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            content()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.card, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(accentBorder ?? Palette.rule, lineWidth: 1)
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

    private func statusBadge(_ status: String, tint: Color? = nil) -> some View {
        let color: Color = tint ?? {
            switch status.lowercased() {
            case "active": return .green
            case "approved": return .orange
            case "evaluated": return .purple
            case "candidate": return .blue
            case "rejected": return .red
            default: return Palette.muted
            }
        }()
        return Text(status.uppercased())
            .font(AppFont.sans(9.5, weight: .bold))
            .monospacedDigit()
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.14), in: Capsule())
            .foregroundStyle(color)
    }

    private func actionLabel(_ action: String) -> String {
        action == "quick_apply" ? "Quick Apply" : action.capitalized
    }

    private func elapsed(_ value: UInt64?) -> String {
        value.map { String(format: "%.2f s", Double($0) / 1000) } ?? "0.00 s"
    }
}
