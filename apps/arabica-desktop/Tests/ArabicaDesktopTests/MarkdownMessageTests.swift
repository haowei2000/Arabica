import XCTest
@testable import ArabicaDesktop

final class MarkdownMessageTests: XCTestCase {
    func testBlockStructureAndLiteralCode() {
        let blocks = MarkdownBlock.parse("# Title\n\nHello **world**\n\n- Item\n2. Next\n> Quote\n---\n```swift\n# literal\n\n**code**\n```")
        XCTAssertEqual(blocks.map(\.kind), [.heading(1), .paragraph, .list("•"), .list("2."), .quote, .rule, .code])
        XCTAssertEqual(blocks.last?.text, "# literal\n\n**code**")
    }

    func testStreamingUnclosedFenceAndPlainText() {
        XCTAssertEqual(MarkdownBlock.parse("Before\n~~~\nlet x = 1"), [
            MarkdownBlock(kind: .paragraph, text: "Before"),
            MarkdownBlock(kind: .code, text: "let x = 1"),
        ])
        XCTAssertEqual(MarkdownBlock.parse("#hashtag\ncontinued"), [
            MarkdownBlock(kind: .paragraph, text: "#hashtag\ncontinued"),
        ])
    }

    func testGFMTableAndColumnAlignment() {
        XCTAssertEqual(MarkdownBlock.parse("| Name | Count |\n|:-----|------:|\n| Tea | 2 |\n| Coffee | 5 |"), [
            MarkdownBlock(kind: .table(
                headers: ["Name", "Count"],
                rows: [["Tea", "2"], ["Coffee", "5"]],
                alignments: [.leading, .trailing]
            ), text: ""),
        ])
    }

    func testTableKeepsEscapedAndInlineCodePipesTogether() {
        let source = "| Name | Value |\n| --- | --- |\n| Inline | `a|b` |\n| Escaped | a\\|b |"
        XCTAssertEqual(MarkdownBlock.parse(source), [
            MarkdownBlock(kind: .table(
                headers: ["Name", "Value"],
                rows: [["Inline", "`a|b`"], ["Escaped", "a|b"]],
                alignments: [.leading, .leading]
            ), text: ""),
        ])
        XCTAssertEqual(MarkdownBlock.parse("```\n" + source + "\n```").first?.kind, .code)
        XCTAssertEqual(MarkdownBlock.parse("a | b\nnot | separator").first?.kind, .paragraph)
    }
}
