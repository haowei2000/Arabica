import SwiftUI

struct NativePolicyCandidatesView: View {
    @ObservedObject var evaluation: EvaluationModel
    @Environment(\.dismiss) private var dismiss
    @State private var selection: String?

    private var records: [EvaluationSnapshot.PolicyRecord] { evaluation.snapshot?.policies ?? [] }
    private var selected: EvaluationSnapshot.PolicyRecord? {
        records.first { $0.id == selection } ?? records.first
    }

    var body: some View {
        NavigationSplitView {
            List(selection: Binding(get: { selected?.id }, set: { selection = $0 })) {
                ForEach(records) { record in
                    VStack(alignment: .leading, spacing: 3) {
                        Text("\(record.policy_id) · v\(record.version)")
                        Text(record.status.capitalized).font(.caption).foregroundStyle(.secondary)
                    }
                    .tag(record.id)
                }
            }
            .navigationTitle("Blend Policy Candidates")
            .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 230)
            .accessibilityLabel("Blend policy candidates")
        } detail: {
            Group {
                if let selected {
                    details(selected)
                } else {
                    ContentUnavailableView(
                        "No Candidates", systemImage: "arrow.triangle.branch",
                        description: Text("Candidates appear here after Blend tuning proposes a policy."))
                }
            }
            .navigationTitle(selected.map { "\($0.policy_id) · v\($0.version)" } ?? "Blend Policy Candidates")
            .safeAreaInset(edge: .bottom) {
                if let error = evaluation.error {
                    Label(error, systemImage: "exclamationmark.circle")
                        .font(.callout).foregroundStyle(.red)
                        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
                        .background(.bar)
                }
            }
        }
        .navigationSplitViewStyle(.balanced)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task { await evaluation.load(refresh: true) }
                } label: {
                    Label("Refresh Policies", systemImage: "arrow.clockwise")
                }
                .disabled(!evaluation.supported || evaluation.isRequesting)
                .help(
                    evaluation.supported
                        ? "Refresh Blend policy candidates" : "Connect a supported workspace to view Blend policies")
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .frame(width: 760, height: 580)
        .task { await evaluation.load() }
    }

    private func details(_ record: EvaluationSnapshot.PolicyRecord) -> some View {
        Form {
            Section("Candidate") {
                LabeledContent("Policy", value: record.policy_id)
                LabeledContent("Version", value: String(record.version))
                LabeledContent("Status", value: record.status.capitalized)
                LabeledContent("Reason") { Text(record.reason).textSelection(.enabled) }
            }
            Section("Routing") {
                LabeledContent("Default Model", value: record.policy.default_model)
                LabeledContent("After Tool Success", value: record.policy.after_tool_success ?? "Keep Current Model")
                LabeledContent("After Tool Error", value: record.policy.after_tool_error ?? "Keep Current Model")
                LabeledContent("Recovery Model", value: record.policy.recovery_model ?? "None")
            }
            Section("Stability") {
                LabeledContent(
                    "Minimum Steps Before Switching", value: String(record.policy.minimum_model_dwell_steps ?? 1))
                LabeledContent(
                    "Steps Without Progress Before Recovery",
                    value: String(record.policy.recovery_after_no_progress_steps ?? 2))
            }
        }
        .formStyle(.grouped)
    }
}
