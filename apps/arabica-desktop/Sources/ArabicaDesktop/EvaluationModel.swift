import Combine
import Foundation

struct EvaluationCapability: Equatable {
    static func supported(by result: [String: Any]) -> Bool {
        guard let meta = result["_meta"] as? [String: Any],
              let capability = meta["arabica.evaluation"] as? [String: Any] else { return false }
        return capability["schemaVersion"] as? Int == 1
            && capability["get"] as? String == "_arabica/evaluation/get"
            && capability["refresh"] as? String == "_arabica/evaluation/refresh"
    }
}

struct EvaluationSnapshot: Decodable, Equatable, Sendable {
    let schema_version: Int
    let session_id: String
    let workspace_id: String
    let worker_status: WorkerStatus?
    let refresh_accepted: Bool?
    let report: Report?
    let policies: [PolicyRecord]?

    struct PolicyRecord: Decodable, Equatable, Sendable, Identifiable {
        var id: String { "\(policy_id)-v\(version)" }
        let policy_id: String
        let version: UInt64
        let status: String
        let policy: PolicyDetails
        let reason: String
        let proposal: Proposal?
        let activated_at: String?

        struct PolicyDetails: Decodable, Equatable, Sendable {
            let default_model: String
            let after_tool_success: String?
            let after_tool_error: String?
            let recovery_model: String?
            let recovery_after_no_progress_steps: Int?
            let minimum_model_dwell_steps: Int?
            let tool_routes: [ToolRoute]?
        }

        struct ToolRoute: Decodable, Equatable, Sendable, Identifiable {
            let id: String
            let model: String
            let priority: Int?
        }
    }

    struct Proposal: Decodable, Equatable, Sendable {
        let proposal_id: String
        let base_policy_id: String
        let base_version: UInt64
        let candidate_version: UInt64
        let rationale: String
        let evidence: ProposalEvidence
    }

    struct ProposalEvidence: Decodable, Equatable, Sendable {
        let metric: String
        let observed_value: String
        let threshold: String
        let category: String?
        let source_fingerprint: String?
        let tool_name: String?
    }

    struct WorkerStatus: Decodable, Equatable, Sendable {
        let completed: UInt64
        let failed: UInt64
        let dropped: UInt64
    }
    struct Report: Decodable, Equatable, Sendable {
        let checkpoint: Checkpoint
        let context: Result
        let model: Result
        let runs: [Run]?
    }
    struct Run: Decodable, Equatable, Sendable, Identifiable {
        var id: String { snapshot.run_id }
        let snapshot: RunSnapshot
        let acceptance: Acceptance?
    }
    struct RunSnapshot: Decodable, Equatable, Sendable {
        let run_id: String
        let session_id: String
        let workspace_id: String
        let terminal_status: String
        let timing: Timing
        let tools: [ObservedTool]?
        let policy_fingerprints: [String]?
    }
    struct ObservedTool: Decodable, Equatable, Sendable, Identifiable {
        var id: String { call_id }
        let call_id: String
        let name: String
        let step: Int
        let outcome: String
        let elapsed_ms: UInt64
    }
    struct Timing: Decodable, Equatable, Sendable {
        let queue_ms: UInt64?
        let wall_ms: UInt64?
        let approval_wait_ms: UInt64?
        let active_ms: UInt64?
        let model_call_ms_total: UInt64
        let runner_ms_total: UInt64?
        let model_calls: UInt64
        let usage_reported_calls: UInt64
    }
    struct Acceptance: Decodable, Equatable, Sendable {
        let task_id: String?
        let completion_bps: UInt32?
        let coverage_bps: UInt32?
        let verified_success: Bool?
        let results: [CriterionResult]
    }
    struct CriterionResult: Decodable, Equatable, Sendable, Identifiable {
        var id: String { criterion_id }
        let criterion_id: String
        let status: String
        let evidence: String?
    }
    struct Checkpoint: Decodable, Equatable, Sendable {
        let session_id: String
        let workspace_id: String
        let sequence: UInt64
    }
    struct Result: Decodable, Equatable, Sendable {
        let plugin: Plugin
        let evidence_fingerprint: String
        let data: Results
    }
    struct Plugin: Decodable, Equatable, Sendable {
        let id: String
        let version: UInt64
    }
    struct Results: Decodable, Equatable, Sendable {
        let groups: [Group]
        let diagnostics: [Diagnostic]
    }
    struct Diagnostic: Decodable, Equatable, Sendable {
        let type: String
        let found: UInt64?
        let required: UInt64?
        let runs: UInt64?
        var explanation: String {
            switch type {
            case "insufficient_samples": "Clustering needs \(required ?? 0) runs; \(found ?? 0) available."
            case "missing_behavior_evidence": "\(runs ?? 0) runs lack behavior evidence."
            case "missing_or_changing_model_registry": "\(runs ?? 0) runs were excluded because routing metadata was missing or changed."
            default: "Additional evaluation diagnostics are available in the CLI report."
            }
        }
    }
    struct Group: Decodable, Equatable, Sendable {
        let group_id: String
        let sample_runs: UInt64
        let routing_policy_fingerprint: String?
        let model_registry_fingerprint: String?
        let report: Metrics
    }
    struct Metrics: Decodable, Equatable, Sendable {
        let items: [String: [String: UInt64]]?
        let models: [String: [String: UInt64]]?
    }

    static func decode(_ result: [String: Any], session: String) throws -> Self {
        let snapshot = try JSONDecoder().decode(Self.self, from: JSONSerialization.data(withJSONObject: result))
        guard snapshot.schema_version == 1, snapshot.session_id == session,
              snapshot.report == nil || (snapshot.report?.checkpoint.session_id == session
                && snapshot.report?.checkpoint.workspace_id == snapshot.workspace_id),
              (snapshot.report?.runs ?? []).allSatisfy({
                  $0.snapshot.session_id == session && $0.snapshot.workspace_id == snapshot.workspace_id
              }) else { throw ACPError.invalidResponse }
        return snapshot
    }
}

@MainActor
final class EvaluationModel: ObservableObject {
    typealias Request = (String, [String: Any]) async throws -> [String: Any]
    @Published private(set) var supported = false
    @Published private(set) var sessionID: String?
    @Published private(set) var snapshot: EvaluationSnapshot?
    @Published private(set) var isRequesting = false
    @Published private(set) var message: String?
    @Published private(set) var error: String?
    @Published private(set) var queriedAt: Date?
    private var cache: [String: EvaluationSnapshot] = [:]
    private var generation = UUID()
    private var request: Request?

    func connect(supported: Bool, request: @escaping Request) {
        reset()
        self.supported = supported
        self.request = request
    }
    func reset() {
        generation = UUID()
        supported = false
        request = nil
        cache = [:]
        select(nil)
    }
    func select(_ id: String?) {
        generation = UUID()
        sessionID = id
        snapshot = id.flatMap { cache[$0] }
        isRequesting = false
        error = nil
        message = nil
        queriedAt = nil
    }
    func load(refresh: Bool = false) async {
        guard supported, !isRequesting, let session = sessionID, let request else { return }
        let scope = generation
        isRequesting = true
        error = nil
        defer { if generation == scope { isRequesting = false } }
        do {
            let response = try await request(refresh ? "_arabica/evaluation/refresh" : "_arabica/evaluation/get", ["sessionId": session])
            guard generation == scope, !Task.isCancelled else { return }
            let next = try EvaluationSnapshot.decode(response, session: session)
            // An absent cache entry during worker activity must not erase a report.
            if next.report != nil || snapshot?.report == nil {
                snapshot = next
                cache[session] = next
            } else if let previous = snapshot {
                snapshot = EvaluationSnapshot(schema_version: next.schema_version, session_id: next.session_id,
                    workspace_id: next.workspace_id, worker_status: next.worker_status,
                    refresh_accepted: next.refresh_accepted, report: previous.report,
                    policies: next.policies ?? previous.policies)
            }
            queriedAt = Date()
            if refresh {
                message = next.refresh_accepted == true
                    ? "Refresh queued. Cached results remain available."
                    : "Refresh was not queued. Try again shortly."
            }
        } catch {
            guard generation == scope, !Task.isCancelled else { return }
            self.error = "Could not read evaluation status. Retry or reconnect the workspace."
        }
    }
    func approvePolicy(policyId: String, version: UInt64) async {
        await performPolicyAction(action: "approve", policyId: policyId, version: version)
    }

    func rejectPolicy(policyId: String, version: UInt64) async {
        await performPolicyAction(action: "reject", policyId: policyId, version: version)
    }

    func activatePolicy(policyId: String, version: UInt64) async {
        await performPolicyAction(action: "activate", policyId: policyId, version: version)
    }

    func quickApplyPolicy(policyId: String, version: UInt64) async {
        await approvePolicy(policyId: policyId, version: version)
        if error == nil {
            await activatePolicy(policyId: policyId, version: version)
            message = "Policy \(policyId) v\(version) activated for new sessions."
        }
    }

    func rollbackPolicy(policyId: String, targetVersion: UInt64? = nil) async {
        await performPolicyAction(action: "rollback", policyId: policyId, targetVersion: targetVersion)
    }

    func proposePolicy(policyId: String? = nil) async {
        await performPolicyAction(action: "propose", policyId: policyId)
    }

    private func performPolicyAction(
        action: String,
        policyId: String? = nil,
        version: UInt64? = nil,
        targetVersion: UInt64? = nil
    ) async {
        guard supported, !isRequesting, let session = sessionID, let request else { return }
        let scope = generation
        isRequesting = true
        error = nil
        defer { if generation == scope { isRequesting = false } }
        do {
            var params: [String: Any] = ["sessionId": session, "action": action]
            if let policyId { params["policyId"] = policyId }
            if let version { params["version"] = version }
            if let targetVersion { params["targetVersion"] = targetVersion }
            let response = try await request("_arabica/evaluation/policy/action", params)
            guard generation == scope, !Task.isCancelled else { return }
            let next = try EvaluationSnapshot.decode(response, session: session)
            snapshot = next
            cache[session] = next
            queriedAt = Date()
            message = "Policy \(action) completed."
        } catch {
            guard generation == scope, !Task.isCancelled else { return }
            self.error = "Failed to \(action) policy: \(error.localizedDescription)"
        }
    }

    func poll() async {
        await load()
        while !Task.isCancelled {
            do { try await Task.sleep(for: .seconds(5)) } catch { return }
            await load()
        }
    }
}
