import Foundation
import XCTest

@testable import ArabicaDesktop

@MainActor
final class SettingsFormStoreTests: XCTestCase {
    private let urlPath = ["providers", "primary", "base_url"]
    private let keyPath = ["providers", "primary", "api_key"]

    func testDebounceCombinesFieldEditsWithoutSubmittingRedactedSecrets() async throws {
        let backend = SettingsBackend(replies: [
            try response(revision: "one", url: "old"),
            try response(revision: "two", url: "latest"),
        ])
        let store = makeStore(backend, debounce: 20_000_000)
        await store.start()
        store.set(.string("intermediate"), at: urlPath)
        store.set(.string("latest"), at: urlPath)
        try await eventually { await backend.commands().count == 2 && !store.hasUnsavedChanges }

        let commands = await backend.commands()
        XCTAssertEqual(commands.map(\.arguments), [["config", "inspect"], ["config", "apply"]])
        let payload = try applyPayload(commands[1])
        XCTAssertEqual(payload["expected_revision"] as? String, "one")
        let edits = try XCTUnwrap(payload["edits"] as? [[String: Any]])
        XCTAssertEqual(edits.count, 2)
        XCTAssertTrue(edits.allSatisfy { $0["path"] as? [String] == urlPath })
        XCTAssertFalse(
            String(decoding: try XCTUnwrap(commands[1].input), as: UTF8.self).contains("api_key"))
        XCTAssertEqual(store.string(at: urlPath), "latest")
        XCTAssertTrue(store.needsReconnect)
    }

    func testEditsDuringSaveAreReplayedAndCommittedAgainstTheNewRevision() async throws {
        let backend = SettingsBackend(
            replies: [
                try response(revision: "one", url: "old"),
                try response(revision: "two", url: "first"),
                try response(revision: "three", url: "second"),
            ], holdFirstApply: true)
        let store = makeStore(backend)
        let loaded = await store.reload()
        XCTAssertTrue(loaded)
        store.set(.string("first"), at: urlPath)
        let flush = Task { await store.flush() }
        try await eventually { await backend.commands().count == 2 }
        XCTAssertTrue(store.isSaving)
        store.set(.string("second"), at: urlPath)
        XCTAssertEqual(store.string(at: urlPath), "second")
        await backend.releaseApply()
        let saved = await flush.value
        XCTAssertTrue(saved)
        XCTAssertEqual(store.snapshot?.revision, "three")
        XCTAssertEqual(store.string(at: urlPath), "second")
        XCTAssertFalse(store.hasUnsavedChanges)
        let commands = await backend.commands()
        XCTAssertEqual(commands.count, 3)
        XCTAssertEqual(try applyPayload(commands[2])["expected_revision"] as? String, "two")
    }

    func testValidationFailureRetainsTheDraftAndDoesNotAutomaticallyRetry() async throws {
        let backend = SettingsBackend(replies: [
            try response(revision: "one", url: "old"),
            failure(code: "invalid_config", message: "Enter an HTTP URL.", path: urlPath),
            try response(revision: "two", url: "fixed"),
        ])
        let store = makeStore(backend, debounce: 10_000_000)
        await store.start()
        store.set(.string("invalid"), at: urlPath)
        try await eventually { store.lastErrorCode == "invalid_config" }
        XCTAssertEqual(store.string(at: urlPath), "invalid")
        XCTAssertTrue(store.hasUnsavedChanges)
        XCTAssertEqual(store.fieldErrors[urlPath], "Enter an HTTP URL.")
        try await Task.sleep(nanoseconds: 70_000_000)
        let failedCommands = await backend.commands()
        XCTAssertEqual(failedCommands.count, 2)

        store.set(.string("fixed"), at: urlPath)
        try await eventually { await backend.commands().count == 3 && !store.hasUnsavedChanges }
        XCTAssertNil(store.lastErrorCode)
        XCTAssertTrue(store.fieldErrors.isEmpty)
    }

    func testConflictAndReloadGuardKeepTheDraftUntilExplicitDiscard() async throws {
        let backend = SettingsBackend(replies: [
            try response(revision: "one", url: "old"),
            failure(code: "conflict", message: "Configuration changed.", path: []),
            try response(revision: "external", url: "external-value"),
        ])
        let store = makeStore(backend)
        _ = await store.reload()
        store.set(.string("local-value"), at: urlPath)
        let saved = await store.flush()
        XCTAssertFalse(saved)
        XCTAssertEqual(store.lastErrorCode, "conflict")
        XCTAssertEqual(store.string(at: urlPath), "local-value")
        let reloaded = await store.reload()
        XCTAssertFalse(reloaded)
        let guardedCommands = await backend.commands()
        XCTAssertEqual(guardedCommands.count, 2)
        XCTAssertEqual(store.string(at: urlPath), "local-value")

        let discarded = await store.discardAndReload()
        XCTAssertTrue(discarded)
        XCTAssertEqual(store.string(at: urlPath), "external-value")
        XCTAssertFalse(store.hasUnsavedChanges)
    }

    func testBlankSecretKeepsExistingValueAndExplicitRemovalIsAnEdit() async throws {
        let backend = SettingsBackend(replies: [
            try response(revision: "one", url: "old"),
            try response(revision: "two", url: "new"),
            try response(revision: "three", url: "new", hasSecret: false),
        ])
        let store = makeStore(backend)
        _ = await store.reload()
        XCTAssertTrue(store.secretExists(at: keyPath))
        XCTAssertEqual(store.secretText(at: keyPath), "")
        store.setSecretText("replacement-secret", at: keyPath)
        XCTAssertEqual(store.secretText(at: keyPath), "replacement-secret")
        store.set(.string("new"), at: urlPath)
        store.setSecretText("", at: keyPath)
        XCTAssertEqual(store.value(at: keyPath), .null)
        XCTAssertTrue(store.secretExists(at: keyPath))
        let kept = await store.flush()
        XCTAssertTrue(kept)
        let firstCommands = await backend.commands()
        let firstEdits = try XCTUnwrap(try applyPayload(firstCommands[1])["edits"] as? [[String: Any]])
        XCTAssertEqual(firstEdits.count, 1)
        XCTAssertEqual(firstEdits[0]["path"] as? [String], urlPath)
        XCTAssertFalse(
            String(decoding: try XCTUnwrap(firstCommands[1].input), as: UTF8.self).contains(
                "replacement-secret"))

        store.remove(at: keyPath)
        XCTAssertFalse(store.secretExists(at: keyPath))
        let removed = await store.flush()
        XCTAssertTrue(removed)
        let finalCommands = await backend.commands()
        let removal = try XCTUnwrap(try applyPayload(finalCommands[2])["edits"] as? [[String: Any]])
        XCTAssertEqual(removal.count, 1)
        XCTAssertEqual(removal[0]["op"] as? String, "remove")
        XCTAssertEqual(removal[0]["path"] as? [String], keyPath)
        XCTAssertNil(removal[0]["value"])
    }

    func testSecretRestoreDoesNotClaimToUndoAnAlreadySubmittedRemoval() async throws {
        let backend = SettingsBackend(
            replies: [
                try response(revision: "one", url: "old"),
                try response(revision: "two", url: "old", hasSecret: false),
            ], holdFirstApply: true)
        let store = makeStore(backend)
        _ = await store.reload()
        store.remove(at: keyPath)
        let flush = Task { await store.flush() }
        try await eventually { await backend.commands().count == 2 }
        XCTAssertTrue(store.isSaving)
        let savingStatus = store.status

        store.restoreSecret(at: keyPath)
        XCTAssertEqual(store.status, savingStatus)
        XCTAssertNil(store.value(at: keyPath))
        XCTAssertFalse(store.secretExists(at: keyPath))
        XCTAssertTrue(store.hasUnsavedChanges)

        await backend.releaseApply()
        let saved = await flush.value
        XCTAssertTrue(saved)
        XCTAssertFalse(store.secretExists(at: keyPath))
        XCTAssertFalse(store.hasUnsavedChanges)
        let commands = await backend.commands()
        XCTAssertEqual(commands.count, 2)
    }

    func testArrayOperationsKeepTheirOrderAndRebaseSecretBadges() async throws {
        let document: SettingsValue = .object([
            "mcp": .array([
                .object(["name": .string("first")]),
                .object(["name": .string("second"), "env": .object(["TOKEN": .null])]),
            ])
        ])
        let updated: SettingsValue = .object([
            "mcp": .array([
                .object(["name": .string("renamed"), "env": .object(["TOKEN": .null])]),
                .object(["name": .string("third")]),
            ])
        ])
        let backend = SettingsBackend(replies: [
            try snapshotResponse(
                SettingsSnapshot(
                    revision: "one", document: document, secretPaths: [["mcp", "1", "env", "TOKEN"]])),
            try snapshotResponse(
                SettingsSnapshot(
                    revision: "two", document: updated, secretPaths: [["mcp", "0", "env", "TOKEN"]])),
        ])
        let store = makeStore(backend)
        _ = await store.reload()
        store.remove(at: ["mcp", "0"])
        XCTAssertTrue(store.secretExists(at: ["mcp", "0", "env", "TOKEN"]))
        XCTAssertFalse(store.secretExists(at: ["mcp", "1", "env", "TOKEN"]))
        store.set(.string("renamed"), at: ["mcp", "0", "name"])
        store.append(.object(["name": .string("third")]), at: ["mcp"])
        XCTAssertEqual(store.arrayValues(at: ["mcp"]).count, 2)
        let saved = await store.flush()
        XCTAssertTrue(saved)
        let commands = await backend.commands()
        let edits = try XCTUnwrap(try applyPayload(commands[1])["edits"] as? [[String: Any]])
        XCTAssertEqual(edits.compactMap { $0["op"] as? String }, ["remove", "set", "append"])
        XCTAssertEqual(
            edits.compactMap { $0["path"] as? [String] }, [["mcp", "0"], ["mcp", "0", "name"], ["mcp"]])
    }

    func testUnparseableAndTransportErrorsDoNotExposeCommandOutput() async throws {
        let backend = SettingsBackend(replies: [
            SettingsCommandResult(status: 0, output: Data("untrusted-secret-output".utf8))
        ])
        let store = makeStore(backend)
        let loaded = await store.reload()
        XCTAssertFalse(loaded)
        XCTAssertEqual(store.lastErrorCode, "invalid_response")
        XCTAssertFalse(store.status.contains("untrusted-secret-output"))
        XCTAssertNil(store.snapshot)

        let failing = SettingsFormStore(
            homeURL: testHome(), executor: { _ in throw CocoaError(.fileNoSuchFile) })
        let launched = await failing.reload()
        XCTAssertFalse(launched)
        XCTAssertEqual(failing.lastErrorCode, "command_failed")
        XCTAssertFalse(failing.isBusy)
    }

    func testAuthenticationSavesFirstAndRefreshesOnlyAfterTheExplicitAction() async throws {
        let backend = SettingsBackend(replies: [
            try response(revision: "one", url: "old"),
            try response(revision: "two", url: "new"),
            SettingsCommandResult(status: 0, output: Data("connected".utf8)),
            try response(revision: "three", url: "new"),
        ])
        let store = makeStore(backend)
        _ = await store.reload()
        store.set(.string("new"), at: urlPath)
        let authenticated = await store.runChatGPTLogin()
        XCTAssertTrue(authenticated)
        let commands = await backend.commands()
        XCTAssertEqual(
            commands.map(\.arguments),
            [
                ["config", "inspect"], ["config", "apply"], ["auth", "chatgpt-login"],
                ["config", "inspect"],
            ])
        XCTAssertNil(commands[2].input)
        XCTAssertEqual(store.snapshot?.revision, "three")
        XCTAssertTrue(store.needsReconnect)
        store.markReconnected()
        XCTAssertFalse(store.needsReconnect)
    }

    func testLocalDiscardDoesNotWriteOrReloadTheConfiguration() async throws {
        let backend = SettingsBackend(replies: [try response(revision: "one", url: "old")])
        let store = makeStore(backend)
        _ = await store.reload()
        store.set(.string("draft"), at: urlPath)
        let discarded = await store.discardChanges()
        XCTAssertTrue(discarded)
        XCTAssertEqual(store.string(at: urlPath), "old")
        XCTAssertFalse(store.hasUnsavedChanges)
        let commands = await backend.commands()
        XCTAssertEqual(commands.count, 1)
    }

    func testStartRefreshesACleanSnapshotAndPreservesAnExistingDraft() async throws {
        let backend = SettingsBackend(replies: [
            try response(revision: "one", url: "initial"),
            try response(revision: "external", url: "externally-edited"),
        ])
        let store = makeStore(backend, debounce: 60_000_000_000)
        await store.start()
        XCTAssertEqual(store.snapshot?.revision, "one")
        await store.start()
        XCTAssertEqual(store.snapshot?.revision, "external")
        XCTAssertEqual(store.string(at: urlPath), "externally-edited")

        store.set(.string("local-draft"), at: urlPath)
        await store.start()
        XCTAssertEqual(store.string(at: urlPath), "local-draft")
        XCTAssertEqual(store.snapshot?.revision, "external")
        let commands = await backend.commands()
        XCTAssertEqual(commands.map(\.arguments), [["config", "inspect"], ["config", "inspect"]])
        _ = await store.discardChanges()
    }

    func testConcurrentAuthAndSyncShareTheSaveAndStartOnlyOneExternalWriter() async throws {
        let backend = SettingsBackend(
            replies: [
                try response(revision: "one", url: "old"),
                try response(revision: "two", url: "new"),
                SettingsCommandResult(status: 0, output: Data()),
                try response(revision: "three", url: "new"),
            ], holdFirstApply: true, holdFirstExternal: true)
        let store = makeStore(backend)
        _ = await store.reload()
        store.set(.string("new"), at: urlPath)
        let auth = Task { await store.runChatGPTLogin() }
        try await eventually { await backend.commands().count == 2 }
        var syncStarted = false
        let sync = Task {
            syncStarted = true
            return await store.syncModels(provider: "primary")
        }
        try await eventually { syncStarted }
        await backend.releaseApply()
        try await eventually { await backend.commands().count == 3 }
        await backend.releaseExternal()
        let results = await [auth.value, sync.value]
        XCTAssertEqual(results.filter { $0 }.count, 1)
        let commands = await backend.commands()
        let external = commands.filter { $0.arguments.first == "auth" || $0.arguments.first == "models" }
        XCTAssertEqual(external.count, 1)
        XCTAssertEqual(commands.filter { $0.arguments == ["config", "apply"] }.count, 1)
    }

    func testAValidEditDuringARejectedSaveStillAutosaves() async throws {
        let backend = SettingsBackend(
            replies: [
                try response(revision: "one", url: "old"),
                failure(code: "validation", message: "Enter an HTTP URL.", path: urlPath),
                try response(revision: "two", url: "https://fixed.example/v1"),
            ], holdFirstApply: true)
        let store = makeStore(backend, debounce: 10_000_000)
        await store.start()
        store.set(.string("invalid"), at: urlPath)
        try await eventually { await backend.commands().count == 2 }
        store.set(.string("https://fixed.example/v1"), at: urlPath)
        await backend.releaseApply()
        try await eventually { await backend.commands().count == 3 && !store.hasUnsavedChanges }
        XCTAssertNil(store.lastErrorCode)
        XCTAssertEqual(store.string(at: urlPath), "https://fixed.example/v1")
        let commands = await backend.commands()
        XCTAssertEqual(try applyPayload(commands[2])["expected_revision"] as? String, "one")
    }

    func testFailedCloseFlushThenValidEditsResumeAutosaveAtTheLatestRevision() async throws {
        func document(modelID: String, isHTTP: Bool, afterError: String) -> SettingsValue {
            var server: [String: SettingsValue] = ["name": .string("fixture")]
            if isHTTP {
                server["url"] = .string("https://mcp.example/mcp")
            } else {
                server["command"] = .string("/usr/bin/env")
            }
            return .object([
                "models": .object(["default": .object(["model_id": .string(modelID)])]),
                "mcp": .array([.object(server)]),
                "blend": .object(["after_tool_error": .string(afterError)]),
            ])
        }
        let commandPath = ["mcp", "0", "command"]
        let backend = SettingsBackend(replies: [
            try snapshotResponse(
                SettingsSnapshot(
                    revision: "one", document: document(modelID: "initial", isHTTP: false, afterError: "default"),
                    secretPaths: [])),
            try snapshotResponse(
                SettingsSnapshot(
                    revision: "two", document: document(modelID: "updated", isHTTP: false, afterError: "default"),
                    secretPaths: [])),
            failure(code: "validation", message: "Use an absolute command.", path: commandPath),
            failure(code: "validation", message: "Use an absolute command.", path: commandPath),
            try snapshotResponse(
                SettingsSnapshot(
                    revision: "three", document: document(modelID: "updated", isHTTP: true, afterError: "backup"),
                    secretPaths: [])),
        ])
        let store = makeStore(backend, debounce: 10_000_000)
        await store.start()
        store.set(.string("updated"), at: ["models", "default", "model_id"])
        try await eventually { store.snapshot?.revision == "two" && !store.hasUnsavedChanges }
        store.set(.string("relative-command"), at: commandPath)
        try await eventually { store.lastErrorCode == "validation" }
        let canClose = await store.flush()
        XCTAssertFalse(canClose)
        XCTAssertTrue(store.hasUnsavedChanges)

        // Choosing Keep Editing changes no state in the controller. Correcting
        // the draft must restart automatic saves after the failed close flush.
        store.set(.string("/usr/bin/env"), at: commandPath)
        store.remove(at: commandPath)
        store.set(.string("https://mcp.example/mcp"), at: ["mcp", "0", "url"])
        store.set(.string("backup"), at: ["blend", "after_tool_error"])
        try await eventually { store.snapshot?.revision == "three" && !store.hasUnsavedChanges }
        let commands = await backend.commands()
        XCTAssertEqual(commands.count, 5)
        XCTAssertEqual(try applyPayload(commands[4])["expected_revision"] as? String, "two")
        XCTAssertEqual(store.string(at: ["mcp", "0", "url"]), "https://mcp.example/mcp")
        XCTAssertEqual(store.string(at: ["blend", "after_tool_error"]), "backup")
        XCTAssertNil(store.value(at: commandPath))
        XCTAssertNil(store.lastErrorCode)
    }

    private func makeStore(_ backend: SettingsBackend, debounce: UInt64 = 600_000_000)
        -> SettingsFormStore
    {
        SettingsFormStore(
            homeURL: testHome(), debounceNanoseconds: debounce,
            executor: { command in
                try await backend.run(command)
            })
    }

    private func testHome() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent(
            "arabica-settings-form-\(UUID().uuidString)", isDirectory: true)
    }

    private func response(revision: String, url: String, hasSecret: Bool = true) throws
        -> SettingsCommandResult
    {
        var provider: [String: SettingsValue] = ["base_url": .string(url)]
        if hasSecret { provider["api_key"] = .null }
        return try snapshotResponse(
            SettingsSnapshot(
                revision: revision,
                document: .object(["providers": .object(["primary": .object(provider)])]),
                secretPaths: hasSecret ? [keyPath] : []))
    }

    private func snapshotResponse(_ snapshot: SettingsSnapshot) throws -> SettingsCommandResult {
        SettingsCommandResult(status: 0, output: try JSONEncoder().encode(snapshot))
    }

    private func failure(code: String, message: String, path: [String]) -> SettingsCommandResult {
        let object: [String: Any] = ["error": ["code": code, "message": message, "path": path]]
        return SettingsCommandResult(
            status: 1, output: try! JSONSerialization.data(withJSONObject: object))
    }

    private func applyPayload(_ command: SettingsCommand) throws -> [String: Any] {
        try XCTUnwrap(
            try JSONSerialization.jsonObject(with: XCTUnwrap(command.input)) as? [String: Any])
    }

    private func eventually(_ predicate: () async -> Bool) async throws {
        for _ in 0..<200 {
            if await predicate() { return }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("The asynchronous settings operation did not finish.")
    }
}

private actor SettingsBackend {
    private var replies: [SettingsCommandResult]
    private var recorded: [SettingsCommand] = []
    private var holdFirstApply: Bool
    private var release: CheckedContinuation<Void, Never>?
    private var holdFirstExternal: Bool
    private var externalRelease: CheckedContinuation<Void, Never>?

    init(replies: [SettingsCommandResult], holdFirstApply: Bool = false, holdFirstExternal: Bool = false) {
        self.replies = replies
        self.holdFirstApply = holdFirstApply
        self.holdFirstExternal = holdFirstExternal
    }

    func commands() -> [SettingsCommand] { recorded }

    func run(_ command: SettingsCommand) async throws -> SettingsCommandResult {
        recorded.append(command)
        guard !replies.isEmpty else {
            throw SettingsFailure(
                code: "unexpected_command", message: "Unexpected test command.", path: [])
        }
        let response = replies.removeFirst()
        if command.arguments == ["config", "apply"] && holdFirstApply {
            holdFirstApply = false
            await withCheckedContinuation { release = $0 }
        }
        if (command.arguments.first == "auth" || command.arguments.first == "models") && holdFirstExternal {
            holdFirstExternal = false
            await withCheckedContinuation { externalRelease = $0 }
        }
        return response
    }

    func releaseApply() {
        release?.resume()
        release = nil
    }

    func releaseExternal() {
        externalRelease?.resume()
        externalRelease = nil
    }
}
