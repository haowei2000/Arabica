import XCTest
@testable import ArabicaDesktop

@MainActor
final class EvaluationModelTests: XCTestCase {
    private func view(_ session: String = "session", report: Bool = true, accepted: Bool? = nil) -> [String: Any] {
        let result: [String: Any] = ["plugin": ["id": "observational", "version": 1],
                                   "evidence_fingerprint": "evidence", "data": ["groups": [], "diagnostics": []]]
        var value: [String: Any] = ["schema_version": 1, "workspace_id": "workspace", "session_id": session,
            "worker_status": ["completed": 2, "failed": 0, "dropped": 0],
            "report": report ? ["checkpoint": ["session_id": session, "workspace_id": "workspace", "sequence": 42],
                               "context": result, "model": result] : NSNull()]
        if let accepted { value["refresh_accepted"] = accepted }
        return value
    }

    func testCapabilityRequiresSupportedVersionAndMethods() {
        XCTAssertFalse(EvaluationCapability.supported(by: [:]))
        let capability: [String: Any] = ["schemaVersion": 1, "get": "_arabica/evaluation/get", "refresh": "_arabica/evaluation/refresh"]
        XCTAssertTrue(EvaluationCapability.supported(by: ["_meta": ["arabica.evaluation": capability]]))
        var unsupported = capability
        unsupported["schemaVersion"] = 2
        XCTAssertFalse(EvaluationCapability.supported(by: ["_meta": ["arabica.evaluation": unsupported]]))
    }

    func testRejectsOtherSessionOrUnsupportedSchema() throws {
        XCTAssertThrowsError(try EvaluationSnapshot.decode(view("other"), session: "session"))
        var response = view()
        response["schema_version"] = 2
        XCTAssertThrowsError(try EvaluationSnapshot.decode(response, session: "session"))
        XCTAssertEqual(try EvaluationSnapshot.decode(view(), session: "session").report?.checkpoint.sequence, 42)
    }

    func testRunTimingAndUnknownAcceptanceDecodeWithoutInventingSuccess() throws {
        var response = view()
        var report = response["report"] as! [String: Any]
        var snapshot: [String: Any] = ["run_id": "run", "session_id": "session", "workspace_id": "workspace",
            "terminal_status": "completed", "timing": ["wall_ms": 2000, "approval_wait_ms": 500, "active_ms": 1500,
                "model_call_ms_total": 900, "model_calls": 2, "usage_reported_calls": 1]]
        report["runs"] = [["snapshot": snapshot, "acceptance": ["coverage_bps": 0, "results": []]]]
        response["report"] = report
        let decoded = try EvaluationSnapshot.decode(response, session: "session").report!.runs!.first!
        XCTAssertEqual(decoded.snapshot.timing.active_ms, 1500)
        XCTAssertNil(decoded.snapshot.timing.runner_ms_total)
        XCTAssertNil(decoded.acceptance?.verified_success)
        snapshot["session_id"] = "other"
        report["runs"] = [["snapshot": snapshot]]
        response["report"] = report
        XCTAssertThrowsError(try EvaluationSnapshot.decode(response, session: "session"))
    }

    func testRefreshAcceptanceRetainsCachedReportWhenWorkerReturnsNoReport() async {
        let model = EvaluationModel()
        var requests: [String] = []
        model.connect(supported: true) { method, params in
            requests.append(method)
            XCTAssertEqual(params["sessionId"] as? String, "session")
            return self.view(report: requests.count == 1, accepted: method.hasSuffix("refresh") ? true : nil)
        }
        model.select("session")
        await model.load()
        await model.load(refresh: true)
        XCTAssertEqual(requests, ["_arabica/evaluation/get", "_arabica/evaluation/refresh"])
        XCTAssertEqual(model.snapshot?.report?.checkpoint.sequence, 42)
        XCTAssertEqual(model.message, "Refresh queued. Cached results remain available.")
        XCTAssertFalse(model.isRequesting)
    }

    func testLateResponseCannotReplaceSelectedSession() async {
        let model = EvaluationModel()
        var pending: CheckedContinuation<[String: Any], Error>?
        model.connect(supported: true) { _, _ in
            try await withCheckedThrowingContinuation { pending = $0 }
        }
        model.select("session")
        let query = Task { await model.load() }
        while pending == nil { await Task.yield() }
        model.select("other")
        pending?.resume(returning: view())
        await query.value
        XCTAssertEqual(model.sessionID, "other")
        XCTAssertNil(model.snapshot)
        XCTAssertFalse(model.isRequesting)
    }

    func testReconnectDiscardsOldResponseEvenForSameSessionID() async {
        let model = EvaluationModel()
        var pending: CheckedContinuation<[String: Any], Error>?
        model.connect(supported: true) { _, _ in try await withCheckedThrowingContinuation { pending = $0 } }
        model.select("session")
        let query = Task { await model.load() }
        while pending == nil { await Task.yield() }
        model.reset()
        model.connect(supported: true) { _, _ in self.view(report: false) }
        model.select("session")
        pending?.resume(returning: view())
        await query.value
        XCTAssertNil(model.snapshot)
    }

    func testQueryFailureAndRejectedRefreshDoNotEraseReport() async {
        let model = EvaluationModel()
        var calls = 0
        model.connect(supported: true) { _, _ in
            calls += 1
            if calls == 3 { throw ACPError.remote("private detail") }
            return self.view(accepted: calls == 2 ? false : nil)
        }
        model.select("session")
        await model.load()
        await model.load(refresh: true)
        XCTAssertEqual(model.message, "Refresh was not queued. Try again shortly.")
        await model.load()
        XCTAssertEqual(model.snapshot?.report?.checkpoint.sequence, 42)
        XCTAssertNotNil(model.error)
        XCTAssertFalse(model.error?.contains("private detail") ?? true)
        model.select("other")
        XCTAssertNil(model.snapshot)
        model.select("session")
        XCTAssertEqual(model.snapshot?.report?.checkpoint.sequence, 42)
    }

    func testUnsupportedAgentDoesNotSendExtensionRequests() async {
        let model = EvaluationModel()
        model.connect(supported: false) { _, _ in XCTFail("unexpected request"); return [:] }
        model.select("session")
        await model.load(refresh: true)
        XCTAssertFalse(model.isRequesting)
        XCTAssertNil(model.snapshot)
    }

    func testPolicyLifecycleDecodesWithProposalAndActivatedAt() throws {
        var response = view()
        response["policies"] = [
            [
                "policy_id": "default",
                "version": 2,
                "status": "active",
                "policy": [
                    "default_model": "claude-3-7-sonnet",
                    "after_tool_success": "claude-3-5-haiku",
                    "after_tool_error": "claude-3-7-sonnet",
                    "recovery_model": "claude-3-7-sonnet",
                    "recovery_after_no_progress_steps": 3,
                    "minimum_model_dwell_steps": 1,
                    "tool_routes": [
                        ["id": "bash", "model": "claude-3-7-sonnet", "priority": 10]
                    ]
                ],
                "reason": "Activated after review",
                "proposal": [
                    "proposal_id": "prop-1",
                    "base_policy_id": "default",
                    "base_version": 1,
                    "candidate_version": 2,
                    "rationale": "High tool failure rate on bash",
                    "evidence": [
                        "metric": "tool_failure_rate",
                        "observed_value": "0.35",
                        "threshold": "0.20",
                        "category": "error",
                        "source_fingerprint": "fp-1",
                        "tool_name": "bash"
                    ]
                ],
                "activated_at": "2026-10-21T12:00:00Z"
            ]
        ]
        let decoded = try EvaluationSnapshot.decode(response, session: "session")
        XCTAssertEqual(decoded.policies?.count, 1)
        let policy = decoded.policies!.first!
        XCTAssertEqual(policy.policy_id, "default")
        XCTAssertEqual(policy.version, 2)
        XCTAssertEqual(policy.status, "active")
        XCTAssertEqual(policy.policy.default_model, "claude-3-7-sonnet")
        XCTAssertEqual(policy.policy.tool_routes?.count, 1)
        XCTAssertEqual(policy.policy.tool_routes?.first?.id, "bash")
        XCTAssertEqual(policy.activated_at, "2026-10-21T12:00:00Z")
        XCTAssertEqual(policy.proposal?.proposal_id, "prop-1")
        XCTAssertEqual(policy.proposal?.evidence.tool_name, "bash")
    }

    func testPolicyActionsDispatchCorrectRPCAndParameters() async {
        let model = EvaluationModel()
        var dispatchedMethods: [String] = []
        var dispatchedParams: [[String: Any]] = []

        model.connect(supported: true) { method, params in
            dispatchedMethods.append(method)
            dispatchedParams.append(params)
            return self.view()
        }
        model.select("session")

        await model.approvePolicy(policyId: "default", version: 2)
        XCTAssertEqual(dispatchedMethods.last, "_arabica/evaluation/policy/action")
        XCTAssertEqual(dispatchedParams.last?["action"] as? String, "approve")
        XCTAssertEqual(dispatchedParams.last?["policyId"] as? String, "default")
        XCTAssertEqual(dispatchedParams.last?["version"] as? UInt64, 2)
        XCTAssertEqual(model.message, "Policy approve completed.")

        await model.activatePolicy(policyId: "default", version: 2)
        XCTAssertEqual(dispatchedParams.last?["action"] as? String, "activate")
        XCTAssertEqual(model.message, "Policy activate completed.")

        await model.rollbackPolicy(policyId: "default", targetVersion: 1)
        XCTAssertEqual(dispatchedParams.last?["action"] as? String, "rollback")
        XCTAssertEqual(dispatchedParams.last?["targetVersion"] as? UInt64, 1)
        XCTAssertEqual(model.message, "Policy rollback completed.")

        await model.proposePolicy(policyId: "default")
        XCTAssertEqual(dispatchedParams.last?["action"] as? String, "propose")
        XCTAssertEqual(dispatchedParams.last?["policyId"] as? String, "default")
        XCTAssertEqual(model.message, "Policy propose completed.")
    }
}
