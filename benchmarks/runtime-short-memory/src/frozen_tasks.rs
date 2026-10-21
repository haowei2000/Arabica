use std::collections::{BTreeMap, BTreeSet};

use arabica_runtime::{
    AcceptanceCheck, AcceptanceCriterion, AcceptanceSpec, BlendRoutingPolicy, ModelPriceRule,
    PriceCard,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FrozenTaskCategory {
    ExactFileOperations,
    StructuredExtraction,
    SmallCodeFixes,
    MultistepWorkAndRecovery,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct FrozenTask {
    pub task_id: String,
    pub category: FrozenTaskCategory,
    pub prompt: String,
    pub acceptance: AcceptanceSpec,
    pub initial_files: BTreeMap<String, String>,
    pub reference_cost: f64,
    pub reference_time_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskSuiteSplit {
    Development,
    HeldOut,
    All,
}

/// The development partition of the frozen task suite (2 tasks per category = 8 tasks total).
pub fn development_task_suite() -> Vec<FrozenTask> {
    let all = frozen_task_suite();
    let mut by_cat = BTreeMap::<FrozenTaskCategory, Vec<FrozenTask>>::new();
    for t in all {
        by_cat.entry(t.category).or_default().push(t);
    }
    let mut dev = Vec::new();
    for (_cat, tasks) in by_cat {
        dev.extend(tasks.into_iter().take(2));
    }
    dev
}

/// The held-out validation partition of the frozen task suite (1 task per category = 4 tasks total).
pub fn held_out_task_suite() -> Vec<FrozenTask> {
    let all = frozen_task_suite();
    let mut by_cat = BTreeMap::<FrozenTaskCategory, Vec<FrozenTask>>::new();
    for t in all {
        by_cat.entry(t.category).or_default().push(t);
    }
    let mut held_out = Vec::new();
    for (_cat, tasks) in by_cat {
        if let Some(last) = tasks.into_iter().nth(2) {
            held_out.push(last);
        }
    }
    held_out
}

pub fn task_suite_for_split(split: TaskSuiteSplit) -> Vec<FrozenTask> {
    match split {
        TaskSuiteSplit::Development => development_task_suite(),
        TaskSuiteSplit::HeldOut => held_out_task_suite(),
        TaskSuiteSplit::All => frozen_task_suite(),
    }
}

/// The 12 frozen evaluation tasks, exactly 3 in each category.
pub fn frozen_task_suite() -> Vec<FrozenTask> {
    vec![
        // ==========================================
        // Category 1: Exact File Operations
        // ==========================================
        FrozenTask {
            task_id: "file-op-config-update".into(),
            category: FrozenTaskCategory::ExactFileOperations,
            prompt: "Update app_config.toml: change port to 8080 and enabled to true. Do not modify any other files.".into(),
            initial_files: BTreeMap::from([
                ("app_config.toml".into(), "port = 3000\nenabled = false\n".into()),
                ("notes.txt".into(), "Do not touch this file.\n".into()),
            ]),
            reference_cost: 0.05,
            reference_time_ms: 15_000,
            acceptance: AcceptanceSpec {
                task_id: "file-op-config-update".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "exact-config",
                        AcceptanceCheck::ExactFileContent {
                            path: "app_config.toml".into(),
                            expected: "port = 8080\nenabled = true\n".into(),
                        },
                    )
                    .with_weight(0.7)
                    .required(),
                    AcceptanceCriterion::new(
                        "permitted-files",
                        AcceptanceCheck::PermittedFiles {
                            allowed_paths: vec!["app_config.toml".into()],
                        },
                    )
                    .with_weight(0.3)
                    .required(),
                ],
            },
        },
        FrozenTask {
            task_id: "file-op-version-bump".into(),
            category: FrozenTaskCategory::ExactFileOperations,
            prompt: "Bump version to 2.1.0 in version.txt and add '## 2.1.0' header at the top of changelog.md. No other files may be modified.".into(),
            initial_files: BTreeMap::from([
                ("version.txt".into(), "2.0.0\n".into()),
                ("changelog.md".into(), "## 2.0.0\nInitial release\n".into()),
            ]),
            reference_cost: 0.06,
            reference_time_ms: 18_000,
            acceptance: AcceptanceSpec {
                task_id: "file-op-version-bump".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "exact-version",
                        AcceptanceCheck::ExactFileContent {
                            path: "version.txt".into(),
                            expected: "2.1.0\n".into(),
                        },
                    )
                    .with_weight(0.4)
                    .required(),
                    AcceptanceCriterion::new(
                        "changelog-contains",
                        AcceptanceCheck::ArtifactContains {
                            path: "changelog.md".into(),
                            value: "## 2.1.0\n".into(),
                        },
                    )
                    .with_weight(0.3)
                    .required(),
                    AcceptanceCriterion::new(
                        "permitted-files",
                        AcceptanceCheck::PermittedFiles {
                            allowed_paths: vec!["version.txt".into(), "changelog.md".into()],
                        },
                    )
                    .with_weight(0.3)
                    .required(),
                ],
            },
        },
        FrozenTask {
            task_id: "file-op-clean-delete".into(),
            category: FrozenTaskCategory::ExactFileOperations,
            prompt: "In flags.env, delete the experimental lines EXPERIMENTAL_X and EXPERIMENTAL_Y, keeping only FEATURE_A=1 and FEATURE_B=1.".into(),
            initial_files: BTreeMap::from([
                ("flags.env".into(), "FEATURE_A=1\nEXPERIMENTAL_X=true\nFEATURE_B=1\nEXPERIMENTAL_Y=true\n".into()),
            ]),
            reference_cost: 0.04,
            reference_time_ms: 12_000,
            acceptance: AcceptanceSpec {
                task_id: "file-op-clean-delete".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "exact-flags",
                        AcceptanceCheck::ExactFileContent {
                            path: "flags.env".into(),
                            expected: "FEATURE_A=1\nFEATURE_B=1\n".into(),
                        },
                    )
                    .with_weight(0.7)
                    .required(),
                    AcceptanceCriterion::new(
                        "permitted-files",
                        AcceptanceCheck::PermittedFiles {
                            allowed_paths: vec!["flags.env".into()],
                        },
                    )
                    .with_weight(0.3)
                    .required(),
                ],
            },
        },

        // ==========================================
        // Category 2: Structured Extraction
        // ==========================================
        FrozenTask {
            task_id: "structured-extract-users".into(),
            category: FrozenTaskCategory::StructuredExtraction,
            prompt: "Parse access.log and extract all user login events into users.json matching required fields name, id, role.".into(),
            initial_files: BTreeMap::from([
                ("access.log".into(), "2026-10-21 LOGIN user=alice id=101 role=admin\n2026-10-21 LOGIN user=bob id=102 role=member\n".into()),
            ]),
            reference_cost: 0.08,
            reference_time_ms: 20_000,
            acceptance: AcceptanceSpec {
                task_id: "structured-extract-users".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "schema-valid",
                        AcceptanceCheck::JsonSchema {
                            path: "users.json".into(),
                            required_fields: vec!["name".into(), "id".into(), "role".into()],
                        },
                    )
                    .with_weight(0.5)
                    .required(),
                    AcceptanceCriterion::new(
                        "alice-user",
                        AcceptanceCheck::JsonField {
                            path: "users.json".into(),
                            pointer: "/0/name".into(),
                            expected: serde_json::json!("alice"),
                        },
                    )
                    .with_weight(0.25)
                    .required(),
                    AcceptanceCriterion::new(
                        "bob-role",
                        AcceptanceCheck::JsonField {
                            path: "users.json".into(),
                            pointer: "/1/role".into(),
                            expected: serde_json::json!("member"),
                        },
                    )
                    .with_weight(0.25),
                ],
            },
        },
        FrozenTask {
            task_id: "structured-metrics-summary".into(),
            category: FrozenTaskCategory::StructuredExtraction,
            prompt: "Summarize data.csv into summary.json with fields total_records (3), mean_score (90.0), and status ('ok').".into(),
            initial_files: BTreeMap::from([
                ("data.csv".into(), "record_id,score\n1,80\n2,90\n3,100\n".into()),
            ]),
            reference_cost: 0.08,
            reference_time_ms: 22_000,
            acceptance: AcceptanceSpec {
                task_id: "structured-metrics-summary".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "summary-schema",
                        AcceptanceCheck::JsonSchema {
                            path: "summary.json".into(),
                            required_fields: vec!["total_records".into(), "mean_score".into(), "status".into()],
                        },
                    )
                    .with_weight(0.5)
                    .required(),
                    AcceptanceCriterion::new(
                        "count-field",
                        AcceptanceCheck::JsonField {
                            path: "summary.json".into(),
                            pointer: "/total_records".into(),
                            expected: serde_json::json!(3),
                        },
                    )
                    .with_weight(0.25)
                    .required(),
                    AcceptanceCriterion::new(
                        "mean-field",
                        AcceptanceCheck::JsonField {
                            path: "summary.json".into(),
                            pointer: "/mean_score".into(),
                            expected: serde_json::json!(90.0),
                        },
                    )
                    .with_weight(0.25)
                    .required(),
                ],
            },
        },
        FrozenTask {
            task_id: "structured-dependency-manifest".into(),
            category: FrozenTaskCategory::StructuredExtraction,
            prompt: "Inspect packages.txt and generate manifest.json with package_count (3) and status ('valid').".into(),
            initial_files: BTreeMap::from([
                ("packages.txt".into(), "tokio=1.0\nserde=1.0\naxum=0.7\n".into()),
            ]),
            reference_cost: 0.07,
            reference_time_ms: 20_000,
            acceptance: AcceptanceSpec {
                task_id: "structured-dependency-manifest".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "manifest-count",
                        AcceptanceCheck::JsonField {
                            path: "manifest.json".into(),
                            pointer: "/package_count".into(),
                            expected: serde_json::json!(3),
                        },
                    )
                    .with_weight(0.5)
                    .required(),
                    AcceptanceCriterion::new(
                        "manifest-status",
                        AcceptanceCheck::JsonField {
                            path: "manifest.json".into(),
                            pointer: "/status".into(),
                            expected: serde_json::json!("valid"),
                        },
                    )
                    .with_weight(0.5)
                    .required(),
                ],
            },
        },

        // ==========================================
        // Category 3: Small Code Fixes
        // ==========================================
        FrozenTask {
            task_id: "code-fix-off-by-one".into(),
            category: FrozenTaskCategory::SmallCodeFixes,
            prompt: "Fix the boundary bug in calculate.py so range(len(items)) is used instead of items+1. Verify by running 'python3 test_calc.py'.".into(),
            initial_files: BTreeMap::from([
                ("calculate.py".into(), "def total_sum(items):\n    s = 0\n    for i in range(len(items) + 1):\n        s += items[i]\n    return s\n".into()),
                ("test_calc.py".into(), "import sys\nfrom calculate import total_sum\nif total_sum([1, 2, 3]) != 6:\n    sys.exit(1)\nsys.exit(0)\n".into()),
            ]),
            reference_cost: 0.10,
            reference_time_ms: 25_000,
            acceptance: AcceptanceSpec {
                task_id: "code-fix-off-by-one".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "calc-test",
                        AcceptanceCheck::ControlledTest {
                            id: "test_calc".into(),
                            command: "python3 test_calc.py".into(),
                            timeout_ms: 10_000,
                            output_limit_bytes: 4096,
                        },
                    )
                    .with_weight(0.7)
                    .required(),
                    AcceptanceCriterion::new(
                        "permitted-files",
                        AcceptanceCheck::PermittedFiles {
                            allowed_paths: vec!["calculate.py".into()],
                        },
                    )
                    .with_weight(0.3)
                    .required(),
                ],
            },
        },
        FrozenTask {
            task_id: "code-fix-null-deref".into(),
            category: FrozenTaskCategory::SmallCodeFixes,
            prompt: "In auth.py, get_user_role(users, username) crashes on missing users. Return 'guest' if user is not found. Test with 'python3 test_auth.py'.".into(),
            initial_files: BTreeMap::from([
                ("auth.py".into(), "def get_user_role(users, username):\n    return users[username]['role']\n".into()),
                ("test_auth.py".into(), "import sys\nfrom auth import get_user_role\nu = {'alice': {'role': 'admin'}}\nif get_user_role(u, 'alice') != 'admin' or get_user_role(u, 'bob') != 'guest':\n    sys.exit(1)\nsys.exit(0)\n".into()),
            ]),
            reference_cost: 0.10,
            reference_time_ms: 25_000,
            acceptance: AcceptanceSpec {
                task_id: "code-fix-null-deref".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "auth-test",
                        AcceptanceCheck::ControlledTest {
                            id: "test_auth".into(),
                            command: "python3 test_auth.py".into(),
                            timeout_ms: 10_000,
                            output_limit_bytes: 4096,
                        },
                    )
                    .with_weight(0.7)
                    .required(),
                    AcceptanceCriterion::new(
                        "permitted-files",
                        AcceptanceCheck::PermittedFiles {
                            allowed_paths: vec!["auth.py".into()],
                        },
                    )
                    .with_weight(0.3)
                    .required(),
                ],
            },
        },
        FrozenTask {
            task_id: "code-fix-string-escape".into(),
            category: FrozenTaskCategory::SmallCodeFixes,
            prompt: "Fix escape_quotes in parser.py to escape double quotes with a backslash. Verify with 'python3 test_parser.py'.".into(),
            initial_files: BTreeMap::from([
                ("parser.py".into(), "def escape_quotes(s):\n    return s.replace('\"', '')\n".into()),
                ("test_parser.py".into(), "import sys\nfrom parser import escape_quotes\nif escape_quotes('say \"hello\"') != 'say \\\"hello\\\"':\n    sys.exit(1)\nsys.exit(0)\n".into()),
            ]),
            reference_cost: 0.10,
            reference_time_ms: 25_000,
            acceptance: AcceptanceSpec {
                task_id: "code-fix-string-escape".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "parser-test",
                        AcceptanceCheck::ControlledTest {
                            id: "test_parser".into(),
                            command: "python3 test_parser.py".into(),
                            timeout_ms: 10_000,
                            output_limit_bytes: 4096,
                        },
                    )
                    .with_weight(0.7)
                    .required(),
                    AcceptanceCriterion::new(
                        "permitted-files",
                        AcceptanceCheck::PermittedFiles {
                            allowed_paths: vec!["parser.py".into()],
                        },
                    )
                    .with_weight(0.3)
                    .required(),
                ],
            },
        },

        // ==========================================
        // Category 4: Multistep Work and Recovery
        // ==========================================
        FrozenTask {
            task_id: "multistep-recover-missing-dir".into(),
            category: FrozenTaskCategory::MultistepWorkAndRecovery,
            prompt: "Export active records from input.raw into 'data/export/clean.txt' (format: one record per line). Ensure the 'data/export' directory is created if missing. Run 'python3 test_export.py' to verify.".into(),
            initial_files: BTreeMap::from([
                ("input.raw".into(), "record:1:active\nrecord:2:inactive\nrecord:3:active\n".into()),
                ("test_export.py".into(), "import os, sys\nif not os.path.exists('data/export/clean.txt'):\n    sys.exit(1)\nwith open('data/export/clean.txt') as f:\n    lines = [l.strip() for l in f if l.strip()]\nif lines != ['record:1:active', 'record:3:active']:\n    sys.exit(1)\nsys.exit(0)\n".into()),
            ]),
            reference_cost: 0.15,
            reference_time_ms: 35_000,
            acceptance: AcceptanceSpec {
                task_id: "multistep-recover-missing-dir".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "export-test",
                        AcceptanceCheck::ControlledTest {
                            id: "test_export".into(),
                            command: "python3 test_export.py".into(),
                            timeout_ms: 10_000,
                            output_limit_bytes: 4096,
                        },
                    )
                    .with_weight(0.6)
                    .required(),
                    AcceptanceCriterion::new(
                        "clean-content",
                        AcceptanceCheck::ArtifactContains {
                            path: "data/export/clean.txt".into(),
                            value: "record:1:active\nrecord:3:active".into(),
                        },
                    )
                    .with_weight(0.4)
                    .required(),
                ],
            },
        },
        FrozenTask {
            task_id: "multistep-build-and-package".into(),
            category: FrozenTaskCategory::MultistepWorkAndRecovery,
            prompt: "Combine 'src/a.txt' and 'src/b.txt' into 'build/bundle.txt' sorted alphabetically, then write the sha256 hex digest of bundle.txt into 'build/checksum.txt'. Run 'python3 test_package.py'.".into(),
            initial_files: BTreeMap::from([
                ("src/a.txt".into(), "ALPHA=1\n".into()),
                ("src/b.txt".into(), "BETA=2\n".into()),
                ("test_package.py".into(), "import hashlib, os, sys\nif not os.path.exists('build/bundle.txt') or not os.path.exists('build/checksum.txt'):\n    sys.exit(1)\nwith open('build/bundle.txt', 'rb') as f:\n    h = hashlib.sha256(f.read()).hexdigest()\nwith open('build/checksum.txt') as f:\n    if f.read().strip() != h:\n        sys.exit(1)\nsys.exit(0)\n".into()),
            ]),
            reference_cost: 0.15,
            reference_time_ms: 35_000,
            acceptance: AcceptanceSpec {
                task_id: "multistep-build-and-package".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "package-test",
                        AcceptanceCheck::ControlledTest {
                            id: "test_package".into(),
                            command: "python3 test_package.py".into(),
                            timeout_ms: 10_000,
                            output_limit_bytes: 4096,
                        },
                    )
                    .with_weight(0.7)
                    .required(),
                    AcceptanceCriterion::new(
                        "bundle-artifact",
                        AcceptanceCheck::ArtifactContains {
                            path: "build/bundle.txt".into(),
                            value: "ALPHA=1\nBETA=2\n".into(),
                        },
                    )
                    .with_weight(0.3)
                    .required(),
                ],
            },
        },
        FrozenTask {
            task_id: "multistep-pipeline-self-correct".into(),
            category: FrozenTaskCategory::MultistepWorkAndRecovery,
            prompt: "pipeline_config.json has 'mode': 'unsupported' which causes test_pipeline.py to fail. Update mode to 'fast' and retries to 5. Run 'python3 test_pipeline.py'.".into(),
            initial_files: BTreeMap::from([
                ("pipeline_config.json".into(), "{\n  \"mode\": \"unsupported\",\n  \"retries\": 3\n}\n".into()),
                ("test_pipeline.py".into(), "import json, sys\nwith open('pipeline_config.json') as f:\n    c = json.load(f)\nif c.get('mode') != 'fast' or c.get('retries') != 5:\n    sys.exit(1)\nsys.exit(0)\n".into()),
            ]),
            reference_cost: 0.12,
            reference_time_ms: 30_000,
            acceptance: AcceptanceSpec {
                task_id: "multistep-pipeline-self-correct".into(),
                version: 1,
                criteria: vec![
                    AcceptanceCriterion::new(
                        "pipeline-test",
                        AcceptanceCheck::ControlledTest {
                            id: "test_pipeline".into(),
                            command: "python3 test_pipeline.py".into(),
                            timeout_ms: 10_000,
                            output_limit_bytes: 4096,
                        },
                    )
                    .with_weight(0.6)
                    .required(),
                    AcceptanceCriterion::new(
                        "mode-field",
                        AcceptanceCheck::JsonField {
                            path: "pipeline_config.json".into(),
                            pointer: "/mode".into(),
                            expected: serde_json::json!("fast"),
                        },
                    )
                    .with_weight(0.4)
                    .required(),
                ],
            },
        },
    ]
}

/// A 4-task smoke subset (1 task from each category) for fast qualification/smoke campaigns.
pub fn smoke_task_subset() -> Vec<FrozenTask> {
    let all = frozen_task_suite();
    vec![
        all[0].clone(), // ExactFileOperations
        all[3].clone(), // StructuredExtraction
        all[6].clone(), // SmallCodeFixes
        all[9].clone(), // MultistepWorkAndRecovery
    ]
}

// ==========================================
// Canonical Policy Definitions for PR 4
// ==========================================

/// Fixed Strong policy: all turns use strong model.
pub fn policy_fixed_strong(strong_model: impl Into<String>) -> BlendRoutingPolicy {
    let model = strong_model.into();
    BlendRoutingPolicy {
        policy_id: "fixed_strong".into(),
        version: 1,
        default_model: model.clone(),
        after_tool_success: None,
        after_tool_error: None,
        recovery_model: None,
        planning_model: None,
        tool_routes: Vec::new(),
        recovery_after_no_progress_steps: 3,
        minimum_model_dwell_steps: 1,
        tool_call_capable_models: BTreeSet::from([model.clone()]),
        typed_completion_capable_models: BTreeSet::from([model]),
    }
}

/// Fixed Inexpensive policy: all turns use inexpensive model.
pub fn policy_fixed_inexpensive(inexpensive_model: impl Into<String>) -> BlendRoutingPolicy {
    let model = inexpensive_model.into();
    BlendRoutingPolicy {
        policy_id: "fixed_inexpensive".into(),
        version: 1,
        default_model: model.clone(),
        after_tool_success: None,
        after_tool_error: None,
        recovery_model: None,
        planning_model: None,
        tool_routes: Vec::new(),
        recovery_after_no_progress_steps: 3,
        minimum_model_dwell_steps: 1,
        tool_call_capable_models: BTreeSet::from([model.clone()]),
        typed_completion_capable_models: BTreeSet::from([model]),
    }
}

/// Fixed Stage Blend: uses strong model for planning, inexpensive for execution.
pub fn policy_fixed_stage_blend(
    strong_model: impl Into<String>,
    inexpensive_model: impl Into<String>,
) -> BlendRoutingPolicy {
    let strong = strong_model.into();
    let inexpensive = inexpensive_model.into();
    BlendRoutingPolicy {
        policy_id: "fixed_stage_blend".into(),
        version: 1,
        default_model: inexpensive.clone(),
        after_tool_success: None,
        after_tool_error: None,
        recovery_model: None,
        planning_model: Some(strong.clone()),
        tool_routes: Vec::new(),
        recovery_after_no_progress_steps: 3,
        minimum_model_dwell_steps: 1,
        tool_call_capable_models: BTreeSet::from([strong.clone(), inexpensive.clone()]),
        typed_completion_capable_models: BTreeSet::from([strong, inexpensive]),
    }
}

/// State-Aware Blend: inexpensive for normal turns, escalates to strong model upon tool error or recovery.
pub fn policy_state_aware_blend(
    strong_model: impl Into<String>,
    inexpensive_model: impl Into<String>,
) -> BlendRoutingPolicy {
    let strong = strong_model.into();
    let inexpensive = inexpensive_model.into();
    BlendRoutingPolicy {
        policy_id: "state_aware_blend".into(),
        version: 1,
        default_model: inexpensive.clone(),
        after_tool_success: Some(inexpensive.clone()),
        after_tool_error: Some(strong.clone()),
        recovery_model: Some(strong.clone()),
        planning_model: None,
        tool_routes: Vec::new(),
        recovery_after_no_progress_steps: 2,
        minimum_model_dwell_steps: 1,
        tool_call_capable_models: BTreeSet::from([strong.clone(), inexpensive.clone()]),
        typed_completion_capable_models: BTreeSet::from([strong, inexpensive]),
    }
}

/// Default standard price card for benchmarking.
pub fn canonical_price_card() -> PriceCard {
    PriceCard::new("2026-10-benchmark-pricing", "USD")
        .with_model(
            "strong",
            ModelPriceRule::new(3.0, 15.0).with_cache_rates(Some(0.30), Some(3.75)),
        )
        .with_model(
            "inexpensive",
            ModelPriceRule::new(0.15, 0.60).with_cache_rates(Some(0.075), None),
        )
        .with_model(
            "claude-3-5-sonnet",
            ModelPriceRule::new(3.0, 15.0).with_cache_rates(Some(0.30), Some(3.75)),
        )
        .with_model(
            "gpt-4o",
            ModelPriceRule::new(2.5, 10.0).with_cache_rates(Some(1.25), None),
        )
        .with_model(
            "gpt-4o-mini",
            ModelPriceRule::new(0.15, 0.60).with_cache_rates(Some(0.075), None),
        )
        .with_model("claude-3-haiku", ModelPriceRule::new(0.25, 1.25))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suite_contains_exactly_12_tasks_across_4_categories() {
        let tasks = frozen_task_suite();
        assert_eq!(tasks.len(), 12);

        let mut cat_counts = BTreeMap::new();
        let mut ids = BTreeSet::new();

        for t in &tasks {
            assert!(ids.insert(&t.task_id), "duplicate task id: {}", t.task_id);
            *cat_counts.entry(t.category).or_insert(0) += 1;
            assert!(t.reference_cost > 0.0);
            assert!(t.reference_time_ms > 0);
            assert!(t.acceptance.validate().is_ok());
        }

        assert_eq!(cat_counts[&FrozenTaskCategory::ExactFileOperations], 3);
        assert_eq!(cat_counts[&FrozenTaskCategory::StructuredExtraction], 3);
        assert_eq!(cat_counts[&FrozenTaskCategory::SmallCodeFixes], 3);
        assert_eq!(cat_counts[&FrozenTaskCategory::MultistepWorkAndRecovery], 3);
    }

    #[test]
    fn suite_splits_are_balanced_and_partition_accurately() {
        let dev = development_task_suite();
        let held_out = held_out_task_suite();
        let full = frozen_task_suite();

        assert_eq!(dev.len(), 8);
        assert_eq!(held_out.len(), 4);
        assert_eq!(full.len(), 12);

        let dev_ids: BTreeSet<_> = dev.iter().map(|t| &t.task_id).collect();
        let held_out_ids: BTreeSet<_> = held_out.iter().map(|t| &t.task_id).collect();
        let full_ids: BTreeSet<_> = full.iter().map(|t| &t.task_id).collect();

        // Disjoint sets
        assert!(dev_ids.is_disjoint(&held_out_ids));
        // Union equals full
        let union: BTreeSet<_> = dev_ids.union(&held_out_ids).copied().collect();
        assert_eq!(union, full_ids);

        // Check category balance
        let mut dev_cats = BTreeMap::new();
        for t in &dev {
            *dev_cats.entry(t.category).or_insert(0) += 1;
        }
        for count in dev_cats.values() {
            assert_eq!(*count, 2);
        }

        let mut held_out_cats = BTreeMap::new();
        for t in &held_out {
            *held_out_cats.entry(t.category).or_insert(0) += 1;
        }
        for count in held_out_cats.values() {
            assert_eq!(*count, 1);
        }
    }

    #[test]
    fn smoke_subset_has_one_from_each_category() {
        let smoke = smoke_task_subset();
        assert_eq!(smoke.len(), 4);
        let categories: BTreeSet<_> = smoke.iter().map(|t| t.category).collect();
        assert_eq!(categories.len(), 4);
    }

    #[test]
    fn four_canonical_policies_are_well_formed() {
        let strong = policy_fixed_strong("gpt-4o");
        assert_eq!(strong.default_model, "gpt-4o");
        assert!(strong.planning_model.is_none());

        let inexp = policy_fixed_inexpensive("gpt-4o-mini");
        assert_eq!(inexp.default_model, "gpt-4o-mini");

        let stage = policy_fixed_stage_blend("gpt-4o", "gpt-4o-mini");
        assert_eq!(stage.default_model, "gpt-4o-mini");
        assert_eq!(stage.planning_model, Some("gpt-4o".into()));

        let state_aware = policy_state_aware_blend("gpt-4o", "gpt-4o-mini");
        assert_eq!(state_aware.default_model, "gpt-4o-mini");
        assert_eq!(state_aware.after_tool_error, Some("gpt-4o".into()));
        assert_eq!(state_aware.recovery_model, Some("gpt-4o".into()));
    }

    #[test]
    fn write_and_verify_frozen_campaign_fixture() {
        let tasks = frozen_task_suite();
        let payload = serde_json::json!({
            "candidates": [
                { "label": "fixed_strong", "policy_id": "fixed_strong" },
                { "label": "fixed_inexpensive", "policy_id": "fixed_inexpensive" },
                { "label": "fixed_stage_blend", "policy_id": "fixed_stage_blend" },
                { "label": "state_aware_blend", "policy_id": "state_aware_blend" }
            ],
            "repetitions": 3,
            "max_model_steps": 15,
            "timeout_seconds": 120,
            "seed": 42,
            "tasks": tasks
        });

        let fixtures_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures");
        let file_path = fixtures_dir.join("frozen-campaign.json");
        std::fs::write(&file_path, serde_json::to_vec_pretty(&payload).unwrap()).unwrap();
        assert!(file_path.exists());
    }
}
