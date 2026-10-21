import Combine
import XCTest

@testable import ArabicaDesktop

@MainActor
final class SidebarLibraryTests: XCTestCase {
    func testProjectRegistryPersistsNormalizedDistinctPathsInOrder() throws {
        try withDefaults { defaults in
            let library = SidebarLibrary(defaults: defaults, storageKey: "library")
            library.addProject("/tmp/arabica/../first/")
            library.addProject("/tmp/first")
            library.addProject("/tmp/second")
            library.addProject("")

            let restored = SidebarLibrary(defaults: defaults, storageKey: "library")
            XCTAssertEqual(restored.projectPaths, ["/tmp/first", "/tmp/second"])
            XCTAssertTrue(restored.hiddenProjectPaths.isEmpty)
        }
    }

    func testRemovingAndReaddingProjectPreservesSessionOrganization() throws {
        try withDefaults { defaults in
            let library = SidebarLibrary(defaults: defaults, storageKey: "library")
            library.addProject("/tmp/project")
            library.togglePinned("session")
            library.setArchived(true, for: "session")
            library.removeProject("/tmp/project/child/..")

            let hidden = SidebarLibrary(defaults: defaults, storageKey: "library")
            XCTAssertTrue(hidden.projectPaths.isEmpty)
            XCTAssertEqual(hidden.hiddenProjectPaths, ["/tmp/project"])
            XCTAssertTrue(hidden.isPinned("session"))
            XCTAssertTrue(hidden.isArchived("session"))

            hidden.addProject("/tmp/project/")
            let restored = SidebarLibrary(defaults: defaults, storageKey: "library")
            XCTAssertEqual(restored.projectPaths, ["/tmp/project"])
            XCTAssertTrue(restored.hiddenProjectPaths.isEmpty)
            XCTAssertTrue(restored.isPinned("session"))
            XCTAssertTrue(restored.isArchived("session"))
        }
    }

    func testInferredProjectCanBeHiddenWithoutPriorRegistration() throws {
        try withDefaults { defaults in
            let library = SidebarLibrary(defaults: defaults, storageKey: "library")
            library.removeProject("/tmp/inferred")
            library.removeProject("/tmp/inferred/")
            library.removeProject("  ")

            let restored = SidebarLibrary(defaults: defaults, storageKey: "library")
            XCTAssertTrue(restored.projectPaths.isEmpty)
            XCTAssertEqual(restored.hiddenProjectPaths, ["/tmp/inferred"])
        }
    }

    func testArchiveAndRestoreRetainsPinAndPinTogglePersists() throws {
        try withDefaults { defaults in
            let library = SidebarLibrary(defaults: defaults, storageKey: "library")
            library.togglePinned("session")
            library.setArchived(true, for: "session")
            library.setArchived(true, for: "session")
            library.setArchived(false, for: "session")
            library.togglePinned("")
            library.setArchived(true, for: " \n")

            let restored = SidebarLibrary(defaults: defaults, storageKey: "library")
            XCTAssertTrue(restored.isPinned("session"))
            XCTAssertFalse(restored.isArchived("session"))
            XCTAssertEqual(restored.pinnedSessionIDs, ["session"])
            XCTAssertTrue(restored.archivedSessionIDs.isEmpty)

            restored.togglePinned("session")
            XCTAssertTrue(SidebarLibrary(defaults: defaults, storageKey: "library").pinnedSessionIDs.isEmpty)
        }
    }

    func testCorruptStorageDefaultsToEmptyAndAcceptsLaterUpdates() throws {
        try withDefaults { defaults in
            defaults.set(Data("invalid JSON".utf8), forKey: "library")
            let library = SidebarLibrary(defaults: defaults, storageKey: "library")
            XCTAssertTrue(library.projectPaths.isEmpty)
            XCTAssertTrue(library.hiddenProjectPaths.isEmpty)
            XCTAssertTrue(library.pinnedSessionIDs.isEmpty)
            XCTAssertTrue(library.archivedSessionIDs.isEmpty)

            library.addProject("/tmp/recovered")
            XCTAssertEqual(
                SidebarLibrary(defaults: defaults, storageKey: "library").projectPaths, ["/tmp/recovered"])
        }
    }

    func testMalformedFieldDoesNotDiscardOtherRestoredMetadata() throws {
        try withDefaults { defaults in
            defaults.set(
                Data(
                    """
                    {"projectPaths": ["/tmp/a", "/tmp/a/", "/tmp/hidden", ""],
                     "hiddenProjectPaths": ["/tmp/hidden/"], "pinnedSessionIDs": 42,
                     "archivedSessionIDs": ["saved", "saved", ""], "futureField": true}
                    """.utf8), forKey: "library")
            let library = SidebarLibrary(defaults: defaults, storageKey: "library")
            XCTAssertEqual(library.projectPaths, ["/tmp/a"])
            XCTAssertEqual(library.hiddenProjectPaths, ["/tmp/hidden"])
            XCTAssertTrue(library.pinnedSessionIDs.isEmpty)
            XCTAssertEqual(library.archivedSessionIDs, ["saved"])
        }
    }

    func testInjectedStorageKeyIsolatedFromOtherLibrariesAndPreferences() throws {
        try withDefaults { defaults in
            defaults.set("/tmp/current-workspace", forKey: "lastWorkspace")
            let first = SidebarLibrary(defaults: defaults, storageKey: "first")
            let second = SidebarLibrary(defaults: defaults, storageKey: "second")
            first.addProject("/tmp/first")
            first.togglePinned("session")
            second.addProject("/tmp/second")
            second.setArchived(true, for: "other-session")

            let restoredFirst = SidebarLibrary(defaults: defaults, storageKey: "first")
            let restoredSecond = SidebarLibrary(defaults: defaults, storageKey: "second")
            XCTAssertEqual(restoredFirst.projectPaths, ["/tmp/first"])
            XCTAssertEqual(restoredFirst.pinnedSessionIDs, ["session"])
            XCTAssertTrue(restoredFirst.archivedSessionIDs.isEmpty)
            XCTAssertEqual(restoredSecond.projectPaths, ["/tmp/second"])
            XCTAssertTrue(restoredSecond.pinnedSessionIDs.isEmpty)
            XCTAssertEqual(restoredSecond.archivedSessionIDs, ["other-session"])
            XCTAssertEqual(defaults.string(forKey: "lastWorkspace"), "/tmp/current-workspace")
        }
    }

    func testActivityProtectsTheUnionOfIndependentWindowOwners() {
        let library = SidebarLibrary.testInstance()
        let first = UUID()
        let second = UUID()
        library.updateActivity(
            owner: first, sessionIDs: ["first", "shared"], projectPaths: ["/tmp/a", "/tmp/shared/"])
        library.updateActivity(
            owner: second, sessionIDs: ["second", "shared"], projectPaths: ["/tmp/b", "/tmp/shared"])
        XCTAssertEqual(library.busySessionIDs, ["first", "second", "shared"])
        XCTAssertEqual(library.protectedProjectPaths, ["/tmp/a", "/tmp/b", "/tmp/shared"])

        library.clearActivity(owner: first)
        XCTAssertEqual(library.busySessionIDs, ["second", "shared"])
        XCTAssertEqual(library.protectedProjectPaths, ["/tmp/b", "/tmp/shared"])

        library.clearActivity(owner: second)
        XCTAssertTrue(library.busySessionIDs.isEmpty)
        XCTAssertTrue(library.protectedProjectPaths.isEmpty)
    }

    func testActivityUpdatesReplaceOnlyTheirOwnerAndIgnoreInvalidValues() {
        let library = SidebarLibrary.testInstance()
        let first = UUID()
        let second = UUID()
        library.updateActivity(owner: first, sessionIDs: ["old"], projectPaths: ["/tmp/old"])
        library.updateActivity(owner: second, sessionIDs: ["retained"], projectPaths: ["/tmp/retained"])
        library.updateActivity(owner: first, sessionIDs: ["new", ""], projectPaths: ["/tmp/child/../new", " "])
        XCTAssertEqual(library.busySessionIDs, ["new", "retained"])
        XCTAssertEqual(library.protectedProjectPaths, ["/tmp/new", "/tmp/retained"])

        library.updateActivity(owner: first, sessionIDs: [], projectPaths: [])
        library.clearActivity(owner: UUID())
        XCTAssertEqual(library.busySessionIDs, ["retained"])
        XCTAssertEqual(library.protectedProjectPaths, ["/tmp/retained"])
    }

    func testActivityPublishesOnlyWhenTheCombinedProtectionChanges() {
        let library = SidebarLibrary.testInstance()
        let first = UUID()
        let second = UUID()
        var sessionUpdates = 0
        var projectUpdates = 0
        let sessions = library.$busySessionIDs.dropFirst().sink { _ in sessionUpdates += 1 }
        let projects = library.$protectedProjectPaths.dropFirst().sink { _ in projectUpdates += 1 }
        library.updateActivity(owner: first, sessionIDs: ["shared"], projectPaths: ["/tmp/shared"])
        library.updateActivity(owner: first, sessionIDs: ["shared"], projectPaths: ["/tmp/shared/"])
        library.updateActivity(owner: second, sessionIDs: ["shared"], projectPaths: ["/tmp/shared"])
        library.clearActivity(owner: first)
        XCTAssertEqual(sessionUpdates, 1)
        XCTAssertEqual(projectUpdates, 1)

        library.clearActivity(owner: second)
        XCTAssertEqual(sessionUpdates, 2)
        XCTAssertEqual(projectUpdates, 2)
        withExtendedLifetime([sessions, projects]) {}
    }

    func testActivityIsNotRestoredWithPersistentLibraryMetadata() throws {
        try withDefaults { defaults in
            let library = SidebarLibrary(defaults: defaults, storageKey: "library")
            library.addProject("/tmp/saved")
            library.updateActivity(owner: UUID(), sessionIDs: ["running"], projectPaths: ["/tmp/busy"])
            library.togglePinned("pinned")

            let restored = SidebarLibrary(defaults: defaults, storageKey: "library")
            XCTAssertEqual(restored.projectPaths, ["/tmp/saved"])
            XCTAssertTrue(restored.isPinned("pinned"))
            XCTAssertTrue(restored.busySessionIDs.isEmpty)
            XCTAssertTrue(restored.protectedProjectPaths.isEmpty)
        }
    }

    private func withDefaults(_ body: (UserDefaults) throws -> Void) throws {
        let suiteName = "ArabicaDesktopTests.SidebarLibrary.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        try body(defaults)
    }
}
