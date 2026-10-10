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
}
