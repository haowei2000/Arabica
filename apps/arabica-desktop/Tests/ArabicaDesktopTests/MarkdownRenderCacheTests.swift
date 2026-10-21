import XCTest

@testable import ArabicaDesktop

final class MarkdownSourceCacheTests: XCTestCase {
    func testReusesUnchangedSourceAndKeepsStreamingRevisionsDistinct() {
        var cache = MarkdownSourceCache<String>(entryLimit: 4, sourceByteLimit: 128, entrySourceByteLimit: 64)
        var parses = 0
        func parse(_ source: String) -> String {
            parses += 1
            return source.uppercased()
        }

        XCTAssertEqual(cache.value(for: "hello") { parse("hello") }, "HELLO")
        XCTAssertEqual(cache.value(for: "hello **world**") { parse("hello **world**") }, "HELLO **WORLD**")
        XCTAssertEqual(cache.value(for: "hello") { parse("hello") }, "HELLO")
        XCTAssertEqual(parses, 2, "Returning to an earlier draft must reuse only that exact source")
    }

    func testEntryLimitEvictsLeastRecentlyUsedSource() {
        var cache = MarkdownSourceCache<Int>(entryLimit: 2, sourceByteLimit: 32, entrySourceByteLimit: 32)
        XCTAssertEqual(cache.value(for: "a") { 1 }, 1)
        XCTAssertEqual(cache.value(for: "b") { 2 }, 2)
        XCTAssertEqual(
            cache.value(for: "a") {
                XCTFail("Expected a cache hit")
                return 0
            }, 1)
        XCTAssertEqual(cache.value(for: "c") { 3 }, 3)
        XCTAssertEqual(cache.count, 2)
        XCTAssertEqual(
            cache.value(for: "a") {
                XCTFail("The recently read entry must survive")
                return 0
            }, 1)
        XCTAssertEqual(cache.value(for: "b") { 20 }, 20)
        XCTAssertEqual(cache.count, 2)
    }

    func testSourceBudgetUsesUTF8BytesAndOversizedSourcesDoNotEvictHistory() {
        var cache = MarkdownSourceCache<Int>(entryLimit: 8, sourceByteLimit: 6, entrySourceByteLimit: 4)
        XCTAssertEqual(cache.value(for: "茶") { 1 }, 1)
        XCTAssertEqual(cache.value(for: "abc") { 2 }, 2)
        XCTAssertEqual(cache.sourceBytes, 6)
        XCTAssertEqual(cache.value(for: "茶茶") { 3 }, 3)
        XCTAssertEqual(cache.value(for: "茶茶") { 30 }, 30, "Oversized values must not enter the cache")
        XCTAssertEqual(cache.count, 2)
        XCTAssertEqual(
            cache.value(for: "茶") {
                XCTFail("Large drafts must not displace existing history")
                return 0
            }, 1)
        XCTAssertEqual(cache.value(for: "xyz") { 4 }, 4)
        XCTAssertEqual(cache.sourceBytes, 6)
        XCTAssertEqual(cache.value(for: "abc") { 20 }, 20, "The byte budget must evict its least recent source")
        XCTAssertLessThanOrEqual(cache.sourceBytes, 6)
    }
}

@MainActor
final class MarkdownRenderCacheTests: XCTestCase {
    func testBlockCachePreservesTablesAndStreamingFenceChanges() {
        let cache = MarkdownRenderCache()
        let draft = "# Title\n\n```swift\nlet value = 1"
        let complete = draft + "\n```\n\n| Model | State |\n| --- | --- |\n| SOE | Ready |"

        XCTAssertEqual(cache.blocks(for: draft), MarkdownBlock.parse(draft))
        XCTAssertEqual(cache.blocks(for: complete), MarkdownBlock.parse(complete))
        XCTAssertEqual(cache.blocks(for: draft), MarkdownBlock.parse(draft))
        XCTAssertEqual(
            cache.blocks(for: complete).last?.kind,
            .table(headers: ["Model", "State"], rows: [["SOE", "Ready"]], alignments: [.leading, .leading]))
    }

    func testInlineCacheKeepsMarkdownSemanticsAndCurrentSource() throws {
        let cache = MarkdownRenderCache()
        let source = "**Bold** `code` [link](https://example.com)\nNext line"
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        let expected = try AttributedString(markdown: source, options: options)

        XCTAssertEqual(cache.inline(for: source), expected)
        XCTAssertEqual(String(cache.inline(for: "replacement").characters), "replacement")
        XCTAssertEqual(cache.inline(for: source), expected)
        XCTAssertEqual(String(cache.inline(for: source).characters), "Bold code link\nNext line")
    }
}
