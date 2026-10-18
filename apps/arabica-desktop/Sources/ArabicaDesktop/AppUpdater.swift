import Combine
import Foundation
import Sparkle

/// Sparkle owns update scheduling, authenticated downloads, installation, and relaunch.
@MainActor
final class AppUpdater: NSObject, ObservableObject, SPUUpdaterDelegate {
    @Published private(set) var canCheckForUpdates = false
    @Published private(set) var automaticallyChecksForUpdates = false
    @Published private(set) var automaticallyDownloadsUpdates = false
    @Published private(set) var waitingForRuns = false

    private var controller: SPUStandardUpdaterController?
    private weak var model: DesktopModel?
    private var deferredInstall: (() -> Void)?
    private var activityObserver: AnyCancellable?

    var isAvailable: Bool { controller != nil }

    init(model: DesktopModel) {
        self.model = model
        super.init()
        #if !DEBUG
        // swift run and swift test have no application bundle or update feed.
        guard Bundle.main.bundleURL.pathExtension == "app",
              Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") is String,
              Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") is String else { return }
        let controller = SPUStandardUpdaterController(
            startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil
        )
        self.controller = controller
        controller.updater.publisher(for: \.canCheckForUpdates)
            .receive(on: DispatchQueue.main).assign(to: &$canCheckForUpdates)
        controller.updater.publisher(for: \.automaticallyChecksForUpdates)
            .receive(on: DispatchQueue.main).assign(to: &$automaticallyChecksForUpdates)
        controller.updater.publisher(for: \.automaticallyDownloadsUpdates)
            .receive(on: DispatchQueue.main).assign(to: &$automaticallyDownloadsUpdates)
        activityObserver = model.objectWillChange.sink { [weak self] in
            Task { @MainActor in self?.resumeInstallationWhenIdle() }
        }
        controller.startUpdater()
        #endif
    }

    func checkForUpdates() { controller?.checkForUpdates(nil) }

    func setAutomaticChecks(_ enabled: Bool) {
        controller?.updater.automaticallyChecksForUpdates = enabled
    }

    func setAutomaticDownloads(_ enabled: Bool) {
        controller?.updater.automaticallyDownloadsUpdates = enabled
    }

    func updater(
        _ updater: SPUUpdater,
        shouldPostponeRelaunchForUpdate item: SUAppcastItem,
        untilInvokingBlock installHandler: @escaping () -> Void
    ) -> Bool {
        guard model?.hasActiveRuns == true else { return false }
        deferredInstall = installHandler
        waitingForRuns = true
        return true
    }

    private func resumeInstallationWhenIdle() {
        guard model?.hasActiveRuns != true, let install = deferredInstall else { return }
        deferredInstall = nil
        waitingForRuns = false
        install()
    }

    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        deferredInstall = nil
        waitingForRuns = false
    }
}
