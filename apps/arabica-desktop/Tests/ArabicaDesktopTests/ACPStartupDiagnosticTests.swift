import XCTest

@testable import ArabicaDesktop

final class ACPStartupDiagnosticTests: XCTestCase {
    private let expectedRejection =
        "The Arabica configuration file was rejected at line 2, column 1. "
        + "It contains invalid or unsupported TOML. Open Settings to review and repair the configuration."

    func testRejectionShowsTheLocationExactlyOnceAndOffersRepair() {
        let diagnostic = ACPStartupDiagnostic()
        diagnostic.consume(Data("Error: invalid TOML in /private/config.toml at line 2, column 1\n".utf8))

        XCTAssertEqual(diagnostic.value, expectedRejection)
        XCTAssertFalse(diagnostic.value?.contains("at at") == true)
    }

    func testDiagnosticSurvivesEveryPossibleReadBoundaryIncludingUTF8() {
        let bytes = Data("Error: invalid TOML in /Users/测试/config.toml at line 2, column 1\n".utf8)
        for boundary in bytes.startIndex...bytes.endIndex {
            let diagnostic = ACPStartupDiagnostic()
            diagnostic.consume(bytes.prefix(upTo: boundary))
            diagnostic.consume(bytes.suffix(from: boundary))

            XCTAssertEqual(diagnostic.value, expectedRejection, "Read boundary: \(boundary)")
        }
    }

    func testSingleByteReadsDoNotExposeSourceExcerptsPathsOrCredentials() {
        let diagnostic = ACPStartupDiagnostic()
        let text = """
            provider authorization: Bearer private-token
            Error: invalid TOML in /Users/秘密/config.toml at line 2, column 1
              |
            2 | api_key = "private-token"
              | ^ unsupported configuration field
            """
        for byte in text.utf8 { diagnostic.consume(Data([byte])) }

        XCTAssertEqual(diagnostic.value, expectedRejection)
        for secret in ["private-token", "authorization", "api_key", "/Users/", "秘密", "unsupported configuration field"]
        {
            XCTAssertFalse(diagnostic.value?.contains(secret) == true)
        }
    }

    func testRejectionWithoutALocationStillExplainsTheProblemAndRecovery() {
        let diagnostic = ACPStartupDiagnostic()
        diagnostic.consume(Data("invalid TOML in /private/config.toml".utf8))

        XCTAssertEqual(
            diagnostic.value,
            "The Arabica configuration file was rejected. It contains invalid or unsupported TOML. "
                + "Open Settings to review and repair the configuration."
        )
    }

    func testUnrelatedLocationsAreNotAttachedToTheConfigurationFailure() {
        let diagnostic = ACPStartupDiagnostic()
        diagnostic.consume(Data("provider failed at line 99, column 88\ninvalid TOML in /private/config.toml\n".utf8))

        XCTAssertFalse(diagnostic.value?.contains("line 99") == true)
        XCTAssertTrue(diagnostic.value?.contains("invalid or unsupported TOML") == true)
    }

    func testLocationDigitsContinueAcrossReadsWithoutWaitingForANewline() {
        let diagnostic = ACPStartupDiagnostic()
        diagnostic.consume(Data("invalid TOML in config.toml at line 12, column 1".utf8))
        XCTAssertTrue(diagnostic.value?.contains("at line 12, column 1.") == true)

        diagnostic.consume(Data("23".utf8))
        XCTAssertTrue(diagnostic.value?.contains("at line 12, column 123.") == true)
    }

    func testUnboundedOrZeroLocationNumbersAreIgnored() {
        for location in ["at line 1234567890, column 1", "at line 1, column 1234567890", "at line 0, column 1"] {
            let diagnostic = ACPStartupDiagnostic()
            diagnostic.consume(Data("invalid TOML in config.toml \(location)\n".utf8))

            XCTAssertFalse(diagnostic.value?.contains("at line") == true)
            XCTAssertTrue(diagnostic.value?.contains("Open Settings") == true)
        }
    }

    func testSetupDiagnosticSurvivesFragmentedReads() {
        let diagnostic = ACPStartupDiagnostic()
        for byte in "configure [providers.<name>], [models.<alias>], and [blend]".utf8 {
            diagnostic.consume(Data([byte]))
        }

        XCTAssertEqual(
            diagnostic.value,
            "Arabica needs a provider, model alias, and blend configuration. Open Settings to configure them."
        )
    }

    func testSetupDiagnosticDoesNotReplaceARejectedConfiguration() {
        let diagnostic = ACPStartupDiagnostic()
        diagnostic.consume(Data("invalid TOML in config.toml at line 2, column 1\n".utf8))
        diagnostic.consume(Data("configure [providers.<name>]".utf8))

        XCTAssertEqual(diagnostic.value, expectedRejection)
    }

    func testLargeUnrelatedStderrDoesNotBecomeAUserFacingMessage() {
        let diagnostic = ACPStartupDiagnostic()
        diagnostic.consume(Data(repeating: 0x73, count: 1_000_000))
        XCTAssertNil(diagnostic.value)

        diagnostic.consume(Data("invalid TOML in config.toml at line 2, column 1\n".utf8))
        XCTAssertEqual(diagnostic.value, expectedRejection)
    }

    func testResetDiscardsBothTheMessageAndPartialDiagnostic() {
        let diagnostic = ACPStartupDiagnostic()
        diagnostic.consume(Data("invalid TOML in config.toml at line 2, column 1\ninvalid TO".utf8))
        diagnostic.reset()
        XCTAssertNil(diagnostic.value)

        diagnostic.consume(Data("ML in config.toml at line 5, column 6\n".utf8))
        XCTAssertNil(diagnostic.value)

        diagnostic.consume(Data("invalid TOML in config.toml at line 2, column 1\n".utf8))
        XCTAssertEqual(diagnostic.value, expectedRejection)
    }
}
