//! Typed classification of shell commands for memory-retention policy.
//!
//! The classifier is a heuristic over command text. It decides how Runtime
//! retains a tool batch; it must never be used to authorize execution. A
//! command such as `cat notes | sh` classifies as inspection while executing
//! arbitrary code, so permission decisions belong to tool names and an explicit
//! policy instead.

use arabica_protocol::ToolInteractionKind;

pub fn classify_shell_interaction(command: &str) -> ToolInteractionKind {
    let command = command.to_ascii_lowercase();
    let dependency_position = last_pattern_position(
        &command,
        &[
            "pip install",
            "uv pip",
            "apt-get install",
            "npm install",
            "cargo add",
        ],
    );
    let mutation_position = last_command_position(
        &command,
        &[
            "sed -i",
            "perl -pi",
            "apply_patch",
            "tee ",
            "touch ",
            "mkdir ",
            "git clone",
            "cp ",
            "mv ",
            "rm ",
            "chmod ",
            "chown ",
            "ln ",
        ],
    )
    .into_iter()
    .chain(shell_output_redirection_position(&command))
    .chain(inline_mutation_position(&command))
    .max();
    let build_position = (!invokes_inline_program(&command))
        .then(|| last_pattern_position(&command, &["build_ext", "cargo build", "cmake ", "make "]))
        .flatten();
    let validation_position =
        last_command_position(&command, &["pytest", "cargo test", "npm test", "unittest"])
            .into_iter()
            .chain(inline_validation_position(&command))
            .max();
    let last_state_change = dependency_position
        .into_iter()
        .chain(mutation_position)
        .chain(build_position)
        .max();

    // A successful runner result validates the final state only when its last
    // recognized semantic operation is validation. This covers safe patterns
    // such as `rm stale && pytest`, while `pytest && rm stale` remains a
    // mutation that revokes completion eligibility.
    if matches!((last_state_change, validation_position), (Some(change), Some(check)) if check > change)
    {
        ToolInteractionKind::MutationWithValidation
    } else if dependency_position.is_some() {
        ToolInteractionKind::Dependency
    } else if mutation_position.is_some() {
        ToolInteractionKind::Mutation
    } else if validation_position.is_some() {
        // Inspect the executable shape before scanning the embedded program.
        // Inline validation source can legitimately contain strings such as
        // `import package.make as make`, which must not turn a read-only probe
        // into a state-changing build event.
        ToolInteractionKind::Validation
    } else if build_position.is_some() {
        ToolInteractionKind::Build
    } else if [
        "cat ",
        "grep ",
        "rg ",
        "find ",
        "ls ",
        "head ",
        "tail ",
        "sed -n",
        "git status",
        "git diff",
    ]
    .iter()
    .any(|pattern| command.contains(pattern))
    {
        ToolInteractionKind::Inspection
    } else {
        ToolInteractionKind::Generic
    }
}

fn last_command_position(command: &str, patterns: &[&str]) -> Option<usize> {
    patterns
        .iter()
        .flat_map(|pattern| command.match_indices(pattern))
        .filter_map(|(position, pattern)| {
            let prefix = &command[..position];
            let trimmed = prefix.trim_end();
            let command_boundary = trimmed.is_empty()
                || trimmed.ends_with("&&")
                || trimmed.ends_with("||")
                || trimmed.ends_with(';')
                || trimmed.ends_with('|')
                || trimmed.ends_with("-m");
            command_boundary.then_some(position + pattern.len())
        })
        .max()
}

fn last_pattern_position(command: &str, patterns: &[&str]) -> Option<usize> {
    patterns
        .iter()
        .filter_map(|pattern| command.rfind(pattern))
        .max()
}

#[cfg(test)]
fn has_shell_output_redirection(command: &str) -> bool {
    shell_output_redirection_position(command).is_some()
}

fn shell_output_redirection_position(command: &str) -> Option<usize> {
    let mut single_quoted = false;
    let mut double_quoted = false;
    let mut escaped = false;
    let chars: Vec<char> = command.chars().collect();
    for (index, character) in chars.iter().copied().enumerate() {
        if escaped {
            escaped = false;
            continue;
        }
        if character == '\\' && !single_quoted {
            escaped = true;
            continue;
        }
        if character == '\'' && !double_quoted {
            single_quoted = !single_quoted;
            continue;
        }
        if character == '"' && !single_quoted {
            double_quoted = !double_quoted;
            continue;
        }
        if character != '>' || single_quoted || double_quoted {
            continue;
        }
        // Descriptor duplication such as `2>&1` and discarding a stream to
        // `/dev/null` change routing, not workspace state. Other unquoted
        // output redirects can write a file and invalidate reusable results.
        let target = chars[index + 1..].iter().collect::<String>();
        if chars.get(index + 1) != Some(&'&') && !target.trim_start().starts_with("/dev/null") {
            return Some(index);
        }
    }
    None
}

/// Position of the last inline validation marker in an embedded program.
///
/// Exposed because callers that gate on pipeline strictness need the same
/// notion of "this command asserts something" as the classifier.
pub fn inline_validation_position(command: &str) -> Option<usize> {
    invokes_inline_program(command)
        .then(|| {
            last_pattern_position(
                command,
                &[
                    "assert ",
                    "raise assertionerror",
                    "sys.exit(1)",
                    "sys.exit(false)",
                ],
            )
        })
        .flatten()
}

fn invokes_inline_program(command: &str) -> bool {
    (command.contains("python ") || command.contains("python3 "))
        && (command.contains(" -c") || command.contains("<<"))
}

fn inline_mutation_position(command: &str) -> Option<usize> {
    if !invokes_inline_program(command) {
        return None;
    }
    let mut position = last_pattern_position(
        command,
        &[
            ".write(",
            "write_text(",
            "write_bytes(",
            "unlink(",
            "remove(",
            "rename(",
            "mkdir(",
            "subprocess",
            "os.system",
            "shutil",
        ],
    );
    // `open(path)` and `open(path, 'rb')` are read-only. Only an explicit
    // write-capable mode makes open itself a mutation marker.
    let mut search_from = 0;
    while let Some(relative) = command[search_from..].find("open(") {
        let start = search_from + relative;
        let end = command[start..]
            .find(')')
            .map_or(command.len(), |offset| start + offset);
        let call = &command[start..end];
        if ["'w", "\"w", "'a", "\"a", "'x", "\"x", "'r+", "\"r+"]
            .iter()
            .any(|mode| call.contains(mode))
        {
            position = Some(position.map_or(start, |current| current.max(start)));
        }
        search_from = end.saturating_add(1);
        if search_from >= command.len() {
            break;
        }
    }
    position
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shell_interactions_are_classified_before_memory_policy() {
        for (command, expected) in [
            ("cat pyproject.toml", ToolInteractionKind::Inspection),
            ("grep -R TODO src", ToolInteractionKind::Inspection),
            (
                "python setup.py build_ext --inplace",
                ToolInteractionKind::Build,
            ),
            (
                "python -m pip install cython",
                ToolInteractionKind::Dependency,
            ),
            ("pytest -q", ToolInteractionKind::Validation),
            (
                "python3 -c \"import package; assert package.check()\"",
                ToolInteractionKind::Validation,
            ),
            (
                "python3 -c \"from pathlib import Path; Path('x').write_text('x'); print('done')\"",
                ToolInteractionKind::Mutation,
            ),
            ("sed -i 's/old/new/' file", ToolInteractionKind::Mutation),
            ("python scripts/generate.py", ToolInteractionKind::Generic),
        ] {
            assert_eq!(classify_shell_interaction(command), expected, "{command}");
        }
    }

    #[test]
    fn shell_classification_uses_command_boundaries_not_argument_substrings() {
        assert_eq!(
            classify_shell_interaction(
                "pip install --no-cache-dir setuptools wheel 'cython<3.1' pytest"
            ),
            ToolInteractionKind::Dependency
        );
        assert_eq!(
            classify_shell_interaction("grep -rln TODO /app"),
            ToolInteractionKind::Inspection
        );
        assert_eq!(
            classify_shell_interaction("pip install pytest && python -m pytest -q"),
            ToolInteractionKind::MutationWithValidation
        );
    }

    #[test]
    fn read_only_inline_validation_and_ordered_pipelines_preserve_final_semantics() {
        let read_only = r#"python3 -B - <<'EOF'
import json
data = json.load(open('/app/output.json'))
blob = open('/app/main.db', 'rb').read()
assert data and blob
EOF"#;
        assert_eq!(
            classify_shell_interaction(read_only),
            ToolInteractionKind::Validation
        );

        let sandbox_copy = r#"python3 -c "
import json, sqlite3, shutil
shutil.copytree('/app', '/tmp/validation', dirs_exist_ok=True)
rows = sqlite3.connect('/tmp/validation/main.db').execute('select 1').fetchall()
assert json.load(open('/app/output.json')) and rows
""#;
        assert_eq!(
            classify_shell_interaction(sandbox_copy),
            ToolInteractionKind::MutationWithValidation
        );
        assert_eq!(
            classify_shell_interaction("rm -f stale && pytest -q"),
            ToolInteractionKind::MutationWithValidation
        );
        assert_eq!(
            classify_shell_interaction("pytest -q && rm -f stale"),
            ToolInteractionKind::Mutation
        );
    }

    #[test]
    fn inline_validation_source_does_not_match_build_substrings() {
        let command = r#"cd /app/sample-project && python3 -c "
import sample_package.make as mk
result = mk.example()
assert result is not None
" 2>&1"#;

        assert_eq!(
            classify_shell_interaction(command),
            ToolInteractionKind::Validation
        );
    }

    #[test]
    fn python_heredoc_without_dash_is_classified_as_validation() {
        let command = r#"cd /app/sample-project && python3 << 'EOF'
import sample_package
assert sample_package.example() is not None
EOF"#;

        assert_eq!(
            classify_shell_interaction(command),
            ToolInteractionKind::Validation
        );
    }

    #[test]
    fn shell_file_redirection_is_mutation_but_descriptor_merging_is_not() {
        assert_eq!(
            classify_shell_interaction(
                "cat > /tmp/fix.py << 'EOF'\nprint('fix')\nEOF\npython /tmp/fix.py"
            ),
            ToolInteractionKind::Mutation
        );
        assert_eq!(
            classify_shell_interaction("pytest -q 2>&1 | tail -20"),
            ToolInteractionKind::Validation
        );
        assert!(!has_shell_output_redirection("python3 -c \"print(2 > 1)\""));
    }

    #[test]
    fn shell_classification_protects_real_mutations_not_dev_null_routing() {
        assert_eq!(
            classify_shell_interaction(
                "which sqlite3; sqlite3 --version 2>/dev/null; python3 -c \"import sqlite3; print(sqlite3.sqlite_version)\""
            ),
            ToolInteractionKind::Generic
        );
        assert_eq!(
            classify_shell_interaction("cp -a /app/main.db /tmp/main.db.bak && ls -la /tmp"),
            ToolInteractionKind::Mutation
        );
        assert_eq!(
            classify_shell_interaction(
                "python3 - <<'EOF'\ndata = open('/tmp/in','rb').read()\nopen('/app/out','wb').write(data)\nprint(len(data))\nEOF"
            ),
            ToolInteractionKind::Mutation
        );
        assert!(!has_shell_output_redirection("pytest -q 2>/dev/null"));
    }

    #[test]
    fn inline_version_probe_does_not_arm_typed_completion() {
        assert_eq!(
            classify_shell_interaction("python3 -c \"import numpy; print(numpy.__version__)\""),
            ToolInteractionKind::Generic
        );
        assert_eq!(
            classify_shell_interaction("python3 -c \"import numpy; assert numpy.__version__\""),
            ToolInteractionKind::Validation
        );
    }
}
