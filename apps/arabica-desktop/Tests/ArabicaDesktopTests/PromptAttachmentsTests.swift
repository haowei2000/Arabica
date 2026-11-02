import XCTest
@testable import ArabicaDesktop

final class PromptAttachmentsTests: XCTestCase {
    func testImageBytesAreSentInlineAndDocumentsRemainLinks() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let image = directory.appendingPathComponent("screenshot.png")
        let bytes = Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")!
        try bytes.write(to: image)
        let document = directory.appendingPathComponent("notes.txt")
        let blocks = try PromptAttachments.blocks(for: [image, document], supportsImages: true)
        XCTAssertEqual(blocks[0]["type"] as? String, "image")
        XCTAssertEqual(blocks[0]["mimeType"] as? String, "image/png")
        XCTAssertEqual(Data(base64Encoded: blocks[0]["data"] as! String), bytes)
        XCTAssertEqual(blocks[1]["type"] as? String, "resource_link")
        XCTAssertEqual(blocks[1]["uri"] as? String, document.absoluteString)
        XCTAssertThrowsError(try PromptAttachments.blocks(for: [image], supportsImages: false))
        XCTAssertThrowsError(try PromptAttachments.blocks(for: Array(repeating: image, count: 5), supportsImages: true))
    }

    func testEmptyOversizedAndUnsupportedImagesAreRejected() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let image = directory.appendingPathComponent("image.jpg")
        try Data().write(to: image)
        XCTAssertThrowsError(try PromptAttachments.blocks(for: [image], supportsImages: true))
        try Data(repeating: 0, count: PromptAttachments.maxImageBytes + 1).write(to: image)
        XCTAssertThrowsError(try PromptAttachments.blocks(for: [image], supportsImages: true))
        XCTAssertThrowsError(try PromptAttachments.blocks(for: [directory.appendingPathComponent("image.heic")], supportsImages: true))
    }
}
