import XCTest
@testable import ArabicaDesktop

final class ACPRemoteErrorMessageTests: XCTestCase {
    func testInternalErrorUsesACPDiagnosticData() {
        XCTAssertEqual(
            ACPRemoteErrorMessage.resolve([
                "message": "Internal error",
                "data": "OpenAI endpoint rejected request: HTTP 401",
            ]),
            "OpenAI endpoint rejected request: HTTP 401"
        )
    }

    func testInternalErrorCanReadStructuredDiagnosticData() {
        XCTAssertEqual(
            ACPRemoteErrorMessage.resolve([
                "message": "Internal error",
                "data": ["detail": "Request timed out"],
            ]),
            "Request timed out"
        )
    }

    func testNonInternalErrorsKeepTheirProtocolMessage() {
        XCTAssertEqual(
            ACPRemoteErrorMessage.resolve(["message": "Invalid params"]),
            "Invalid params"
        )
    }

    func testInternalErrorWithoutDetailsUsesFallback() {
        XCTAssertEqual(
            ACPRemoteErrorMessage.resolve(["message": "Internal error"]),
            "The agent could not complete this run. Check the provider configuration and connection."
        )
    }
}
