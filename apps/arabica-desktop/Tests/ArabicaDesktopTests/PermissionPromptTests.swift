import XCTest

@testable import ArabicaDesktop

final class PermissionPromptTests: XCTestCase {
    private func prompt(_ options: [(id: String, name: String, kind: String?)]) -> PermissionPrompt {
        PermissionPrompt(
            id: .int(1), title: "Run a tool", detail: "", options: options,
            method: "session/request_permission", params: [:]
        )
    }

    func testRejectionsComeFirstAndAllowOnceTakesTheDefaultPosition() {
        let prompt = prompt([
            ("a1", "Allow", "allow_once"),
            ("a2", "Allow Always", "allow_always"),
            ("r1", "Reject", "reject_once"),
            ("r2", "Reject Always", "reject_always"),
        ])
        XCTAssertEqual(prompt.orderedOptions.map(\.id), ["r2", "r1", "a2", "a1"])
        XCTAssertTrue(prompt.hasRejectOnceOption)
    }

    func testKindFallsBackToOptionIDAndUnknownKindsKeepTheirOrder() {
        let prompt = prompt([
            ("allow_once", "Allow", nil),
            ("custom-b", "B", "custom"),
            ("custom-a", "A", "custom"),
        ])
        XCTAssertEqual(prompt.orderedOptions.map(\.id), ["custom-b", "custom-a", "allow_once"])
        XCTAssertFalse(prompt.hasRejectOnceOption)
    }
}
