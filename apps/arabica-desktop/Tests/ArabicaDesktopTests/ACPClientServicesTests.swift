import XCTest
@testable import ArabicaDesktop

@MainActor
final class ACPClientServicesTests: XCTestCase {
    func testFileRequestsStayInWorkspace() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let services = ACPClientServices()
        let file = root.appendingPathComponent("note.txt")
        _ = try await services.handle("fs/write_text_file", params: [
            "sessionId": "s1", "path": file.path, "content": "one\ntwo\nthree",
        ], workspace: root)
        let read = try await services.handle("fs/read_text_file", params: [
            "sessionId": "s1", "path": file.path, "line": 2, "limit": 1,
        ], workspace: root)
        XCTAssertEqual(read["content"] as? String, "two")
        let outside = FileManager.default.temporaryDirectory.appendingPathComponent("outside.txt")
        try FileManager.default.createSymbolicLink(at: root.appendingPathComponent("link.txt"),
                                                    withDestinationURL: outside)
        do {
            _ = try await services.handle("fs/write_text_file", params: [
                "sessionId": "s1", "path": root.appendingPathComponent("link.txt").path,
                "content": "secret",
            ], workspace: root)
            XCTFail("Expected a symlink escape to be rejected")
        } catch ServiceError.invalid { }
    }

    func testTerminalLifecycle() async throws {
        let root = FileManager.default.temporaryDirectory
        let services = ACPClientServices()
        defer { Task { @MainActor in services.reset() } }
        let created = try await services.handle("terminal/create", params: [
            "sessionId": "s1", "command": "/bin/echo", "args": ["hello"], "cwd": root.path,
        ], workspace: root)
        let id = try XCTUnwrap(created["terminalId"] as? String)
        let exited = try await services.handle("terminal/wait_for_exit", params: [
            "sessionId": "s1", "terminalId": id,
        ], workspace: root)
        XCTAssertNotNil(exited["exitStatus"])
        _ = try await services.handle("terminal/release", params: [
            "sessionId": "s1", "terminalId": id,
        ], workspace: root)
    }
}
