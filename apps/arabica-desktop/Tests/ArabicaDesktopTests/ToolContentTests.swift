import XCTest
@testable import ArabicaDesktop

@MainActor
final class ToolContentTests: XCTestCase {
    func testTextDiffAndTerminalContent() {
        let detail = DesktopModel.toolDetail([
            ["type": "content", "content": ["type": "text", "text": "Found 2 matches"]],
            ["type": "diff", "path": "/workspace/a.txt", "oldText": "before", "newText": "after"],
            ["type": "terminal", "terminalId": "term-1"],
        ])
        XCTAssertTrue(detail?.contains("Found 2 matches") == true)
        XCTAssertTrue(detail?.contains("/workspace/a.txt") == true)
        XCTAssertTrue(detail?.contains("− before") == true)
        XCTAssertTrue(detail?.contains("+ after") == true)
        XCTAssertTrue(detail?.contains("Terminal term-1") == true)
    }
}
