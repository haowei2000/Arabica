//! Tool definitions for the tools this crate can execute.
//!
//! The runner owns both the advertised schema and the execution path so the
//! two cannot drift: a tool the model is told about is one the runner can run.

use structure_model::ToolDefinition;

use crate::{LocalRunnerPolicy, LocalTool};

pub fn write_file_definition() -> ToolDefinition {
    ToolDefinition {
        name: "write_file".to_owned(),
        description: "Write UTF-8 text to a relative path inside the configured workspace root. Parent directories must already exist.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path inside the workspace root"
                },
                "content": {
                    "type": "string",
                    "description": "Complete UTF-8 file content"
                }
            },
            "required": ["path", "content"],
            "additionalProperties": false
        }),
        strict: None,
    }
}

pub fn read_file_definition() -> ToolDefinition {
    ToolDefinition {
        name: "read_file".to_owned(),
        description: "Read one UTF-8 file relative to the confined workspace root.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path inside the workspace root"
                }
            },
            "required": ["path"],
            "additionalProperties": false
        }),
        strict: Some(true),
    }
}

pub fn list_dir_definition() -> ToolDefinition {
    ToolDefinition {
        name: "list_dir".to_owned(),
        description: "List one directory inside the confined workspace root. Omit the path to list the root. Directories end with '/' and symbolic links with '@'.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path inside the workspace root; omit for the root"
                }
            },
            "required": [],
            "additionalProperties": false
        }),
        // Optional properties are incompatible with OpenAI strict mode, which
        // requires every declared property to be required.
        strict: None,
    }
}

pub fn grep_definition() -> ToolDefinition {
    ToolDefinition {
        name: "grep".to_owned(),
        description: "Search file contents inside the confined workspace root with a Rust regular expression. Files ignored by version control and non-UTF-8 files are skipped. Results are reported as path:line:text.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Rust regular expression matched against each line"
                },
                "path": {
                    "type": "string",
                    "description": "Relative directory to search; omit to search the whole root"
                }
            },
            "required": ["pattern"],
            "additionalProperties": false
        }),
        strict: None,
    }
}

pub fn find_files_definition() -> ToolDefinition {
    ToolDefinition {
        name: "find_files".to_owned(),
        description: "Find files by glob inside the confined workspace root, for example '**/*.rs'. Files ignored by version control are skipped. '*' does not cross directory separators.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "glob": {
                    "type": "string",
                    "description": "Glob matched against paths relative to the workspace root"
                },
                "path": {
                    "type": "string",
                    "description": "Relative directory to search; omit to search the whole root"
                }
            },
            "required": ["glob"],
            "additionalProperties": false
        }),
        strict: None,
    }
}

pub fn edit_files_definition() -> ToolDefinition {
    ToolDefinition {
        name: "edit_files".to_owned(),
        description: "Replace exact strings in one or more files inside the confined workspace root. Every edit is checked before anything is written, so a batch that fails changes nothing. old_string must match the file exactly and must be unique unless replace_all is set. Several edits may target the same file and apply in order.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Edits applied in order",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative file path inside the workspace root"
                            },
                            "old_string": {
                                "type": "string",
                                "description": "Exact text to replace, copied from the file including indentation"
                            },
                            "new_string": {
                                "type": "string",
                                "description": "Replacement text"
                            },
                            "replace_all": {
                                "type": "boolean",
                                "description": "Replace every occurrence instead of requiring a unique match"
                            }
                        },
                        "required": ["path", "old_string", "new_string"],
                        "additionalProperties": false
                    }
                }
            },
            "required": ["edits"],
            "additionalProperties": false
        }),
        strict: None,
    }
}

pub fn delete_file_definition() -> ToolDefinition {
    ToolDefinition {
        name: "delete_file".to_owned(),
        description: "Delete one regular file inside the confined workspace root. Symbolic links are refused.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path inside the workspace root"
                }
            },
            "required": ["path"],
            "additionalProperties": false
        }),
        strict: Some(true),
    }
}

/// The definition for one tool the local runner can execute.
pub fn definition(tool: LocalTool) -> ToolDefinition {
    match tool {
        LocalTool::ReadFile => read_file_definition(),
        LocalTool::ListDir => list_dir_definition(),
        LocalTool::Grep => grep_definition(),
        LocalTool::FindFiles => find_files_definition(),
        LocalTool::WriteFile => write_file_definition(),
        LocalTool::EditFiles => edit_files_definition(),
        LocalTool::DeleteFile => delete_file_definition(),
    }
}

/// Definitions for exactly the tools a policy enables.
///
/// The host advertises what the runner will execute, from one source, so the
/// model is never told about a tool that would be refused.
pub fn tool_definitions(policy: &LocalRunnerPolicy) -> Vec<ToolDefinition> {
    policy.tools.iter().copied().map(definition).collect()
}
