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

    func testGroupedForFeedConsecutiveTools() {
        let items: [ChatItem] = [
            ChatItem(kind: .user, text: "Hello"),
            ChatItem(kind: .thought, text: "Thinking about it..."),
            ChatItem(kind: .tool, text: "Read file", status: "completed", toolID: "t1", toolKind: "read"),
            ChatItem(kind: .tool, text: "Grep symbols", status: "completed", toolID: "t2", toolKind: "search"),
            ChatItem(kind: .assistant, text: "Here is the result"),
            ChatItem(kind: .tool, text: "Run tests", status: "in_progress", toolID: "t3", toolKind: "execute"),
        ]

        let feed = items.groupedForFeed
        XCTAssertEqual(feed.count, 5)

        if case .user(let item) = feed[0] {
            XCTAssertEqual(item.text, "Hello")
        } else {
            XCTFail("Expected .user")
        }

        if case .thought(let item) = feed[1] {
            XCTAssertEqual(item.text, "Thinking about it...")
        } else {
            XCTFail("Expected .thought")
        }

        if case .toolGroup(_, let tools) = feed[2] {
            XCTAssertEqual(tools.count, 2)
            XCTAssertEqual(tools[0].toolKind, "read")
            XCTAssertEqual(tools[1].toolKind, "search")
        } else {
            XCTFail("Expected .toolGroup")
        }

        if case .assistant(let item) = feed[3] {
            XCTAssertEqual(item.text, "Here is the result")
        } else {
            XCTFail("Expected .assistant")
        }

        if case .toolGroup(_, let tools) = feed[4] {
            XCTAssertEqual(tools.count, 1)
            XCTAssertEqual(tools[0].toolKind, "execute")
        } else {
            XCTFail("Expected .toolGroup")
        }
    }

    func testToolVisualIcons() {
        XCTAssertEqual(ToolVisuals.icon(for: "read"), "doc.text.magnifyingglass")
        XCTAssertEqual(ToolVisuals.icon(for: "search"), "magnifyingglass")
        XCTAssertEqual(ToolVisuals.icon(for: "edit"), "square.and.pencil")
        XCTAssertEqual(ToolVisuals.icon(for: "execute"), "terminal")
        XCTAssertEqual(ToolVisuals.icon(for: "delete"), "trash")
        XCTAssertEqual(ToolVisuals.icon(for: "move"), "arrow.right.doc.on.clipboard")
        XCTAssertEqual(ToolVisuals.icon(for: "fetch"), "globe")
        XCTAssertEqual(ToolVisuals.icon(for: "think"), "brain.head.profile")
        XCTAssertEqual(ToolVisuals.icon(for: "switch_mode"), "arrow.triangle.2.circlepath")
        XCTAssertEqual(ToolVisuals.icon(for: "custom", title: "git status"), "arrow.triangle.branch")
        XCTAssertEqual(ToolVisuals.icon(for: "unknown", title: "custom tool"), "wrench.and.screwdriver")
    }
}
