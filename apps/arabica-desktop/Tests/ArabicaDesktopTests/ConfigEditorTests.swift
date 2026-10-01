import XCTest
@testable import ArabicaDesktop

final class ConfigEditorTests: XCTestCase {
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
}
