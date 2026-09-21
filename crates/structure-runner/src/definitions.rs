//! Tool definitions for the tools this crate can execute.
//!
//! The runner owns both the advertised schema and the execution path so the
//! two cannot drift: a tool the model is told about is one the runner can run.

use structure_model::ToolDefinition;

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
