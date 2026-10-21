use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fmt::{Display, Formatter};

use arabica_model::RuntimeUsage;
use arabica_protocol::{Event, EventEnvelope, RunId};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AccountingError(pub String);

impl Display for AccountingError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for AccountingError {}

fn default_true() -> bool {
    true
}

/// A pricing rule for a specific model or model family.
/// Rates are specified per million tokens (standard LLM pricing unit).
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ModelPriceRule {
    /// Cost per 1,000,000 uncached input tokens in the card's currency.
    pub input_cost_per_million: f64,
    /// Cost per 1,000,000 output tokens in the card's currency.
    pub output_cost_per_million: f64,
    /// Cost per 1,000,000 cached input read tokens (prompt cache hits).
    /// If None, defaults to `input_cost_per_million`.
    #[serde(default)]
    pub cache_read_cost_per_million: Option<f64>,
    /// Cost per 1,000,000 cache creation / write tokens.
    /// If None, defaults to 0.0 (no additional cache creation premium).
    #[serde(default)]
    pub cache_write_cost_per_million: Option<f64>,
    /// Cost per 1,000,000 reasoning output tokens.
    /// If None, reasoning tokens are billed at `output_cost_per_million`.
    #[serde(default)]
    pub reasoning_output_cost_per_million: Option<f64>,
    /// Whether failed calls with reported token usage remain billable.
    /// Standard provider APIs bill for tokens generated prior to failure.
    #[serde(default = "default_true")]
    pub billable_on_failure: bool,
}

impl ModelPriceRule {
    pub fn new(input_cost_per_million: f64, output_cost_per_million: f64) -> Self {
        Self {
            input_cost_per_million,
            output_cost_per_million,
            cache_read_cost_per_million: None,
            cache_write_cost_per_million: None,
            reasoning_output_cost_per_million: None,
            billable_on_failure: true,
        }
    }

    pub fn with_cache_rates(mut self, cache_read: Option<f64>, cache_write: Option<f64>) -> Self {
        self.cache_read_cost_per_million = cache_read;
        self.cache_write_cost_per_million = cache_write;
        self
    }

    pub fn with_reasoning_rate(mut self, reasoning_rate: Option<f64>) -> Self {
        self.reasoning_output_cost_per_million = reasoning_rate;
        self
    }

    pub fn with_billable_on_failure(mut self, billable: bool) -> Self {
        self.billable_on_failure = billable;
        self
    }

    /// Calculate detailed cost breakdown for a given usage and call success state.
    pub fn calculate_call_cost(
        &self,
        usage: &RuntimeUsage,
        provider_succeeded: bool,
    ) -> CallCostBreakdown {
        if !provider_succeeded && !self.billable_on_failure {
            return CallCostBreakdown {
                uncached_input_tokens: usage.input_tokens.saturating_sub(usage.cached_input_tokens),
                uncached_input_cost: 0.0,
                cached_read_tokens: usage.cached_input_tokens,
                cache_read_cost: 0.0,
                cache_write_tokens: usage.cache_creation_input_tokens,
                cache_write_cost: 0.0,
                reasoning_output_tokens: usage.reasoning_output_tokens,
                reasoning_cost: 0.0,
                standard_output_tokens: usage
                    .output_tokens
                    .saturating_sub(usage.reasoning_output_tokens),
                standard_output_cost: 0.0,
                total_cost: 0.0,
            };
        }

        let cached_read_tokens = usage.cached_input_tokens;
        let uncached_input_tokens = usage.input_tokens.saturating_sub(cached_read_tokens);
        let cache_write_tokens = usage.cache_creation_input_tokens;

        let reasoning_output_tokens = usage.reasoning_output_tokens;
        let standard_output_tokens = usage.output_tokens.saturating_sub(reasoning_output_tokens);

        let uncached_input_cost =
            (uncached_input_tokens as f64 / 1_000_000.0) * self.input_cost_per_million;

        let cache_read_rate = self
            .cache_read_cost_per_million
            .unwrap_or(self.input_cost_per_million);
        let cache_read_cost = (cached_read_tokens as f64 / 1_000_000.0) * cache_read_rate;

        let cache_write_rate = self.cache_write_cost_per_million.unwrap_or(0.0);
        let cache_write_cost = (cache_write_tokens as f64 / 1_000_000.0) * cache_write_rate;

        let reasoning_rate = self
            .reasoning_output_cost_per_million
            .unwrap_or(self.output_cost_per_million);
        let reasoning_cost = (reasoning_output_tokens as f64 / 1_000_000.0) * reasoning_rate;

        let standard_output_cost =
            (standard_output_tokens as f64 / 1_000_000.0) * self.output_cost_per_million;

        let total_cost = uncached_input_cost
            + cache_read_cost
            + cache_write_cost
            + reasoning_cost
            + standard_output_cost;

        CallCostBreakdown {
            uncached_input_tokens,
            uncached_input_cost,
            cached_read_tokens,
            cache_read_cost,
            cache_write_tokens,
            cache_write_cost,
            reasoning_output_tokens,
            reasoning_cost,
            standard_output_tokens,
            standard_output_cost,
            total_cost,
        }
    }
}

/// Versioned price card mapping models/aliases to billing rules in a given currency.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct PriceCard {
    pub version: String,
    pub currency: String,
    pub models: BTreeMap<String, ModelPriceRule>,
    #[serde(default)]
    pub effective_date: Option<String>,
}

impl PriceCard {
    pub fn new(version: impl Into<String>, currency: impl Into<String>) -> Self {
        Self {
            version: version.into(),
            currency: currency.into(),
            models: BTreeMap::new(),
            effective_date: None,
        }
    }

    pub fn with_model(mut self, model_or_alias: impl Into<String>, rule: ModelPriceRule) -> Self {
        self.models.insert(model_or_alias.into(), rule);
        self
    }

    pub fn with_effective_date(mut self, date: impl Into<String>) -> Self {
        self.effective_date = Some(date.into());
        self
    }

    /// Lookup pricing rule for a model ID or alias.
    pub fn get_rule(&self, model_or_alias: &str) -> Option<&ModelPriceRule> {
        if let Some(rule) = self.models.get(model_or_alias) {
            return Some(rule);
        }
        // Fallback: match without provider prefix if e.g. "anthropic:claude-3-5" vs "claude-3-5"
        if let Some((_, bare)) = model_or_alias.split_once(':')
            && let Some(rule) = self.models.get(bare)
        {
            return Some(rule);
        }
        None
    }

    pub fn fingerprint(&self) -> String {
        crate::context::fingerprint(&self)
    }
}

/// Fine-grained token and dollar breakdown for a single model call.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CallCostBreakdown {
    pub uncached_input_tokens: u64,
    pub uncached_input_cost: f64,
    pub cached_read_tokens: u64,
    pub cache_read_cost: f64,
    pub cache_write_tokens: u64,
    pub cache_write_cost: f64,
    pub reasoning_output_tokens: u64,
    pub reasoning_cost: f64,
    pub standard_output_tokens: u64,
    pub standard_output_cost: f64,
    pub total_cost: f64,
}

/// Behavioral role of a model invocation.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CallCategory {
    /// Normal primary agent execution turns.
    Main,
    /// Upfront or mid-flight structural plan generation.
    Planning,
    /// Auxiliary calls (summarization, memory compression, tool translation).
    Auxiliary,
    /// Immediate retries after network or transient model error.
    Retry,
    /// Recovery calls triggered by tool errors or health regression.
    Recovery,
    /// External model-judge or evaluator checks (excluded from agent task spend).
    Evaluator,
}

impl CallCategory {
    /// Classify a call based on route reason, decision context, and tags.
    pub fn classify(reason: &str, is_evaluator: bool) -> Self {
        if is_evaluator || reason.contains("evaluator") || reason.contains("judge") {
            Self::Evaluator
        } else if reason.contains("recovery") || reason.contains("fallback") {
            Self::Recovery
        } else if reason.contains("retry") {
            Self::Retry
        } else if reason.contains("plan") {
            Self::Planning
        } else if reason.contains("aux")
            || reason.contains("summary")
            || reason.contains("compress")
        {
            Self::Auxiliary
        } else {
            Self::Main
        }
    }
}

/// Detailed accounting record for one model call.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CallCostRecord {
    pub model_step: usize,
    pub decision_id: String,
    pub model: String,
    pub category: CallCategory,
    pub elapsed_ms: u64,
    pub provider_succeeded: bool,
    pub billable: bool,
    pub usage: Option<RuntimeUsage>,
    pub breakdown: Option<CallCostBreakdown>,
    pub total_cost: Option<f64>,
}

/// Verification metrics regarding billing completeness.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct AccountingCoverage {
    pub total_calls: usize,
    pub billable_calls: usize,
    pub calls_with_usage: usize,
    pub calls_with_pricing: usize,
    pub unpriced_models: BTreeSet<String>,
    pub missing_usage_calls: usize,
    pub replayed_duplicate_calls_suppressed: usize,
    /// True only if all billable calls have both usage and valid price rules.
    pub is_complete: bool,
}

/// Bounded time metrics separating active work from user/permission approval wait.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct TimeAccounting {
    pub queue_ms: Option<u64>,
    pub wall_ms: Option<u64>,
    pub approval_wait_ms: Option<u64>,
    pub active_ms: Option<u64>,
    pub first_response_ms: Option<u64>,
    pub model_call_ms_total: u64,
    pub runner_ms_total: Option<u64>,
}

/// Complete, reproducible run cost and time accounting report.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RunCostAccounting {
    pub price_card_version: String,
    pub currency: String,
    /// Agent cost for task execution (Main + Planning + Auxiliary + Retry + Recovery).
    /// None if coverage is incomplete (unpriced models or missing usage).
    pub total_agent_cost: Option<f64>,
    /// Separate costs incurred by model evaluators / judges.
    pub total_evaluator_cost: Option<f64>,
    /// Grand total (agent + evaluator), available when coverage is complete.
    pub total_cost: Option<f64>,
    pub call_costs: Vec<CallCostRecord>,
    pub costs_by_category: BTreeMap<CallCategory, f64>,
    pub costs_by_model: BTreeMap<String, f64>,
    pub total_input_tokens: u64,
    pub total_output_tokens: u64,
    pub total_cached_tokens: u64,
    pub total_cache_creation_tokens: u64,
    pub total_reasoning_tokens: u64,
    pub time: TimeAccounting,
    pub coverage: AccountingCoverage,
}

fn interval_union(intervals: &mut [(u64, u64)]) -> u64 {
    intervals.sort_unstable();
    let mut total = 0_u64;
    let mut previous = None;
    for &(start, end) in intervals.iter() {
        match previous {
            Some((left, right)) if start <= right => previous = Some((left, right.max(end))),
            Some((left, right)) => {
                total = total.saturating_add(right - left);
                previous = Some((start, end));
            }
            None => previous = Some((start, end)),
        }
    }
    if let Some((left, right)) = previous {
        total = total.saturating_add(right - left);
    }
    total
}

/// Compute complete cost and time accounting from an event history and price card.
/// Duplicate/replayed observations are identified and suppressed from billing.
pub fn account_run_cost_and_time(
    history: &[EventEnvelope],
    target_run_id: Option<&RunId>,
    price_card: &PriceCard,
) -> Result<RunCostAccounting, AccountingError> {
    // 1. Filter events for the target run (or single run in history)
    let filtered_events: Vec<&EventEnvelope> = match target_run_id {
        Some(run_id) => history
            .iter()
            .filter(|e| e.run_id.as_ref() == Some(run_id))
            .collect(),
        None => {
            // Find unique run id if exists
            let run_ids: BTreeSet<&RunId> =
                history.iter().filter_map(|e| e.run_id.as_ref()).collect();
            if run_ids.len() > 1 {
                return Err(AccountingError(
                    "multiple runs present in history; specify target_run_id".into(),
                ));
            }
            history.iter().collect()
        }
    };

    let mut scheduled = None;
    let mut started = None;
    let mut terminal_ms = None;
    let mut first_response = None;
    let mut waits = BTreeMap::<String, u64>::new();
    let mut wait_intervals = Vec::<(u64, u64)>::new();
    let mut runner_ms_total = 0_u64;
    let mut has_runner_ms = false;

    // Track model steps to routing metadata
    struct RouteInfo {
        model: String,
        category: CallCategory,
    }
    let mut routes = BTreeMap::<usize, RouteInfo>::new();

    // Call tracking and deduplication
    let mut seen_decision_ids = HashSet::new();
    let mut replayed_duplicate_calls_suppressed = 0_usize;

    let mut call_records = Vec::new();
    let mut model_call_ms_total = 0_u64;

    let mut total_input_tokens = 0_u64;
    let mut total_output_tokens = 0_u64;
    let mut total_cached_tokens = 0_u64;
    let mut total_cache_creation_tokens = 0_u64;
    let mut total_reasoning_tokens = 0_u64;

    let mut unpriced_models = BTreeSet::new();
    let mut missing_usage_calls = 0_usize;
    let mut calls_with_usage = 0_usize;
    let mut calls_with_pricing = 0_usize;
    let mut billable_calls = 0_usize;

    for envelope in &filtered_events {
        match &envelope.event {
            Event::RunScheduled => {
                scheduled.get_or_insert(envelope.occurred_at_ms);
            }
            Event::RunStarted => {
                started.get_or_insert(envelope.occurred_at_ms);
            }
            Event::RunCompleted { .. } | Event::RunFailed { .. } | Event::RunCancelled => {
                terminal_ms.get_or_insert(envelope.occurred_at_ms);
            }
            Event::ModelResponseItem { .. } => {
                first_response.get_or_insert(envelope.occurred_at_ms);
            }
            Event::ToolCallPermissionRequested { call_id } => {
                waits.insert(call_id.clone(), envelope.occurred_at_ms);
            }
            Event::ToolCallPermissionResolved { call_id, .. } => {
                if let Some(start) = waits.remove(call_id)
                    && envelope.occurred_at_ms >= start
                {
                    wait_intervals.push((start, envelope.occurred_at_ms));
                }
            }
            Event::ToolExecutionObserved { elapsed_ms, .. } => {
                has_runner_ms = true;
                runner_ms_total = runner_ms_total.saturating_add(*elapsed_ms);
            }
            Event::ModelRouteSelected {
                model_step,
                model_alias,
                reason,
                ..
            } => {
                let model = model_alias.clone().unwrap_or_else(|| "default".to_string());
                let category = CallCategory::classify(reason, false);
                routes.insert(*model_step, RouteInfo { model, category });
            }
            Event::ModelRequestPrepared {
                model_step,
                request,
            } => {
                // If model was not established by RouteSelected, take from request
                routes.entry(*model_step).or_insert_with(|| RouteInfo {
                    model: request.model.clone(),
                    category: CallCategory::Main,
                });
            }
            Event::ModelCallObserved {
                model_step,
                decision_id,
                elapsed_ms,
                provider_succeeded,
                usage,
                ..
            } => {
                // Duplicate suppression: if this decision_id was already observed, do not double count
                if !seen_decision_ids.insert(decision_id.clone()) {
                    replayed_duplicate_calls_suppressed += 1;
                    continue;
                }

                model_call_ms_total = model_call_ms_total.saturating_add(*elapsed_ms);

                let route_info = routes.get(model_step);
                let model = route_info
                    .map(|r| r.model.clone())
                    .unwrap_or_else(|| "unknown".to_string());
                let category = route_info.map(|r| r.category).unwrap_or(CallCategory::Main);

                let maybe_rule = price_card.get_rule(&model);
                if maybe_rule.is_none() {
                    unpriced_models.insert(model.clone());
                } else {
                    calls_with_pricing += 1;
                }

                if let Some(u) = usage {
                    calls_with_usage += 1;
                    total_input_tokens = total_input_tokens.saturating_add(u.input_tokens);
                    total_output_tokens = total_output_tokens.saturating_add(u.output_tokens);
                    total_cached_tokens = total_cached_tokens.saturating_add(u.cached_input_tokens);
                    total_cache_creation_tokens =
                        total_cache_creation_tokens.saturating_add(u.cache_creation_input_tokens);
                    total_reasoning_tokens =
                        total_reasoning_tokens.saturating_add(u.reasoning_output_tokens);
                } else {
                    missing_usage_calls += 1;
                }

                let (billable, breakdown, cost) = match (maybe_rule, usage) {
                    (Some(rule), Some(u)) => {
                        let is_billable = *provider_succeeded || rule.billable_on_failure;
                        if is_billable {
                            billable_calls += 1;
                            let bd = rule.calculate_call_cost(u, *provider_succeeded);
                            let c = bd.total_cost;
                            (is_billable, Some(bd), Some(c))
                        } else {
                            let bd = rule.calculate_call_cost(u, *provider_succeeded);
                            (false, Some(bd), Some(0.0))
                        }
                    }
                    _ => (false, None, None),
                };

                call_records.push(CallCostRecord {
                    model_step: *model_step,
                    decision_id: decision_id.clone(),
                    model,
                    category,
                    elapsed_ms: *elapsed_ms,
                    provider_succeeded: *provider_succeeded,
                    billable,
                    usage: usage.clone(),
                    breakdown,
                    total_cost: cost,
                });
            }
            _ => {}
        }
    }

    // Compute timings
    let total_wait_ms = interval_union(&mut wait_intervals);
    let queue_ms = match (scheduled, started) {
        (Some(sch), Some(sta)) if sta >= sch => Some(sta - sch),
        _ => None,
    };
    let wall_ms = match (started, terminal_ms) {
        (Some(sta), Some(term)) if term >= sta => Some(term - sta),
        _ => None,
    };
    let active_ms = wall_ms.map(|wall| wall.saturating_sub(total_wait_ms));
    let first_resp_ms = match (started, first_response) {
        (Some(sta), Some(first)) if first >= sta => Some(first - sta),
        _ => None,
    };

    let time_accounting = TimeAccounting {
        queue_ms,
        wall_ms,
        approval_wait_ms: Some(total_wait_ms),
        active_ms,
        first_response_ms: first_resp_ms,
        model_call_ms_total,
        runner_ms_total: if has_runner_ms {
            Some(runner_ms_total)
        } else {
            None
        },
    };

    let total_calls = call_records.len();
    let is_complete = unpriced_models.is_empty() && missing_usage_calls == 0;

    let mut costs_by_category = BTreeMap::new();
    let mut costs_by_model = BTreeMap::new();
    let mut agent_cost_sum = 0.0;
    let mut evaluator_cost_sum = 0.0;

    for record in &call_records {
        if let Some(cost) = record.total_cost {
            *costs_by_category.entry(record.category).or_insert(0.0) += cost;
            *costs_by_model.entry(record.model.clone()).or_insert(0.0) += cost;
            if record.category == CallCategory::Evaluator {
                evaluator_cost_sum += cost;
            } else {
                agent_cost_sum += cost;
            }
        }
    }

    let (total_agent_cost, total_evaluator_cost, total_cost) = if is_complete {
        (
            Some(agent_cost_sum),
            Some(evaluator_cost_sum),
            Some(agent_cost_sum + evaluator_cost_sum),
        )
    } else {
        (None, None, None)
    };

    let coverage = AccountingCoverage {
        total_calls,
        billable_calls,
        calls_with_usage,
        calls_with_pricing,
        unpriced_models,
        missing_usage_calls,
        replayed_duplicate_calls_suppressed,
        is_complete,
    };

    Ok(RunCostAccounting {
        price_card_version: price_card.version.clone(),
        currency: price_card.currency.clone(),
        total_agent_cost,
        total_evaluator_cost,
        total_cost,
        call_costs: call_records,
        costs_by_category,
        costs_by_model,
        total_input_tokens,
        total_output_tokens,
        total_cached_tokens,
        total_cache_creation_tokens,
        total_reasoning_tokens,
        time: time_accounting,
        coverage,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use arabica_model::RuntimeUsage;
    use arabica_protocol::{
        CommandId, Event, EventEnvelope, EventId, EventMetadata, ModelCallOutcome, RunId,
        SessionId, ToolPermissionOutcome, ToolPermissionScope, ToolPermissionSource, WorkspaceId,
    };

    fn test_envelope(sequence: u64, occurred_at_ms: u64, event: Event) -> EventEnvelope {
        EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("ev-{sequence}")),
                command_id: CommandId::new("cmd-test"),
                workspace_id: WorkspaceId::new("ws-test"),
                session_id: SessionId::new("sess-test"),
                run_id: Some(RunId::new("run-1")),
                sequence,
                occurred_at_ms,
            },
            event,
        )
    }

    fn sample_price_card() -> PriceCard {
        PriceCard::new("2026-10-pricing", "USD")
            .with_model(
                "claude-3-5-sonnet",
                ModelPriceRule::new(3.0, 15.0)
                    .with_cache_rates(Some(0.30), Some(3.75))
                    .with_reasoning_rate(Some(15.0)),
            )
            .with_model(
                "gpt-4o-mini",
                ModelPriceRule::new(0.15, 0.60).with_cache_rates(Some(0.075), None),
            )
            .with_model(
                "claude-3-haiku",
                ModelPriceRule::new(0.25, 1.25).with_billable_on_failure(false),
            )
    }

    #[test]
    fn pricing_formula_accounts_for_cache_and_reasoning() {
        let card = sample_price_card();
        let rule = card.get_rule("claude-3-5-sonnet").unwrap();

        let usage = RuntimeUsage {
            input_tokens: 1_000_000,
            output_tokens: 200_000,
            cached_input_tokens: 800_000,
            cache_creation_input_tokens: 100_000,
            reasoning_output_tokens: 50_000,
        };

        let breakdown = rule.calculate_call_cost(&usage, true);

        // uncached input = 200,000 / 1M * 3.0 = 0.60
        assert!((breakdown.uncached_input_cost - 0.60).abs() < 1e-9);
        // cached read = 800,000 / 1M * 0.30 = 0.24
        assert!((breakdown.cache_read_cost - 0.24).abs() < 1e-9);
        // cache write = 100,000 / 1M * 3.75 = 0.375
        assert!((breakdown.cache_write_cost - 0.375).abs() < 1e-9);
        // reasoning = 50,000 / 1M * 15.0 = 0.75
        assert!((breakdown.reasoning_cost - 0.75).abs() < 1e-9);
        // standard output = 150,000 / 1M * 15.0 = 2.25
        assert!((breakdown.standard_output_cost - 2.25).abs() < 1e-9);

        // total = 0.60 + 0.24 + 0.375 + 0.75 + 2.25 = 4.215
        assert!((breakdown.total_cost - 4.215).abs() < 1e-9);
    }

    #[test]
    fn failed_call_respects_billable_on_failure_setting() {
        let card = sample_price_card();

        // Sonnet bills on failure
        let sonnet_rule = card.get_rule("claude-3-5-sonnet").unwrap();
        let usage = RuntimeUsage {
            input_tokens: 100_000,
            output_tokens: 10_000,
            cached_input_tokens: 0,
            cache_creation_input_tokens: 0,
            reasoning_output_tokens: 0,
        };
        let bd_sonnet = sonnet_rule.calculate_call_cost(&usage, false);
        assert!(bd_sonnet.total_cost > 0.0);

        // Haiku configured with billable_on_failure = false
        let haiku_rule = card.get_rule("claude-3-haiku").unwrap();
        let bd_haiku = haiku_rule.calculate_call_cost(&usage, false);
        assert_eq!(bd_haiku.total_cost, 0.0);
    }

    #[test]
    fn complete_run_accounting_with_categories_and_timing() {
        let card = sample_price_card();

        let events = vec![
            test_envelope(1, 1000, Event::RunScheduled),
            test_envelope(2, 1050, Event::RunStarted),
            // Call 1: Planning call
            test_envelope(
                3,
                1100,
                Event::ModelRouteSelected {
                    model_step: 0,
                    decision_id: "dec-1".into(),
                    policy_id: "blend-1".into(),
                    policy_version: 1,
                    policy_fingerprint: "fp1".into(),
                    model_registry_snapshot: "snap1".into(),
                    model_alias: Some("gpt-4o-mini".into()),
                    reason: "initial task planning".into(),
                },
            ),
            test_envelope(
                4,
                1200,
                Event::ModelCallObserved {
                    model_step: 0,
                    decision_id: "dec-1".into(),
                    elapsed_ms: 100,
                    provider_succeeded: true,
                    outcome: ModelCallOutcome::Succeeded,
                    usage: Some(RuntimeUsage {
                        input_tokens: 10_000,
                        output_tokens: 2_000,
                        cached_input_tokens: 0,
                        cache_creation_input_tokens: 0,
                        reasoning_output_tokens: 0,
                    }),
                },
            ),
            // Call 2: Main call with Sonnet
            test_envelope(
                5,
                1300,
                Event::ModelRouteSelected {
                    model_step: 1,
                    decision_id: "dec-2".into(),
                    policy_id: "blend-1".into(),
                    policy_version: 1,
                    policy_fingerprint: "fp1".into(),
                    model_registry_snapshot: "snap1".into(),
                    model_alias: Some("claude-3-5-sonnet".into()),
                    reason: "default execution".into(),
                },
            ),
            test_envelope(
                6,
                1500,
                Event::ModelCallObserved {
                    model_step: 1,
                    decision_id: "dec-2".into(),
                    elapsed_ms: 200,
                    provider_succeeded: true,
                    outcome: ModelCallOutcome::Succeeded,
                    usage: Some(RuntimeUsage {
                        input_tokens: 50_000,
                        output_tokens: 5_000,
                        cached_input_tokens: 40_000,
                        cache_creation_input_tokens: 0,
                        reasoning_output_tokens: 0,
                    }),
                },
            ),
            // Tool call with permission wait
            test_envelope(
                7,
                1510,
                Event::ToolCallPermissionRequested {
                    call_id: "call-1".into(),
                },
            ),
            test_envelope(
                8,
                1610,
                Event::ToolCallPermissionResolved {
                    call_id: "call-1".into(),
                    outcome: ToolPermissionOutcome::Allowed,
                    scope: ToolPermissionScope::Once,
                    source: ToolPermissionSource::User,
                },
            ),
            test_envelope(
                9,
                1620,
                Event::ToolExecutionObserved {
                    call_id: "call-1".into(),
                    elapsed_ms: 80,
                    outcome: ModelCallOutcome::Succeeded,
                },
            ),
            test_envelope(
                10,
                2000,
                Event::RunCompleted {
                    output: Some("done".into()),
                },
            ),
        ];

        let accounting = account_run_cost_and_time(&events, None, &card).unwrap();

        // Coverage assertions
        assert!(accounting.coverage.is_complete);
        assert_eq!(accounting.coverage.total_calls, 2);
        assert_eq!(accounting.coverage.missing_usage_calls, 0);
        assert!(accounting.coverage.unpriced_models.is_empty());

        // Category breakdown
        assert_eq!(accounting.call_costs[0].category, CallCategory::Planning);
        assert_eq!(accounting.call_costs[1].category, CallCategory::Main);

        // Time accounting
        // wall = 2000 - 1050 = 950ms
        assert_eq!(accounting.time.wall_ms, Some(950));
        // approval wait = 1610 - 1510 = 100ms
        assert_eq!(accounting.time.approval_wait_ms, Some(100));
        // active = 950 - 100 = 850ms
        assert_eq!(accounting.time.active_ms, Some(850));
        assert_eq!(accounting.time.runner_ms_total, Some(80));

        // Cost assertions
        assert!(accounting.total_agent_cost.is_some());
        let agent_cost = accounting.total_agent_cost.unwrap();
        assert!(agent_cost > 0.0);
        assert_eq!(accounting.total_evaluator_cost, Some(0.0));
        assert_eq!(accounting.total_cost, Some(agent_cost));
    }

    #[test]
    fn replayed_duplicate_calls_are_suppressed() {
        let card = sample_price_card();

        let events = vec![
            test_envelope(1, 100, Event::RunStarted),
            test_envelope(
                2,
                200,
                Event::ModelRouteSelected {
                    model_step: 0,
                    decision_id: "dec-dup".into(),
                    policy_id: "p1".into(),
                    policy_version: 1,
                    policy_fingerprint: "fp".into(),
                    model_registry_snapshot: "s".into(),
                    model_alias: Some("gpt-4o-mini".into()),
                    reason: "main".into(),
                },
            ),
            test_envelope(
                3,
                300,
                Event::ModelCallObserved {
                    model_step: 0,
                    decision_id: "dec-dup".into(),
                    elapsed_ms: 50,
                    provider_succeeded: true,
                    outcome: ModelCallOutcome::Succeeded,
                    usage: Some(RuntimeUsage {
                        input_tokens: 1_000,
                        output_tokens: 100,
                        cached_input_tokens: 0,
                        cache_creation_input_tokens: 0,
                        reasoning_output_tokens: 0,
                    }),
                },
            ),
            // Duplicate replayed observation with same decision_id
            test_envelope(
                4,
                350,
                Event::ModelCallObserved {
                    model_step: 0,
                    decision_id: "dec-dup".into(),
                    elapsed_ms: 50,
                    provider_succeeded: true,
                    outcome: ModelCallOutcome::Succeeded,
                    usage: Some(RuntimeUsage {
                        input_tokens: 1_000,
                        output_tokens: 100,
                        cached_input_tokens: 0,
                        cache_creation_input_tokens: 0,
                        reasoning_output_tokens: 0,
                    }),
                },
            ),
            test_envelope(5, 400, Event::RunCompleted { output: None }),
        ];

        let accounting = account_run_cost_and_time(&events, None, &card).unwrap();
        assert_eq!(accounting.coverage.total_calls, 1);
        assert_eq!(accounting.coverage.replayed_duplicate_calls_suppressed, 1);
    }

    #[test]
    fn unpriced_model_or_missing_usage_marks_coverage_incomplete() {
        let card = sample_price_card();

        // 1. Unpriced model
        let events_unpriced = vec![
            test_envelope(1, 100, Event::RunStarted),
            test_envelope(
                2,
                200,
                Event::ModelRouteSelected {
                    model_step: 0,
                    decision_id: "dec-unknown".into(),
                    policy_id: "p".into(),
                    policy_version: 1,
                    policy_fingerprint: "f".into(),
                    model_registry_snapshot: "s".into(),
                    model_alias: Some("unregistered-super-model".into()),
                    reason: "main".into(),
                },
            ),
            test_envelope(
                3,
                300,
                Event::ModelCallObserved {
                    model_step: 0,
                    decision_id: "dec-unknown".into(),
                    elapsed_ms: 50,
                    provider_succeeded: true,
                    outcome: ModelCallOutcome::Succeeded,
                    usage: Some(RuntimeUsage::default()),
                },
            ),
            test_envelope(4, 400, Event::RunCompleted { output: None }),
        ];

        let acc_unpriced = account_run_cost_and_time(&events_unpriced, None, &card).unwrap();
        assert!(!acc_unpriced.coverage.is_complete);
        assert!(
            acc_unpriced
                .coverage
                .unpriced_models
                .contains("unregistered-super-model")
        );
        assert_eq!(acc_unpriced.total_cost, None);

        // 2. Missing usage
        let events_missing_usage = vec![
            test_envelope(1, 100, Event::RunStarted),
            test_envelope(
                2,
                200,
                Event::ModelRouteSelected {
                    model_step: 0,
                    decision_id: "dec-no-usage".into(),
                    policy_id: "p".into(),
                    policy_version: 1,
                    policy_fingerprint: "f".into(),
                    model_registry_snapshot: "s".into(),
                    model_alias: Some("gpt-4o-mini".into()),
                    reason: "main".into(),
                },
            ),
            test_envelope(
                3,
                300,
                Event::ModelCallObserved {
                    model_step: 0,
                    decision_id: "dec-no-usage".into(),
                    elapsed_ms: 50,
                    provider_succeeded: true,
                    outcome: ModelCallOutcome::Succeeded,
                    usage: None,
                },
            ),
            test_envelope(4, 400, Event::RunCompleted { output: None }),
        ];

        let acc_missing = account_run_cost_and_time(&events_missing_usage, None, &card).unwrap();
        assert!(!acc_missing.coverage.is_complete);
        assert_eq!(acc_missing.coverage.missing_usage_calls, 1);
        assert_eq!(acc_missing.total_cost, None);
    }

    #[test]
    fn evaluator_calls_are_segregated_from_agent_spend() {
        let card = sample_price_card();

        let events = vec![
            test_envelope(1, 100, Event::RunStarted),
            // Agent call
            test_envelope(
                2,
                200,
                Event::ModelRouteSelected {
                    model_step: 0,
                    decision_id: "dec-agent".into(),
                    policy_id: "p".into(),
                    policy_version: 1,
                    policy_fingerprint: "f".into(),
                    model_registry_snapshot: "s".into(),
                    model_alias: Some("gpt-4o-mini".into()),
                    reason: "main".into(),
                },
            ),
            test_envelope(
                3,
                300,
                Event::ModelCallObserved {
                    model_step: 0,
                    decision_id: "dec-agent".into(),
                    elapsed_ms: 50,
                    provider_succeeded: true,
                    outcome: ModelCallOutcome::Succeeded,
                    usage: Some(RuntimeUsage {
                        input_tokens: 1_000_000,
                        output_tokens: 1_000_000,
                        cached_input_tokens: 0,
                        cache_creation_input_tokens: 0,
                        reasoning_output_tokens: 0,
                    }),
                },
            ),
            // Evaluator judge call
            test_envelope(
                4,
                400,
                Event::ModelRouteSelected {
                    model_step: 1,
                    decision_id: "dec-judge".into(),
                    policy_id: "p".into(),
                    policy_version: 1,
                    policy_fingerprint: "f".into(),
                    model_registry_snapshot: "s".into(),
                    model_alias: Some("claude-3-5-sonnet".into()),
                    reason: "evaluator rubric judge".into(),
                },
            ),
            test_envelope(
                5,
                500,
                Event::ModelCallObserved {
                    model_step: 1,
                    decision_id: "dec-judge".into(),
                    elapsed_ms: 100,
                    provider_succeeded: true,
                    outcome: ModelCallOutcome::Succeeded,
                    usage: Some(RuntimeUsage {
                        input_tokens: 100_000,
                        output_tokens: 1_000,
                        cached_input_tokens: 0,
                        cache_creation_input_tokens: 0,
                        reasoning_output_tokens: 0,
                    }),
                },
            ),
            test_envelope(6, 600, Event::RunCompleted { output: None }),
        ];

        let acc = account_run_cost_and_time(&events, None, &card).unwrap();
        assert!(acc.coverage.is_complete);

        // mini: 1M in * 0.15 + 1M out * 0.60 = 0.75
        let agent_cost = acc.total_agent_cost.unwrap();
        assert!((agent_cost - 0.75).abs() < 1e-9);

        // sonnet: 0.1M in * 3.0 + 0.001M out * 15.0 = 0.30 + 0.015 = 0.315
        let eval_cost = acc.total_evaluator_cost.unwrap();
        assert!((eval_cost - 0.315).abs() < 1e-9);

        // Total = 0.75 + 0.315 = 1.065
        assert!((acc.total_cost.unwrap() - 1.065).abs() < 1e-9);
    }
}
