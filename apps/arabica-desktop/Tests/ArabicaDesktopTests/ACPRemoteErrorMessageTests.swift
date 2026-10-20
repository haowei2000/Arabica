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

    func testRejectedSessionOptionShowsTheActionableReason() {
        XCTAssertEqual(
            ACPRemoteErrorMessage.resolve([
                "message": "Invalid params",
                "data": "unsupported thinking level for this provider",
            ]),
            "unsupported thinking level for this provider"
        )
    }

    func testBusySessionShowsTheReasonForRejectingAConfigurationChange() {
        XCTAssertEqual(
            ACPRemoteErrorMessage.resolve([
                "message": "Invalid request",
                "data": ["reason": "a prompt is already in progress for this session"],
            ]),
            "a prompt is already in progress for this session"
        )
    }

    func testInvalidRequestWithoutUsefulDetailKeepsItsProtocolMessage() {
        let invalidDetails: [Any] = ["  \n ", ["detail": "  "], ["unexpected": "value"]]
        for data in invalidDetails {
            XCTAssertEqual(
                ACPRemoteErrorMessage.resolve(["message": "Invalid request", "data": data]),
                "Invalid request"
            )
        }
    }

    func testSpecificRemoteMessageIsNotReplacedByDiagnosticPayload() {
        XCTAssertEqual(
            ACPRemoteErrorMessage.resolve([
                "message": "This model is unavailable",
                "data": "unrelated diagnostic",
            ]),
            "This model is unavailable"
        )
    }

    func testInternalErrorWithoutDetailsUsesFallback() {
        XCTAssertEqual(
            ACPRemoteErrorMessage.resolve(["message": "Internal error"]),
            "The agent could not complete this run. Check the provider configuration and connection."
        )
    }
}
