# Blend: Model Routing Reading List

Recorded: 2026-09-29. This note preserves the four papers discussed during
initial exploration of Blend, a proposed event- and state-aware model routing
feature for Structure. It is a reading list, not a completed literature review
or an implementation commitment.

## Proposed feature

Select the model for the next model call within a single agent run using the
triggering event, task stage, recent tool results, failure history, capability
requirements, and remaining budget. Make the decision at model-call boundaries,
after relevant tool results have been collected, rather than on every emitted
event or streaming fragment.

## Priority reading

### 1. EvoRoute: Experience-Driven Self-Routing LLM Agent Systems

- Authors: Guibin Zhang, Haiyang Yu, Kaiming Yang, Bingli Wu, Fei Huang,
  Yongbin Li, and Shuicheng Yan.
- Publication: ACL 2026, Volume 1: Long Papers, pages 38213–38225.
- [Paper and abstract](https://aclanthology.org/2026.acl-long.1771/)
- [DOI](https://doi.org/10.18653/v1/2026.acl-long.1771)
- Idea: use accumulated experience to select model backbones at each agent
  step, balancing performance, cost, and latency; refine selection through
  environment feedback.
- Relevance: direct overlap with dynamic model selection within a run. This is
  a priority comparison before claiming novelty for Blend.
- Reading question: how are execution state, experience retrieval, and
  downstream task outcomes represented in the routing policy?
- Evidence: abstract and publisher metadata inspected; title, authors, and
  publication year matched through Crossref. Experiments were not reproduced.

### 2. AgentRouter: Heterogeneous Model Routing for Cost-Optimal Multi-Step Agentic Workflows

- Authors: Rudrendu Kumar Paul and Sourav Nandy.
- First submitted: 2026-09-19; arXiv:2609.22951.
- [Paper and abstract](https://arxiv.org/abs/2609.22951)
- Idea: formulate step-level model routing over agent trajectories and use a
  lightweight classifier to assign each step to one of four model tiers.
- Relevance: direct overlap with choosing the model for the next operation;
  explicitly considers differences in subtask complexity within a trajectory.
- Reading question: which routing-time features are available without knowing
  the future outcome, and how are trajectory-level quality dependencies tested?
- Evidence: arXiv abstract and metadata inspected. The record reports acceptance
  at the AgenticUQ Workshop, ICML 2026; that venue claim was not independently
  verified. OpenAlex metadata verification was rate-limited (HTTP 429).
- Disambiguation: this is not the separate paper titled "AgentRouter: A
  Knowledge-Graph-Guided LLM Router for Collaborative Multi-Agent Question
  Answering" (arXiv:2510.05445).

### 3. RouteLLM: Learning to Route LLMs with Preference Data

- Authors: Isaac Ong, Amjad Almahairi, Vincent Wu, Wei-Lin Chiang, Tianhao Wu,
  Joseph E. Gonzalez, M Waleed Kadous, and Ion Stoica.
- First submitted: 2024-06-26; inspected revision: 2025-02-23, v4.
- [Paper and abstract](https://arxiv.org/abs/2406.18665)
- [Official implementation](https://github.com/lm-sys/RouteLLM)
- Idea: train efficient routers using preference data to choose between a
  stronger and a weaker model, trading off response quality and cost.
- Relevance: foundational routing and evaluation reference. Its request-level
  formulation does not by itself establish a policy for event-driven agent
  trajectories.
- Reading question: how should preference labels and evaluation change when
  the selected model affects subsequent tool actions and later steps?
- Evidence: arXiv abstract, revision metadata, and official repository inspected;
  no independent metadata-service cross-check or experiment reproduction in
  this initial pass. The linked citation is the arXiv version.

### 4. FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance

- Authors: Lingjiao Chen, Matei Zaharia, and James Zou.
- First submitted: 2023-05-09; arXiv:2305.05176.
- [Paper and abstract](https://arxiv.org/abs/2305.05176)
- Idea: learn combinations of models in a cascade to reduce cost while
  preserving or improving answer quality.
- Relevance: foundational reference for escalation after an initial response.
  Distinguish choosing a model before a call from deciding whether to escalate
  after inspecting its output.
- Reading question: what reliable acceptance signal can replace a generic
  answer-quality score for tool use, validation failures, and recovery?
- Evidence: arXiv abstract and metadata inspected; no independent
  metadata-service cross-check or experiment reproduction in this initial pass.
  The linked citation is the arXiv version.

## Engineering references from the same discussion

- [LangChain agent middleware](https://www.langchain.com/blog/agent-middleware):
  model-call hooks and dynamic changes to models, context, and tools.
- [RouteLLM](https://github.com/lm-sys/RouteLLM): router serving and evaluation.
- [LiteLLM Router](https://docs.litellm.ai/docs/routing): deployment routing,
  cost/latency strategies, and failover. Agent event semantics would still need
  to come from Structure's runtime.

## Questions to resolve before implementation

- Start with an explainable rule policy and record each decision, its reason,
  policy version, and model step in typed events.
- Keep orchestration in `arabica-runtime`, provider invocation behind
  `ModelProvider`, protocol evidence in `arabica-protocol`, and event sequencing
  in `arabica-session`.
- Check context continuity across providers, including tool-call/result pairing
  and provider-specific state that cannot be transferred directly.
- Compare fixed strong-model, fixed inexpensive-model, fixed stage assignment,
  and event/state-aware routing baselines.
- Measure end-to-end success, total cost per successful task, completion
  latency, and retries, including routing and model-switching overhead.

These are proposed design and evaluation directions, not findings established
by the cited papers or measured results for Structure.

## Search provenance and limits

The initial search on 2026-09-29 included:

- Google Scholar through browser control: `LLM agent step level model routing`,
  relevance order, first 10 results inspected.
- Official arXiv API: `all:agent AND all:routing`, newest first, first six
  results; and `ti:AgentRouter OR ti:RouteLLM OR ti:FrugalGPT`, up to five results.
- Web discovery queries included `RouteLLM FrugalGPT model routing`,
  `LangChain dynamic model selection middleware agent`, and Chinese equivalents
  for large-language-model agent dynamic model routing.
- Original arXiv records, ACL Anthology, and official engineering documentation
  were used to verify selected records beyond their titles.
- CNKI browser access failed with a certificate-name error; its coverage is
  incomplete. Crossref matched EvoRoute; OpenAlex rate-limited AgentRouter.

This bounded, primarily abstract-level search establishes relevant prior work,
but does not establish global originality or validate reported performance.
