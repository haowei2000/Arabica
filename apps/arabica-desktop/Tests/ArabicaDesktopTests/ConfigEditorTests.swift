import XCTest
@testable import ArabicaDesktop

final class ConfigEditorTests: XCTestCase {
    func testChatGPTAuthConfigDoesNotPersistAPIKeySettings() throws {
        let source = """
        [providers.primary]
        api_type = "open_ai_responses"
        base_url = "https://api.openai.com/v1"
        api_key_env = "ARABICA_PROVIDER_PRIMARY_API_KEY"
        api_key = "old-secret"

        [models.default]
        provider = "primary"
        model_id = "gpt-6.1-sol"

        [blend]
        default_model = "default"
        """
        let result = try ConfigDocument(source).updating(
            oldProviderSection: "providers.primary",
            newProviderSection: "providers.primary",
            oldModelSection: "models.default",
            newModelSection: "models.default",
            providerValues: [
                "auth": ConfigDocument.quote("chatgpt"),
                "api_type": ConfigDocument.quote("open_ai_responses"),
                "base_url": ConfigDocument.quote("https://api.openai.com/v1"),
            ],
            modelValues: [
                "provider": ConfigDocument.quote("primary"),
                "model_id": ConfigDocument.quote("gpt-6.1-sol"),
            ],
            thinking: "",
            defaultPolicy: "",
            defaultModelAlias: "default",
            newAPIKey: nil,
            removeAPIKey: true,
            removeAPIKeyEnv: true
        )

        let provider = ConfigDocument(result).values(in: "providers.primary")
        XCTAssertEqual(provider["auth"], "chatgpt")
        XCTAssertNil(provider["api_key"])
        XCTAssertNil(provider["api_key_env"])
        XCTAssertEqual(ConfigDocument(result).values(in: "models.default")["model_id"], "gpt-6.1-sol")
    }

    private let editableConfig = """
    [providers.fast]
    api_type = "open_ai_responses"
    base_url = "https://fast.example.test/v1"
    api_key_env = "FAST_API_KEY"

    [providers.strong]
    api_type = "anthropic_messages"
    base_url = "https://strong.example.test/v1"
    api_key_env = "STRONG_API_KEY"

    [models.default]
    provider = "fast"
    model_id = "fast-v1"

    [models.quick]
    provider = "fast"
    model_id = "quick-v1"

    [models.deep]
    provider = "strong"
    model_id = "deep-v1"

    [blend]
    default_policy = "coding"

    [blend.policies.coding]
    default_model = "default"
    after_tool_error = "deep"

    [blend.policies.review]
    default_model = "deep"
    after_tool_success = "quick"
    """

    @MainActor
    private func withEditor(_ body: (ConfigEditor, URL) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("config.toml")
        try editableConfig.write(to: url, atomically: true, encoding: .utf8)
        try body(ConfigEditor(configURL: url), url)
    }

    @MainActor
    private func withFreshEditor(_ body: (ConfigEditor, URL) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("config.toml")
        try body(ConfigEditor(configURL: url), url)
    }

    @MainActor
    func testSelectingEntriesWithoutChangesNeverWritesConfiguration() throws {
        try withEditor { editor, url in
            XCTAssertFalse(editor.hasUnsavedChanges)
            editor.selectProvider("strong")
            XCTAssertEqual(editor.providerName, "strong")
            XCTAssertEqual(editor.modelProvider, "strong")
            XCTAssertEqual(editor.modelAlias, "deep")
            editor.selectModel("quick")
            XCTAssertEqual(editor.providerName, "fast")
            XCTAssertEqual(editor.modelID, "quick-v1")
            XCTAssertFalse(editor.hasUnsavedChanges)
            XCTAssertNil(editor.pendingAction)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testSwitchingWithChangesWaitsForExplicitDecisionAndCanDiscard() throws {
        try withEditor { editor, url in
            editor.modelID = "unsaved-v2"
            editor.selectProvider("strong")
            XCTAssertEqual(editor.providerName, "fast")
            XCTAssertEqual(editor.modelID, "unsaved-v2")
            XCTAssertNotNil(editor.pendingAction)
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)

            editor.pendingAction = nil
            XCTAssertEqual(editor.modelID, "unsaved-v2")
            XCTAssertTrue(editor.hasUnsavedChanges)
            editor.selectProvider("strong")
            editor.discardAndContinue()
            XCTAssertEqual(editor.providerName, "strong")
            XCTAssertEqual(editor.modelID, "deep-v1")
            XCTAssertFalse(editor.hasUnsavedChanges)
            XCTAssertNil(editor.pendingAction)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testSaveAndContinuePersistsDraftBeforeSelectingAnotherModel() throws {
        try withEditor { editor, url in
            editor.modelID = "saved-v2"
            editor.selectModel("quick")
            editor.saveAndContinue()
            XCTAssertEqual(editor.modelAlias, "quick")
            XCTAssertEqual(editor.modelID, "quick-v1")
            XCTAssertFalse(editor.hasUnsavedChanges)
            let document = ConfigDocument(try String(contentsOf: url, encoding: .utf8))
            XCTAssertEqual(document.values(in: "models.default")["model_id"], "saved-v2")
        }
    }

    @MainActor
    func testFailedSaveKeepsTheDraftAndDoesNotContinueNavigation() throws {
        try withEditor { editor, url in
            editor.modelID = ""
            editor.selectProvider("strong")
            editor.saveAndContinue()
            XCTAssertEqual(editor.providerName, "fast")
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertEqual(editor.statusKind, .error)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testReloadRequiresExplicitDiscardWhenTheDraftHasChanges() throws {
        try withEditor { editor, _ in
            editor.modelID = "unsaved"
            editor.requestReload()
            XCTAssertEqual(editor.modelID, "unsaved")
            XCTAssertNotNil(editor.pendingAction)
            editor.discardAndContinue()
            XCTAssertEqual(editor.modelID, "fast-v1")
            XCTAssertFalse(editor.hasUnsavedChanges)
        }
    }

    @MainActor
    func testDefaultPolicyChangeIsAnExplicitSavableDraftAndPreservesRoutes() throws {
        try withEditor { editor, url in
            editor.selectPolicy("review")
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
            XCTAssertTrue(editor.save())
            XCTAssertFalse(editor.hasUnsavedChanges)
            let document = ConfigDocument(try String(contentsOf: url, encoding: .utf8))
            XCTAssertEqual(document.values(in: "blend")["default_policy"], "review")
            XCTAssertEqual(document.values(in: "blend.policies.review")["default_model"], "deep")
            XCTAssertEqual(document.values(in: "blend.policies.review")["after_tool_success"], "quick")
            XCTAssertEqual(document.values(in: "blend.policies.coding")["after_tool_error"], "deep")
        }
    }

    @MainActor
    func testSaveRejectsConfigurationChangedByAnExternalEditor() throws {
        try withEditor { editor, url in
            editor.modelID = "unsaved-v2"
            let external = editableConfig + "\n# External edit\n"
            try external.write(to: url, atomically: true, encoding: .utf8)
            XCTAssertFalse(editor.save())
            XCTAssertEqual(editor.statusKind, .error)
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), external)
        }
    }

    @MainActor
    func testChangingAPIProtocolResetsUnsupportedReasoningAndURLNeedsHostname() throws {
        try withEditor { editor, url in
            editor.thinking = "high"
            editor.setAPIType("anthropic_messages")
            XCTAssertEqual(editor.thinking, "")
            editor.baseURL = "https:/v1"
            XCTAssertFalse(editor.save())
            XCTAssertEqual(editor.statusKind, .error)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testRemovingTheOnlyCredentialRequiresAReplacement() throws {
        try withEditor { editor, url in
            let source = editableConfig.replacingOccurrences(
                of: "api_key_env = \"FAST_API_KEY\"",
                with: "api_key = \"test-only-key\""
            )
            try source.write(to: url, atomically: true, encoding: .utf8)
            editor.reload()
            XCTAssertTrue(editor.hasSavedKey)
            editor.apiKeyEnv = ""
            editor.clearSavedKey = true
            XCTAssertFalse(editor.save())
            XCTAssertEqual(editor.statusKind, .error)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), source)
        }
    }

    @MainActor
    func testAuthModeParticipatesInDirtyChangesAndCanBeSavedWithoutAPIKeySettings() throws {
        try withEditor { editor, url in
            editor.authMode = "chatgpt"
            XCTAssertTrue(editor.hasUnsavedChanges)
            editor.selectProvider("strong")
            XCTAssertEqual(editor.providerName, "fast")
            XCTAssertNotNil(editor.pendingAction)
            editor.pendingAction = nil

            editor.selectAuthMode("chatgpt")
            editor.apiKey = "must-not-persist"
            XCTAssertTrue(editor.save())
            XCTAssertFalse(editor.hasUnsavedChanges)
            let saved = ConfigDocument(try String(contentsOf: url, encoding: .utf8))
            let provider = saved.values(in: "providers.fast")
            XCTAssertEqual(provider["auth"], "chatgpt")
            XCTAssertNil(provider["api_key"])
            XCTAssertNil(provider["api_key_env"])
            XCTAssertEqual(editor.authMode, "chatgpt")
        }
    }

    @MainActor
    func testSelectingChatGPTAuthOffersSaveAndSignInForTheDraft() throws {
        try withEditor { editor, url in
            XCTAssertEqual(editor.chatGPTAuthButtonTitle, "Sign in")
            XCTAssertTrue(editor.canChangeChatGPTConnection)

            editor.selectAuthMode("chatgpt")

            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertEqual(editor.chatGPTAuthButtonTitle, "Save & Sign in")
            XCTAssertTrue(editor.canChangeChatGPTConnection)
            XCTAssertTrue(editor.chatGPTAuthHelp.lowercased().contains("save"))
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testFreshChatGPTLoginBootstrapsWithoutInventingAModelAndLoadsTheGeneratedCatalog() throws {
        try withFreshEditor { editor, url in
            editor.selectAuthMode("chatgpt")
            XCTAssertEqual(editor.modelID, "")
            XCTAssertEqual(editor.chatGPTAuthButtonTitle, "Sign in")
            let preparation = try XCTUnwrap(editor.prepareChatGPTAuth(
                executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))
            XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
            XCTAssertEqual(editor.modelID, "")

            try editableConfig.write(to: url, atomically: true, encoding: .utf8)
            editor.completeConfigurationUpdate(successMessage: "Connected.", authPreparation: preparation)

            XCTAssertEqual(editor.modelID, "fast-v1")
            XCTAssertEqual(editor.modelOptions, ["deep", "default", "quick"])
            XCTAssertFalse(editor.hasUnsavedChanges)
            XCTAssertEqual(editor.status, "Connected.")
        }
    }

    @MainActor
    func testFreshChatGPTLoginPreservesEditsMadeWhileAuthenticationRunsAndKeepsConflictProtection() throws {
        try withFreshEditor { editor, url in
            editor.selectAuthMode("chatgpt")
            let preparation = try XCTUnwrap(editor.prepareChatGPTAuth(
                executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))
            editor.modelID = "unsaved-during-login"
            try editableConfig.write(to: url, atomically: true, encoding: .utf8)

            editor.completeConfigurationUpdate(successMessage: "Connected.", authPreparation: preparation)

            XCTAssertEqual(editor.modelID, "unsaved-during-login")
            XCTAssertEqual(editor.authMode, "chatgpt")
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertTrue(editor.status.contains("unsaved edits were kept"))
            XCTAssertFalse(editor.save())
            XCTAssertTrue(editor.status.contains("changed on disk"))
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testFreshChatGPTLoginDoesNotBypassValidationForEditsBeforeOrAfterSelectingAuthentication() throws {
        for editBeforeSelection in [true, false] {
            try withFreshEditor { editor, url in
                if editBeforeSelection { editor.modelID = " " }
                editor.selectAuthMode("chatgpt")
                if !editBeforeSelection { editor.modelID = " " }

                XCTAssertNil(editor.prepareChatGPTAuth(
                    executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))

                XCTAssertEqual(editor.statusKind, .error)
                XCTAssertEqual(editor.modelID, " ")
                XCTAssertTrue(editor.hasUnsavedChanges)
                XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
            }
        }
    }

    @MainActor
    func testFreshChatGPTLoginDoesNotReplaceConfigurationCreatedByAnExternalEditor() throws {
        try withFreshEditor { editor, url in
            editor.selectAuthMode("chatgpt")
            try editableConfig.write(to: url, atomically: true, encoding: .utf8)

            XCTAssertNil(editor.prepareChatGPTAuth(
                executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))

            XCTAssertEqual(editor.statusKind, .error)
            XCTAssertTrue(editor.status.contains("changed on disk"))
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testSavedChatGPTLoginPreservesNewEditsWhenItsModelCatalogUpdateCompletes() throws {
        try withEditor { editor, url in
            editor.selectAuthMode("chatgpt")
            let preparation = try XCTUnwrap(editor.prepareChatGPTAuth(
                executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))
            let saved = try String(contentsOf: url, encoding: .utf8)
            let updated = saved.replacingOccurrences(of: "fast-v1", with: "synced-model")
            editor.modelID = "new-unsaved-model"
            try updated.write(to: url, atomically: true, encoding: .utf8)

            editor.completeConfigurationUpdate(successMessage: "Connected.", authPreparation: preparation)

            XCTAssertEqual(editor.modelID, "new-unsaved-model")
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertFalse(editor.save())
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), updated)
        }
    }

    @MainActor
    func testSelectingChatGPTClearsUnsupportedThinkingBeforeSavingAndSigningIn() throws {
        for (apiType, thinking) in [("anthropic_messages", "off"), ("open_ai_chat_completions", "on")] {
            try withEditor { editor, url in
                editor.setAPIType(apiType)
                editor.thinking = thinking
                XCTAssertTrue(editor.save())
                editor.selectAuthMode("chatgpt")

                XCTAssertEqual(editor.thinking, "")
                XCTAssertNotNil(editor.prepareChatGPTAuth(
                    executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))

                let provider = ConfigDocument(try String(contentsOf: url, encoding: .utf8)).values(in: "providers.fast")
                XCTAssertEqual(provider["auth"], "chatgpt")
                XCTAssertEqual(provider["api_type"], "open_ai_responses")
                XCTAssertNil(provider["thinking"])
                XCTAssertFalse(editor.hasUnsavedChanges)
            }
        }
    }

    @MainActor
    func testSignInPreparationSavesTheDraftAndRemovesAPIKeySettings() throws {
        try withEditor { editor, url in
            let source = editableConfig.replacingOccurrences(
                of: "api_key_env = \"FAST_API_KEY\"",
                with: "api_key_env = \"FAST_API_KEY\"\napi_key = \"test-only-key\""
            )
            try source.write(to: url, atomically: true, encoding: .utf8)
            editor.reload()
            editor.selectAuthMode("chatgpt")
            editor.modelID = "saved-before-sign-in"

            XCTAssertNotNil(editor.prepareChatGPTAuth(
                executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))

            XCTAssertFalse(editor.hasUnsavedChanges)
            XCTAssertFalse(editor.isChatGPTAuthRunning)
            let document = ConfigDocument(try String(contentsOf: url, encoding: .utf8))
            let provider = document.values(in: "providers.fast")
            XCTAssertEqual(provider["auth"], "chatgpt")
            XCTAssertNil(provider["api_key"])
            XCTAssertNil(provider["api_key_env"])
            XCTAssertEqual(document.values(in: "models.default")["model_id"], "saved-before-sign-in")
            XCTAssertEqual(document.values(in: "providers.strong")["api_key_env"], "STRONG_API_KEY")
            XCTAssertEqual(document.values(in: "blend.policies.coding")["after_tool_error"], "deep")
            XCTAssertEqual(editor.chatGPTAuthButtonTitle, "Sign in")
        }
    }

    @MainActor
    func testSignInPreparationWithMissingExecutableDoesNotSaveTheDraft() throws {
        try withEditor { editor, url in
            editor.selectAuthMode("chatgpt")
            editor.modelID = "unsaved-sign-in"

            XCTAssertNil(editor.prepareChatGPTAuth(executable: nil, savingChanges: true))

            XCTAssertEqual(editor.statusKind, .error)
            XCTAssertTrue(editor.status.lowercased().contains("executable"))
            XCTAssertEqual(editor.modelID, "unsaved-sign-in")
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertFalse(editor.isChatGPTAuthRunning)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testSignInPreparationWithInvalidDraftDoesNotChangeTheFile() throws {
        try withEditor { editor, url in
            editor.selectAuthMode("chatgpt")
            editor.modelID = ""

            XCTAssertNil(editor.prepareChatGPTAuth(
                executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))

            XCTAssertEqual(editor.statusKind, .error)
            XCTAssertEqual(editor.modelID, "")
            XCTAssertEqual(editor.authMode, "chatgpt")
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertFalse(editor.isChatGPTAuthRunning)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testSignInPreparationPreservesExternalChangesAndTheDraftOnConflict() throws {
        try withEditor { editor, url in
            editor.selectAuthMode("chatgpt")
            editor.modelID = "unsaved-sign-in"
            let external = editableConfig + "\n# External edit\n"
            try external.write(to: url, atomically: true, encoding: .utf8)

            XCTAssertNil(editor.prepareChatGPTAuth(
                executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))

            XCTAssertEqual(editor.statusKind, .error)
            XCTAssertTrue(editor.status.contains("changed on disk"))
            XCTAssertEqual(editor.modelID, "unsaved-sign-in")
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertFalse(editor.isChatGPTAuthRunning)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), external)
        }
    }

    @MainActor
    func testBusyConfigurationOperationsPreventSignInPreparationAndSaving() throws {
        let busyStates: [ReferenceWritableKeyPath<ConfigEditor, Bool>] = [
            \.isSaving, \.isChatGPTAuthRunning, \.isSyncingModels,
        ]
        for busyState in busyStates {
            try withEditor { editor, url in
                editor.selectAuthMode("chatgpt")
                editor.modelID = "unsaved-sign-in"
                editor[keyPath: busyState] = true

                XCTAssertFalse(editor.canChangeChatGPTConnection)
                XCTAssertNil(editor.prepareChatGPTAuth(
                    executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: true))

                XCTAssertTrue(editor.hasUnsavedChanges)
                XCTAssertEqual(editor.modelID, "unsaved-sign-in")
                XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
            }
        }
    }

    @MainActor
    func testDisconnectWithUnsavedChangesRemainsUnavailableAndDoesNotSave() throws {
        try withEditor { editor, url in
            editor.chatGPTConnected = true
            editor.modelID = "unsaved-draft"

            XCTAssertEqual(editor.chatGPTAuthButtonTitle, "Disconnect")
            XCTAssertFalse(editor.canChangeChatGPTConnection)
            XCTAssertNil(editor.prepareChatGPTAuth(
                executable: URL(fileURLWithPath: "/tmp/arabica-test-cli"), savingChanges: false))

            XCTAssertTrue(editor.status.contains("Save or discard"))
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertEqual(editor.modelID, "unsaved-draft")
            XCTAssertFalse(editor.isChatGPTAuthRunning)
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    @MainActor
    func testBackgroundConfigurationUpdateKeepsNewDraftAndDiskConflictProtection() throws {
        try withEditor { editor, url in
            editor.modelID = "unsaved-draft"
            let updated = editableConfig.replacingOccurrences(of: "fast-v1", with: "synced-model")
            try updated.write(to: url, atomically: true, encoding: .utf8)

            editor.completeConfigurationUpdate(successMessage: "Models synchronized.")

            XCTAssertEqual(editor.modelID, "unsaved-draft")
            XCTAssertTrue(editor.hasUnsavedChanges)
            XCTAssertTrue(editor.status.contains("unsaved edits were kept"))
            XCTAssertFalse(editor.save())
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), updated)
        }
    }

    @MainActor
    func testBackgroundConfigurationUpdateReloadsACleanDraft() throws {
        try withEditor { editor, url in
            let updated = editableConfig.replacingOccurrences(of: "fast-v1", with: "synced-model")
            try updated.write(to: url, atomically: true, encoding: .utf8)

            editor.completeConfigurationUpdate(successMessage: "Models synchronized.")

            XCTAssertEqual(editor.modelID, "synced-model")
            XCTAssertFalse(editor.hasUnsavedChanges)
            XCTAssertEqual(editor.statusKind, .success)
        }
    }

    @MainActor
    func testModelSyncDoesNotStartWithUnsavedChanges() throws {
        try withEditor { editor, url in
            editor.modelID = "unsaved-draft"
            editor.syncModels()
            XCTAssertFalse(editor.isSyncingModels)
            XCTAssertTrue(editor.status.contains("Save or discard"))
            XCTAssertEqual(editor.modelID, "unsaved-draft")
            XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), editableConfig)
        }
    }

    func testLegacySingleProviderIsRewrittenAndSecretAndOtherSectionsArePreserved() throws {
        let source = """
        [provider]
        api_type = "open_ai_chat_completions"
        api_key = "do-not-display"
        base_url = "https://api.example.test/v1"
        model = "model-v1"
        thinking = "medium"

        [[mcp]]
        name = "tools"
        url = "https://mcp.example.test"
        """
        let result = try ConfigDocument(source).updating(
            oldProviderSection: "provider",
            newProviderSection: "providers.primary",
            oldModelSection: nil,
            newModelSection: "models.default",
            providerValues: [
                "api_type": ConfigDocument.quote("open_ai_chat_completions"),
                "base_url": ConfigDocument.quote("https://api.example.test/v1"),
                "api_key_env": ConfigDocument.quote("ARABICA_PROVIDER_PRIMARY_API_KEY"),
            ],
            modelValues: [
                "provider": ConfigDocument.quote("primary"),
                "model_id": ConfigDocument.quote("model-v2"),
            ],
            thinking: "high",
            defaultPolicy: "",
            defaultModelAlias: "default",
            newAPIKey: nil,
            removeAPIKey: false
        )

        XCTAssertTrue(result.contains("[providers.primary]"))
        XCTAssertTrue(result.contains("[models.default]"))
        XCTAssertTrue(result.contains("model_id = \"model-v2\""))
        XCTAssertTrue(result.contains("api_key = \"do-not-display\""))
        XCTAssertTrue(result.contains("[[mcp]]\nname = \"tools\""))
        XCTAssertFalse(result.contains("\nmodel = \"model-v1\""))
    }

    func testUpdatingNamedBlendPolicyPersistsOnlyTheDefaultPolicyAndItsDefaultAlias() throws {
        let source = """
        [providers.primary]
        api_type = "open_ai_responses"
        base_url = "https://api.example.test/v1"
        api_key_env = "ARABICA_PROVIDER_PRIMARY_API_KEY"

        [models.default]
        provider = "primary"
        model_id = "model-v1"

        [blend]
        default_policy = "coding"

        [blend.policies.coding]
        version = 1
        default_model = "default"
        after_tool_error = "default"

        [blend.policies.review]
        version = 1
        default_model = "default"
        """

        let result = try ConfigDocument(source).updating(
            oldProviderSection: "providers.primary",
            newProviderSection: "providers.primary",
            oldModelSection: "models.default",
            newModelSection: "models.default",
            providerValues: ["base_url": ConfigDocument.quote("https://api.example.test/v1")],
            modelValues: ["provider": ConfigDocument.quote("primary"), "model_id": ConfigDocument.quote("model-v2")],
            thinking: "",
            defaultPolicy: "review",
            defaultModelAlias: "default",
            newAPIKey: nil,
            removeAPIKey: false
        )

        XCTAssertTrue(result.contains("default_policy = \"review\""))
        XCTAssertTrue(result.contains("[blend.policies.review]\nversion = 1\ndefault_model = \"default\""))
        XCTAssertTrue(result.contains("[blend.policies.coding]\nversion = 1\ndefault_model = \"default\"\nafter_tool_error = \"default\""))
        XCTAssertFalse(result.contains("[blend]\ndefault_policy = \"review\"\ndefault_model"))
    }

    func testUpdatingOneModelPreservesOtherCatalogEntriesAndIndependentPolicyDefault() throws {
        let source = """
        [providers.fast]
        api_type = "open_ai_responses"
        base_url = "https://fast.example.test/v1"

        [providers.strong]
        api_type = "anthropic_messages"
        base_url = "https://strong.example.test/v1"

        [models.quick]
        provider = "fast"
        model_id = "quick-v1"

        [models.deep]
        provider = "strong"
        model_id = "deep-v1"

        [blend]
        default_policy = "coding"

        [blend.policies.coding]
        default_model = "deep"
        after_tool_error = "quick"
        """

        let result = try ConfigDocument(source).updating(
            oldProviderSection: "providers.fast",
            newProviderSection: "providers.fast",
            oldModelSection: "models.quick",
            newModelSection: "models.quick",
            providerValues: ["base_url": ConfigDocument.quote("https://fast.example.test/v2")],
            modelValues: ["provider": ConfigDocument.quote("fast"), "model_id": ConfigDocument.quote("quick-v2")],
            thinking: "",
            defaultPolicy: "coding",
            defaultModelAlias: "deep",
            newAPIKey: nil,
            removeAPIKey: false
        )

        XCTAssertTrue(result.contains("[providers.strong]\napi_type = \"anthropic_messages\"\nbase_url = \"https://strong.example.test/v1\""))
        XCTAssertTrue(result.contains("[models.deep]\nprovider = \"strong\"\nmodel_id = \"deep-v1\""))
        XCTAssertTrue(result.contains("[models.quick]\nprovider = \"fast\"\nmodel_id = \"quick-v2\""))
        XCTAssertTrue(result.contains("[blend.policies.coding]\ndefault_model = \"deep\"\nafter_tool_error = \"quick\""))
    }

    func testSavingDefaultPolicyPreservesEveryRouteAndUnrelatedSetting() throws {
        let source = """
        [providers.fast]
        base_url = "https://fast.example.test/v1"

        [models.quick]
        provider = "fast"
        model_id = "quick-model"

        [models.deep]
        provider = "fast"
        model_id = "deep-model"

        [blend]
        default_policy = "coding"

        [blend.policies.coding]
        version = 1
        default_model = "quick"
        after_tool_error = "deep"

        [blend.policies.review]
        version = 1
        default_model = "deep"
        after_tool_success = "quick"

        [[mcp]]
        name = "docs"
        url = "https://mcp.example.test"
        """

        let result = try ConfigDocument(source).settingDefaultPolicy("review")

        XCTAssertTrue(result.contains("default_policy = \"review\""))
        XCTAssertTrue(result.contains("[blend.policies.coding]\nversion = 1\ndefault_model = \"quick\"\nafter_tool_error = \"deep\""))
        XCTAssertTrue(result.contains("[blend.policies.review]\nversion = 1\ndefault_model = \"deep\"\nafter_tool_success = \"quick\""))
        XCTAssertTrue(result.contains("[[mcp]]\nname = \"docs\""))
    }

    func testTOMLStringQuotingEscapesQuotesAndNewlines() {
        XCTAssertEqual(ConfigDocument.quote("a\"b\\c"), "\"a\\\"b\\\\c\"")
    }

    @MainActor
    func testBlankAPIKeyVariableIsOmittedWhenUsingASavedKey() throws {
        try withEditor { editor, url in
            let source = editableConfig.replacingOccurrences(
                of: "api_key_env = \"FAST_API_KEY\"",
                with: "api_key = \"test-only-key\""
            )
            try source.write(to: url, atomically: true, encoding: .utf8)
            editor.reload()
            editor.apiKeyEnv = "  "
            XCTAssertTrue(editor.save())

            let saved = ConfigDocument(try String(contentsOf: url, encoding: .utf8))
            XCTAssertNil(saved.values(in: "providers.fast")["api_key_env"])
            XCTAssertEqual(saved.values(in: "providers.fast")["api_key"], "test-only-key")
            XCTAssertEqual(saved.values(in: "providers.strong")["api_key_env"], "STRONG_API_KEY")
        }
    }

    @MainActor
    func testClearingAPIKeyVariableRemovesTheExistingKeyWhenProvidingAReplacementCredential() throws {
        try withEditor { editor, url in
            editor.apiKeyEnv = ""
            editor.apiKey = "replacement-test-only-key"
            XCTAssertTrue(editor.save())
            let saved = ConfigDocument(try String(contentsOf: url, encoding: .utf8))
            XCTAssertNil(saved.values(in: "providers.fast")["api_key_env"])
            XCTAssertEqual(saved.values(in: "providers.fast")["api_key"], "replacement-test-only-key")
        }
    }

    @MainActor
    func testSavingHeadersWithInlineCommentsPreservesTableBoundaries() throws {
        try withEditor { editor, url in
            let source = editableConfig
                .replacingOccurrences(of: "[providers.fast]", with: "[providers.fast] # Primary provider")
                .replacingOccurrences(of: "[providers.strong]", with: "[providers.strong] # Other provider")
                .replacingOccurrences(of: "[models.default]", with: "[models.default] # Main model")
                .replacingOccurrences(of: "[blend]", with: "[blend] # Routing")
            try source.write(to: url, atomically: true, encoding: .utf8)
            editor.reload()
            editor.modelID = "updated-model"
            XCTAssertTrue(editor.save())

            let text = try String(contentsOf: url, encoding: .utf8)
            let saved = ConfigDocument(text)
            XCTAssertEqual(saved.sectionNames.filter { $0 == "providers.fast" }.count, 1)
            XCTAssertEqual(saved.values(in: "models.default")["model_id"], "updated-model")
            XCTAssertEqual(saved.values(in: "providers.strong")["api_type"], "anthropic_messages")
            XCTAssertEqual(saved.values(in: "blend")["default_policy"], "coding")
            XCTAssertTrue(text.contains("[providers.fast] # Primary provider"))
            XCTAssertTrue(text.contains("[models.default] # Main model"))
        }
    }

    @MainActor
    func testQuotedCatalogNamesAreDecodedAndSavedAsSingleTableKeys() throws {
        try withEditor { editor, url in
            let source = #"""
            ["providers" . "cloud.#primary"] # Provider with punctuation
            api_type = "open_ai_responses"
            base_url = "https://example.test/v1"
            api_key_env = "TEST_API_KEY"

            [models.'fast.v1'] # Dotted alias
            provider = "cloud.#primary"
            model_id = "model-\U0001F600"

            [blend]
            default_policy = "code.review"

            [blend.policies.'code.review'] # Dotted policy
            default_model = "fast.v1"
            """#
            try source.write(to: url, atomically: true, encoding: .utf8)
            editor.reload()

            XCTAssertEqual(editor.providerOptions, ["cloud.#primary"])
            XCTAssertEqual(editor.providerName, "cloud.#primary")
            XCTAssertEqual(editor.modelOptions, ["fast.v1"])
            XCTAssertEqual(editor.modelAlias, "fast.v1")
            XCTAssertEqual(editor.modelID, "model-😀")
            XCTAssertEqual(editor.policyOptions, ["code.review"])
            editor.modelID = "updated-model"
            XCTAssertTrue(editor.save())

            let text = try String(contentsOf: url, encoding: .utf8)
            let saved = ConfigDocument(text)
            XCTAssertEqual(saved.childSectionNames(in: "providers"), ["cloud.#primary"])
            XCTAssertEqual(saved.childSectionNames(in: "models"), ["fast.v1"])
            XCTAssertEqual(saved.values(in: ConfigDocument.tablePath("models", "fast.v1"))["model_id"], "updated-model")
            XCTAssertEqual(saved.configuredPolicies().map(\.id), ["code.review"])
            XCTAssertTrue(text.contains("[models.'fast.v1'] # Dotted alias"))
        }
    }

    func testTablePathsEscapeQuotedAndBackslashNamesAndMatchEquivalentQuotedKeys() {
        let provider = "team \"quoted\"\\local"
        let path = ConfigDocument.tablePath("providers", provider)
        XCTAssertEqual(path, #"providers."team \"quoted\"\\local""#)
        let document = ConfigDocument("[\(path)] # Keep this table\n\"api_key_env\" = \"TEST_API_KEY\"\n")
        XCTAssertEqual(document.childSectionNames(in: "\"providers\""), [provider])
        XCTAssertEqual(document.values(in: path)["api_key_env"], "TEST_API_KEY")
    }

    func testCommentedMCPHeadersRemainOutsideManagedProviderTables() throws {
        let source = """
        [providers.primary]
        api_type = "open_ai_responses"

        [[mcp]] # Tools
        name = "filesystem"
        command = "tool-host"
        [mcp.env] # Environment
        TEST_MODE = "true"
        """
        let document = ConfigDocument(source)
        XCTAssertNil(document.values(in: "providers.primary")["name"])
        XCTAssertEqual(document.mcpServers().map(\.name), ["filesystem"])
        XCTAssertEqual(document.mcpServers().first?.envKeys, ["TEST_MODE"])
    }

    @MainActor
    func testThinkingValidationMatchesProviderAPITypeSupport() {
        XCTAssertNotNil(ConfigEditor.thinkingValidationError(
            apiType: "open_ai_chat_completions",
            thinking: "medium"
        ))
        XCTAssertNil(ConfigEditor.thinkingValidationError(
            apiType: "open_ai_chat_completions",
            thinking: "on"
        ))
        XCTAssertNil(ConfigEditor.thinkingValidationError(
            apiType: "open_ai_responses",
            thinking: "medium"
        ))
        XCTAssertNotNil(ConfigEditor.thinkingValidationError(
            apiType: "anthropic_messages",
            thinking: "high"
        ))
        XCTAssertNil(ConfigEditor.thinkingValidationError(apiType: "anthropic_messages", thinking: ""))
    }

    func testMCPServersAndSkillRootsParsing() {
        let toml = """
        [skills]
        roots = ["skills", "/opt/custom/skills"]

        [[mcp]]
        name = "filesystem"
        command = "npx"
        args = ["-y", "@mcp/fs", "/workspace"]

        [mcp.env]
        NODE_ENV = "production"

        [[mcp]]
        name = "remote"
        url = "https://mcp.remote.test/sse"
        """

        let doc = ConfigDocument(toml)
        let mcp = doc.mcpServers()
        XCTAssertEqual(mcp.count, 2)
        XCTAssertEqual(mcp[0].name, "filesystem")
        XCTAssertEqual(mcp[0].command, "npx")
        XCTAssertEqual(mcp[0].args, ["-y", "@mcp/fs", "/workspace"])
        XCTAssertEqual(mcp[0].transportType, "stdio")
        XCTAssertEqual(mcp[0].envKeys, ["NODE_ENV"])

        XCTAssertEqual(mcp[1].name, "remote")
        XCTAssertEqual(mcp[1].url, "https://mcp.remote.test/sse")
        XCTAssertEqual(mcp[1].transportType, "http")

        let roots = doc.skillRoots(relativeTo: URL(fileURLWithPath: "/tmp/arabica"))
        XCTAssertEqual(roots.count, 2)
        XCTAssertEqual(roots[0].rawPath, "skills")
        XCTAssertEqual(roots[0].resolvedURL.path, "/tmp/arabica/skills")
        XCTAssertEqual(roots[1].rawPath, "/opt/custom/skills")
        XCTAssertEqual(roots[1].resolvedURL.path, "/opt/custom/skills")
    }
}
