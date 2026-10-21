import Foundation
import XCTest

@testable import ArabicaDesktop

@MainActor
final class SettingsTerminationTests: XCTestCase {
    private let path = ["providers", "primary", "base_url"]

    func testQuitPreparationSavesAValidPendingDraftWithoutPrompting() async throws {
        let backend = QuitBackend(rejectApply: false)
        let store = makeStore(backend)
        _ = await store.reload()
        store.set(.string("new"), at: path)
        let prepared = await SettingsQuitPreparation.prepare(store: store) {
            XCTFail("A valid draft should save without a discard prompt")
            return .keepEditing
        }
        XCTAssertTrue(prepared)
        XCTAssertFalse(store.hasUnsavedChanges)
        XCTAssertEqual(store.string(at: path), "new")
        let writes = await backend.writes()
        XCTAssertEqual(writes, 1)
    }

    func testKeepEditingCancelsQuitAndPreservesInvalidDraft() async throws {
        let backend = QuitBackend(rejectApply: true)
        let store = makeStore(backend)
        _ = await store.reload()
        store.set(.string("invalid"), at: path)
        let prepared = await SettingsQuitPreparation.prepare(store: store) { .keepEditing }
        XCTAssertFalse(prepared)
        XCTAssertTrue(store.hasUnsavedChanges)
        XCTAssertEqual(store.string(at: path), "invalid")
        XCTAssertEqual(store.fieldError(at: path), "Enter an HTTP URL.")
    }

    func testExplicitDiscardAllowsQuitAndRestoresSavedValues() async throws {
        let backend = QuitBackend(rejectApply: true)
        let store = makeStore(backend)
        _ = await store.reload()
        store.set(.string("invalid"), at: path)
        let prepared = await SettingsQuitPreparation.prepare(store: store) { .discardChanges }
        XCTAssertTrue(prepared)
        XCTAssertFalse(store.hasUnsavedChanges)
        XCTAssertEqual(store.string(at: path), "old")
        let writes = await backend.writes()
        XCTAssertEqual(writes, 1, "Discard must not write a replacement configuration")
    }

    private func makeStore(_ backend: QuitBackend) -> SettingsFormStore {
        SettingsFormStore(
            homeURL: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString),
            executableURL: URL(fileURLWithPath: "/unused/arabica"),
            executor: { command in try await backend.execute(command) })
    }
}

private actor QuitBackend {
    private let rejectApply: Bool
    private var applyCount = 0

    init(rejectApply: Bool) { self.rejectApply = rejectApply }
    func writes() -> Int { applyCount }

    func execute(_ command: SettingsCommand) throws -> SettingsCommandResult {
        if command.arguments == ["config", "apply"] {
            applyCount += 1
            if rejectApply {
                let response = ErrorResponse(
                    error: SettingsFailure(
                        code: "invalid_config", message: "Enter an HTTP URL.",
                        path: ["providers", "primary", "base_url"]))
                return SettingsCommandResult(status: 1, output: try JSONEncoder().encode(response))
            }
        }
        let snapshot = SettingsSnapshot(
            revision: applyCount == 0 ? "one" : "two",
            document: .object([
                "providers": .object([
                    "primary": .object([
                        "base_url": .string(applyCount == 0 ? "old" : "new")
                    ])
                ])
            ]), secretPaths: [])
        return SettingsCommandResult(status: 0, output: try JSONEncoder().encode(snapshot))
    }

    private struct ErrorResponse: Encodable { let error: SettingsFailure }
}
