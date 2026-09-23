# Kubernetes experiment plane

This directory contains a private LongCat API center and an isolated Job template
for frozen Structure long-horizon trials. Apply the base only after a cluster
administrator has replaced the empty credential Secret with a 1Password Secret
Operator integration:

```text
kubectl apply -k deploy/experiments
```

`experiment-job-template.yaml` is deliberately excluded from the base: a
manifest controller must substitute a lowercase `TRIAL_ID`, immutable image
digest, and result URI before applying an individual Job.

The API center is the only workload that receives `LONGCAT_API_KEY`. The base
deployment contains only the non-sensitive placeholder `unconfigured`; replace
it through 1Password or the cluster operator before any model call. It forwards
the native LongCat OpenAI-compatible Chat Completions path
`/openai/v1/chat/completions` and replaces client authorization with
`Authorization: Bearer $LONGCAT_API_KEY`. Trial Jobs receive only the internal
base URL and a non-provider placeholder token. Do not commit a Secret
manifest containing values, use `kubectl create secret` interactively, or pass
keys through manifest environment variables.

The frozen primary experiment sets `STRUCTURE_THINKING=false`; this is
intentional for paired comparability, even though LongCat also supports enabled
thinking.

Terminal-Bench requires a task sandbox. The standard restricted Job template
does not mount `/var/run/docker.sock` and does not run privileged containers.
Use a dedicated sandbox service or isolated, audited worker node before wiring
Harbor execution into this template.

The provider egress policy permits TCP/443 as an initial bootstrap setting.
Replace it with an egress gateway or provider CIDRs before production use.
